"""
Tests for the Playlists app — models, API endpoints, generator, ZIP download.
"""
import io
import os
import tempfile
import zipfile

from django.conf import settings
from django.test import TestCase, override_settings
from django.db.utils import IntegrityError
from rest_framework.test import APIClient
from rest_framework import status

from tracks.models import Track
from .models import Playlist, PlaylistTrack
from .generator import generate_playlist, _knapsack_dp


# ======================================================================
# Generator Tests
# ======================================================================
class GeneratorTests(TestCase):
    """Tests for the priority-first playlist generation algorithm."""

    def setUp(self):
        for i in range(10):
            Track.objects.create(
                file=f'mp3/gen_{i}.mp3',
                original_filename=f'gen_{i}.mp3',
                title=f'Track {i}',
                artist='Test Artist',
                duration=30 + i * 25,  # 30, 55, 80, 105, 130, 155, 180, 205, 230, 255
            )

    def test_generate_empty_queryset(self):
        qs = Track.objects.filter(title='Nonexistent')
        result = generate_playlist(qs, target_seconds=300)
        self.assertEqual(result['track_ids'], [])
        self.assertEqual(result['total_duration'], 0.0)

    def test_generate_within_target(self):
        """All priority tracks fit → they are all included."""
        qs = Track.objects.filter(duration__lte=100)  # 30, 55, 80 → total=165
        result = generate_playlist(qs, target_seconds=300)
        self.assertEqual(len(result['track_ids']), 3)
        self.assertAlmostEqual(result['total_duration'], 165.0, places=1)
        self.assertEqual(result['algorithm'], 'priority_first')

    def test_generate_exceeds_target_uses_dp(self):
        """When priority tracks exceed target, DP selects the best subset."""
        qs = Track.objects.all()  # total = 1425s
        result = generate_playlist(qs, target_seconds=395)
        self.assertGreater(result['total_duration'], 0)
        self.assertLessEqual(result['total_duration'], 395)
        self.assertEqual(result['algorithm'], 'priority_knapsack')

    def test_generate_no_duration(self):
        """Without a target duration, all priority tracks are returned."""
        qs = Track.objects.filter(duration__lte=80)  # 30, 55, 80
        result = generate_playlist(qs, target_seconds=None)
        self.assertEqual(len(result['track_ids']), 3)
        self.assertEqual(result['algorithm'], 'priority_only')

    def test_generate_small_target(self):
        """Target smaller than every track in the pool → empty result."""
        qs = Track.objects.filter(duration__gt=200)  # 205, 230, 255
        result = generate_playlist(qs, target_seconds=100)
        self.assertEqual(result['track_ids'], [])

    def test_generate_relaxation(self):
        """
        When strict DP gives poor coverage (< 60% of target),
        a relaxed target (×1.2) is tried and relaxation flag is set.
        Scenario: two tracks of 400s and 350s, target = 700s.
          strict:  best = 400s (400+350=750 > 700).
          400 < 0.6×700=420 → relaxation triggered.
          relaxed: target=840. 400+350=750 ≤ 840 → selected.
        """
        Track.objects.all().delete()
        Track.objects.create(file='mp3/r1.mp3', original_filename='r1.mp3',
                             title='R1', artist='A', duration=400)
        Track.objects.create(file='mp3/r2.mp3', original_filename='r2.mp3',
                             title='R2', artist='A', duration=350)
        qs = Track.objects.all()
        result = generate_playlist(qs, target_seconds=700)
        self.assertTrue(result['relaxation'])
        self.assertGreater(result['total_duration'], 700)

    def test_generate_with_fallback(self):
        """Priority tracks fill target partially; fallback fills the rest."""
        priority_qs = Track.objects.filter(duration=30)   # 1 track, 30s
        fallback_qs = Track.objects.filter(duration=55)   # 1 track, 55s
        result = generate_playlist(priority_qs, fallback_queryset=fallback_qs,
                                   target_seconds=200)
        # Both should be included (30+55=85 ≤ 200)
        self.assertGreaterEqual(len(result['track_ids']), 1)
        self.assertEqual(result['algorithm'], 'priority_first')

    def test_knapsack_dp_core(self):
        """Test the core DP function directly."""
        tracks = [
            {'id': 'a', 'duration': 30},
            {'id': 'b', 'duration': 55},
            {'id': 'c', 'duration': 80},
        ]
        durations = [300, 550, 800]  # scaled ×10
        result = _knapsack_dp(tracks, durations, 850)
        self.assertEqual(result['best_sum'], 850)
        self.assertIn('a', result['best_ids'])
        self.assertIn('b', result['best_ids'])


