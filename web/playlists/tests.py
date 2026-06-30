"""
Tests for the Playlists app — models, API endpoints, generator, ZIP download.
"""
import io
import json
import os
import tempfile
import uuid
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
    """Tests for the DP playlist generation algorithm."""

    def setUp(self):
        # Create tracks with varying durations
        self.tracks_data = []
        for i in range(10):
            track = Track.objects.create(
                file=f'mp3/gen_{i}.mp3',
                original_filename=f'gen_{i}.mp3',
                title=f'Track {i}',
                artist='Test Artist',
                duration=30 + i * 25,  # 30, 55, 80, 105, 130, 155, 180, 205, 230, 255
            )
            self.tracks_data.append(track)

    def test_generate_empty_queryset(self):
        qs = Track.objects.filter(title='Nonexistent')
        result = generate_playlist(qs, 300)
        self.assertEqual(result['track_ids'], [])
        self.assertEqual(result['total_duration'], 0.0)

    def test_generate_exact_match(self):
        """Generate a playlist with an exact sum target."""
        # Tracks: 30, 55, 80, 105, 130, 155, 180, 205, 230, 255
        # 30 + 80 + 105 + 180 = 395, 55 + 130 + 255 = 440
        qs = Track.objects.all()
        result = generate_playlist(qs, 395)
        # Should find the optimal combination
        self.assertGreater(result['total_duration'], 0)
        self.assertLessEqual(result['total_duration'], 395)
        self.assertEqual(result['algorithm'], 'dp_knapsack')

    def test_generate_small_target(self):
        """Target smaller than the shortest track."""
        qs = Track.objects.filter(duration__gt=200)  # Only tracks > 200s
        result = generate_playlist(qs, 100)
        self.assertEqual(result['track_ids'], [])

    def test_generate_relaxation(self):
        """When strict DP gives poor results, relaxation should kick in."""
        # Create tracks that are all too long
        Track.objects.all().delete()
        for i in range(5):
            Track.objects.create(
                file=f'mp3/long_{i}.mp3',
                original_filename=f'long_{i}.mp3',
                title=f'Long {i}',
                artist='A',
                duration=600 + i * 10,  # all > 10 minutes
            )
        qs = Track.objects.all()
        result = generate_playlist(qs, 300)  # target 5 min
        # Relaxation should allow overage
        self.assertTrue(result['relaxation'])
        self.assertGreater(result['total_duration'], 300)

    def test_knapsack_dp_core(self):
        """Test the core DP function directly."""
        tracks = [
            {'id': 'a', 'duration': 30},
            {'id': 'b', 'duration': 55},
            {'id': 'c', 'duration': 80},
        ]
        durations = [300, 550, 800]  # scaled x10
        result = _knapsack_dp(tracks, durations, 850)
        # 300 + 550 = 850 (exact match with a + b)
        self.assertEqual(result['best_sum'], 850)
        self.assertIn('a', result['best_ids'])
        self.assertIn('b', result['best_ids'])