# ======================================================================
# Model Tests
# ======================================================================
class PlaylistModelTests(TestCase):

    def setUp(self):
        self.track1 = Track.objects.create(
            file='mp3/pl1.mp3', original_filename='pl1.mp3',
            title='Song A', artist='Artist A', duration=200,
        )
        self.track2 = Track.objects.create(
            file='mp3/pl2.mp3', original_filename='pl2.mp3',
            title='Song B', artist='Artist B', duration=180,
        )
        self.track3 = Track.objects.create(
            file='mp3/pl3.mp3', original_filename='pl3.mp3',
            title='Song C', artist='Artist C', duration=120,
        )

    def test_create_playlist(self):
        pl = Playlist.objects.create(name='My Playlist')
        self.assertEqual(str(pl), 'My Playlist (0 piste(s))')
        self.assertEqual(pl.total_duration, 0.0)

    def test_set_tracks(self):
        pl = Playlist.objects.create(name='Ordered')
        pl.set_tracks([self.track1.id, self.track2.id, self.track3.id])
        self.assertEqual(pl.tracks.count(), 3)
        self.assertEqual(pl.total_duration, 500.0)
        entries = pl.tracks.all()
        self.assertEqual(entries[0].track_id, self.track1.id)
        self.assertEqual(entries[0].position, 0)
        self.assertEqual(entries[2].track_id, self.track3.id)
        self.assertEqual(entries[2].position, 2)

    def test_set_tracks_replaces_existing(self):
        pl = Playlist.objects.create(name='Replace')
        pl.set_tracks([self.track1.id])
        pl.set_tracks([self.track2.id, self.track3.id])
        self.assertEqual(pl.tracks.count(), 2)
        self.assertEqual(pl.total_duration, 300.0)

    def test_unique_position_constraint(self):
        pl = Playlist.objects.create(name='Unique')
        PlaylistTrack.objects.create(playlist=pl, track=self.track1, position=0)
        with self.assertRaises(IntegrityError):
            PlaylistTrack.objects.create(playlist=pl, track=self.track2, position=0)

    def test_unique_track_constraint(self):
        pl = Playlist.objects.create(name='Unique Track')
        PlaylistTrack.objects.create(playlist=pl, track=self.track1, position=0)
        with self.assertRaises(IntegrityError):
            PlaylistTrack.objects.create(playlist=pl, track=self.track1, position=1)

    def test_duration_formatted(self):
        pl = Playlist(total_duration=3661)
        self.assertEqual(pl.duration_formatted(), '1:01:01')
        pl.total_duration = 125
        self.assertEqual(pl.duration_formatted(), '02:05')
        pl.total_duration = 0
        self.assertEqual(pl.duration_formatted(), '00:00')

    def test_recalculate_duration(self):
        pl = Playlist.objects.create(name='Recalc')
        PlaylistTrack.objects.create(playlist=pl, track=self.track1, position=0)
        PlaylistTrack.objects.create(playlist=pl, track=self.track2, position=1)
        pl.recalculate_duration()
        self.assertEqual(pl.total_duration, 380.0)

    def test_cascade_delete(self):
        pl = Playlist.objects.create(name='Cascade')
        PlaylistTrack.objects.create(playlist=pl, track=self.track1, position=0)
        pl_id = pl.id
        pl.delete()
        self.assertEqual(PlaylistTrack.objects.filter(playlist_id=pl_id).count(), 0)


# ======================================================================
# API Tests
# ======================================================================
class PlaylistAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.t1 = Track.objects.create(
            file='mp3/api1.mp3', original_filename='api1.mp3',
            title='API Song 1', artist='API Artist', duration=200, genre='Pop',
        )
        self.t2 = Track.objects.create(
            file='mp3/api2.mp3', original_filename='api2.mp3',
            title='API Song 2', artist='API Artist', duration=180, genre='Rock',
        )
        self.t3 = Track.objects.create(
            file='mp3/api3.mp3', original_filename='api3.mp3',
            title='API Song 3', artist='Other', duration=120, genre='Pop',
        )

    # ------------------------------------------------------------------
    # Playlist CRUD
    # ------------------------------------------------------------------
    def test_list_playlists_empty(self):
        resp = self.client.get('/api/playlists/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        # List may be paginated
        results = data.get('results', data)
        self.assertEqual(results, [])

    def test_create_playlist_with_tracks(self):
        data = {
            'name': 'Test Playlist',
            'description': 'A test playlist',
            'tracks': [str(self.t1.id), str(self.t2.id)],
        }
        resp = self.client.post('/api/playlists/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        result = resp.json()
        self.assertEqual(result['name'], 'Test Playlist')
        # Verify via DB (create serializer may not expose computed fields)
        pl = Playlist.objects.get(id=result['id'])
        self.assertEqual(pl.total_duration, 380.0)
        self.assertEqual(pl.tracks.count(), 2)

    def test_create_playlist_without_tracks(self):
        resp = self.client.post('/api/playlists/', {'name': 'Empty'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        pl = Playlist.objects.get(id=resp.json()['id'])
        self.assertEqual(pl.tracks.count(), 0)

    def test_create_playlist_missing_name(self):
        resp = self.client.post('/api/playlists/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_playlist_detail(self):
        pl = Playlist.objects.create(name='Detail')
        pl.set_tracks([self.t1.id, self.t3.id])
        resp = self.client.get(f'/api/playlists/{pl.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data['name'], 'Detail')
        self.assertEqual(len(data['tracks']), 2)
        self.assertEqual(data['tracks'][0]['track_detail']['title'], 'API Song 1')
        self.assertEqual(data['tracks'][0]['position'], 0)

    def test_update_playlist(self):
        pl = Playlist.objects.create(name='Old Name')
        pl.set_tracks([self.t1.id])
        resp = self.client.put(
            f'/api/playlists/{pl.id}/',
            {'name': 'New Name', 'tracks': [str(self.t2.id), str(self.t3.id)]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        pl.refresh_from_db()
        self.assertEqual(pl.name, 'New Name')
        self.assertEqual(pl.tracks.count(), 2)
        self.assertEqual(pl.total_duration, 300.0)

    def test_delete_playlist(self):
        pl = Playlist.objects.create(name='To Delete')
        pl.set_tracks([self.t1.id])
        resp = self.client.delete(f'/api/playlists/{pl.id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Playlist.objects.filter(id=pl.id).exists())
        self.assertEqual(PlaylistTrack.objects.filter(playlist_id=pl.id).count(), 0)

    # ------------------------------------------------------------------
    # Remove track from playlist
    # ------------------------------------------------------------------
    def test_remove_track(self):
        pl = Playlist.objects.create(name='Remove Test')
        pl.set_tracks([self.t1.id, self.t2.id, self.t3.id])
        resp = self.client.delete(f'/api/playlists/{pl.id}/tracks/{self.t2.id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(pl.tracks.count(), 2)
        remaining = list(pl.tracks.all())
        self.assertEqual(remaining[0].position, 0)
        self.assertEqual(remaining[1].position, 1)

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------
    def test_generate_playlist(self):
        """Genre filter: both Pop tracks (200+120=320s) fit within 400s target."""
        data = {'genre': ['Pop'], 'target_duration': 400}
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        result = resp.json()
        self.assertIn('tracks', result)
        self.assertIn('total_duration', result)
        self.assertIn('algorithm_metadata', result)
        # Both Pop tracks should be included (priority)
        self.assertEqual(len(result['tracks']), 2)
        self.assertLessEqual(result['total_duration'], 400)

    def test_generate_no_duration(self):
        """No duration: returns all tracks matching the filter."""
        data = {'genre': ['Pop']}
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        result = resp.json()
        self.assertEqual(len(result['tracks']), 2)
        self.assertEqual(result['algorithm_metadata']['algorithm'], 'priority_only')

    def test_generate_no_criteria(self):
        """No filter and no duration must be rejected."""
        resp = self.client.post('/api/playlists/generate/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_no_match(self):
        """Filter that matches no tracks returns 404."""
        data = {'genre': ['Jazz'], 'target_duration': 300}
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_generate_with_artist_filter(self):
        """Artist filter: tracks by 'Other' (t3, 120s) are priority."""
        data = {'artist': ['Other'], 'target_duration': 200}
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        result = resp.json()
        self.assertEqual(len(result['tracks']), 1)
        self.assertEqual(result['tracks'][0]['title'], 'API Song 3')

    def test_generate_artist_icontains(self):
        """
        Artist filter uses __icontains: 'API Artist' must capture both
        t1 ('API Artist') and a variant like 'API Artist & Band'.
        """
        Track.objects.create(
            file='mp3/variant.mp3', original_filename='variant.mp3',
            title='Variant Song', artist='API Artist & Band', duration=150, genre='Pop',
        )
        data = {'artist': ['API Artist'], 'target_duration': 600}
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {t['title'] for t in resp.json()['tracks']}
        # Both 'API Artist' songs AND the 'API Artist & Band' variant must be priority
        self.assertIn('API Song 1', titles)
        self.assertIn('API Song 2', titles)
        self.assertIn('Variant Song', titles)

    def test_generate_priority_before_fallback(self):
        """Priority tracks come before fallback tracks in the result."""
        # Only t3 matches genre=Pop AND artist=Other.
        # t1 and t2 are fallback.
        # With target=400s: t3(120s priority) + filler(t1=200 or t2=180) fill remaining 280s.
        data = {
            'genre': ['Pop'],
            'artist': ['Other'],
            'target_duration': 400,
        }
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        tracks = resp.json()['tracks']
        self.assertGreaterEqual(len(tracks), 1)
        # First track must be the priority match
        self.assertEqual(tracks[0]['title'], 'API Song 3')

    # ------------------------------------------------------------------
    # ZIP Download
    # ------------------------------------------------------------------
    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_download_zip(self):
        mp3_dir = os.path.join(settings.MEDIA_ROOT, 'mp3')
        os.makedirs(mp3_dir, exist_ok=True)
        for fname in ['dl1.mp3', 'dl2.mp3']:
            with open(os.path.join(mp3_dir, fname), 'wb') as f:
                f.write(b'\xff\xfb\x90\x00' + os.urandom(500))

        t1 = Track.objects.create(file='mp3/dl1.mp3', original_filename='dl1.mp3',
                                   title='DL1', artist='A', duration=100)
        t2 = Track.objects.create(file='mp3/dl2.mp3', original_filename='dl2.mp3',
                                   title='DL2', artist='B', duration=150)
        pl = Playlist.objects.create(name='ZIP Test')
        pl.set_tracks([t1.id, t2.id])

        resp = self.client.get(f'/api/playlists/{pl.id}/download/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertIn('playlist_ZIP_Test.zip', resp['Content-Disposition'])

        zip_data = io.BytesIO(
            b''.join(resp.streaming_content) if hasattr(resp, 'streaming_content')
            else resp.content
        )
        with zipfile.ZipFile(zip_data, 'r') as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 2)
            self.assertIn('01_dl1.mp3', names)
            self.assertIn('02_dl2.mp3', names)

    def test_download_zip_empty_playlist(self):
        pl = Playlist.objects.create(name='Empty ZIP')
        resp = self.client.get(f'/api/playlists/{pl.id}/download/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