# ======================================================================
# Model Tests
# ======================================================================
class PlaylistModelTests(TestCase):
    """Tests for Playlist and PlaylistTrack models."""

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

        # Verify ordering
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
    """Tests for the Playlist API endpoints."""

    def setUp(self):
        self.client = APIClient()
        # Create tracks
        self.t1 = Track.objects.create(
            file='mp3/api1.mp3', original_filename='api1.mp3',
            title='API Song 1', artist='API Artist', duration=200, genre='Pop'
        )
        self.t2 = Track.objects.create(
            file='mp3/api2.mp3', original_filename='api2.mp3',
            title='API Song 2', artist='API Artist', duration=180, genre='Rock'
        )
        self.t3 = Track.objects.create(
            file='mp3/api3.mp3', original_filename='api3.mp3',
            title='API Song 3', artist='Other', duration=120, genre='Pop'
        )

    # ------------------------------------------------------------------
    # Playlist CRUD
    # ------------------------------------------------------------------
    def test_list_playlists_empty(self):
        resp = self.client.get('/api/playlists/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json(), [])

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
        self.assertEqual(result['total_duration'], 380.0)
        self.assertEqual(result['track_count'], 2)

    def test_create_playlist_without_tracks(self):
        resp = self.client.post('/api/playlists/', {
            'name': 'Empty',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.json()['track_count'], 0)

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
        # Check track detail is included
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
        self.assertEqual(
            PlaylistTrack.objects.filter(playlist_id=pl.id).count(), 0
        )

    # ------------------------------------------------------------------
    # Remove track from playlist
    # ------------------------------------------------------------------
    def test_remove_track(self):
        pl = Playlist.objects.create(name='Remove Test')
        pl.set_tracks([self.t1.id, self.t2.id, self.t3.id])

        resp = self.client.delete(f'/api/playlists/{pl.id}/tracks/{self.t2.id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(pl.tracks.count(), 2)

        # Verify re-indexing
        remaining = list(pl.tracks.all())
        self.assertEqual(remaining[0].position, 0)
        self.assertEqual(remaining[1].position, 1)

    # ------------------------------------------------------------------
    # Generate
    # ------------------------------------------------------------------
    def test_generate_playlist(self):
        data = {
            'genre': 'Pop',
            'target_duration': 300,
        }
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        result = resp.json()
        self.assertIn('tracks', result)
        self.assertIn('total_duration', result)
        self.assertIn('algorithm_metadata', result)
        # Only Pop tracks (t1=200, t3=120)
        self.assertEqual(len(result['tracks']), 2)
        self.assertLessEqual(result['total_duration'], 320)

    def test_generate_no_match(self):
        data = {'genre': 'Jazz', 'target_duration': 300}
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_generate_with_artist_filter(self):
        data = {
            'artist': 'Other',
            'target_duration': 200,
        }
        resp = self.client.post('/api/playlists/generate/', data, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        result = resp.json()
        self.assertEqual(len(result['tracks']), 1)
        self.assertEqual(result['tracks'][0]['title'], 'API Song 3')

    # ------------------------------------------------------------------
    # ZIP Download
    # ------------------------------------------------------------------
    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_download_zip(self):
        # Create real files on disk
        mp3_dir = os.path.join(settings.MEDIA_ROOT, 'mp3')
        os.makedirs(mp3_dir, exist_ok=True)

        for fname in ['dl1.mp3', 'dl2.mp3']:
            with open(os.path.join(mp3_dir, fname), 'wb') as f:
                f.write(b'\xff\xfb\x90\x00' + os.urandom(500))

        t1 = Track.objects.create(
            file='mp3/dl1.mp3', original_filename='dl1.mp3',
            title='DL1', artist='A', duration=100,
        )
        t2 = Track.objects.create(
            file='mp3/dl2.mp3', original_filename='dl2.mp3',
            title='DL2', artist='B', duration=150,
        )

        pl = Playlist.objects.create(name='ZIP Test')
        pl.set_tracks([t1.id, t2.id])

        resp = self.client.get(f'/api/playlists/{pl.id}/download/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/zip')
        self.assertIn('attachment', resp['Content-Disposition'])
        self.assertIn('playlist_ZIP_Test.zip', resp['Content-Disposition'])

        # Verify ZIP contents
        zip_data = io.BytesIO(b''.join(resp.streaming_content) if hasattr(resp, 'streaming_content') else resp.content)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            names = zf.namelist()
            self.assertEqual(len(names), 2)
            self.assertIn('01_dl1.mp3', names)
            self.assertIn('02_dl2.mp3', names)

    def test_download_zip_empty_playlist(self):
        pl = Playlist.objects.create(name='Empty ZIP')
        resp = self.client.get(f'/api/playlists/{pl.id}/download/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
