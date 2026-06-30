"""
Tests for the Tracks app — models, serializers, API endpoints, streaming.
"""
import io
import json
import os
import tempfile
import uuid

from django.conf import settings
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient
from rest_framework import status

from .models import Track


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TrackModelTests(TestCase):
    """Tests for the Track model."""

    def test_create_track_minimal(self):
        """Creating a Track with minimal fields."""
        track = Track.objects.create(
            file='mp3/test.mp3',
            original_filename='test.mp3',
            title='Test Title',
            artist='Test Artist',
            duration=180.5,
        )
        self.assertEqual(track.title, 'Test Title')
        self.assertEqual(track.artist, 'Test Artist')
        self.assertEqual(track.duration, 180.5)
        self.assertIsNotNone(track.id)
        self.assertIsInstance(track.id, uuid.UUID)
        self.assertFalse(track.metadata_complete)

    def test_duration_formatted(self):
        """duration_formatted returns mm:ss."""
        track = Track(duration=125)
        self.assertEqual(track.duration_formatted(), '02:05')
        track.duration = 3661
        self.assertEqual(track.duration_formatted(), '61:01')
        track.duration = 0
        self.assertEqual(track.duration_formatted(), '00:00')

    def test_compute_hash(self):
        """SHA256 hash computation."""
        h = Track.compute_hash(b'hello')
        self.assertEqual(len(h), 64)
        self.assertEqual(
            h,
            '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
        )

    def test_file_hash_unique(self):
        """Duplicate file_hash raises IntegrityError."""
        Track.objects.create(
            file='a.mp3', original_filename='a.mp3',
            title='A', artist='A', duration=10,
            file_hash='abc123',
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Track.objects.create(
                file='b.mp3', original_filename='b.mp3',
                title='B', artist='B', duration=20,
                file_hash='abc123',
            )

    def test_str_method(self):
        track = Track(title='Song', artist='Artist')
        self.assertEqual(str(track), 'Artist — Song')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TrackAPITests(TestCase):
    """Tests for the Track API endpoints."""

    def setUp(self):
        self.client = APIClient()
        self.track = Track.objects.create(
            file='mp3/existing.mp3',
            original_filename='existing.mp3',
            title='Existing Song',
            artist='Existing Artist',
            album='Test Album',
            genre='Rock',
            language='en',
            duration=200.0,
            file_size=1024,
            file_hash='existing_hash_123',
            metadata_complete=True,
        )
        # Create a real temp MP3 file for upload tests
        self.temp_media = tempfile.mkdtemp()
        settings.MEDIA_ROOT = self.temp_media
        os.makedirs(os.path.join(self.temp_media, 'mp3'), exist_ok=True)

        # Create minimal valid MP3 bytes (just the header)
        self.fake_mp3_content = (
            b'\xff\xfb\x90\x00'  # MP3 frame header
            + b'\x00' * 1000      # padding
        )

    def _create_upload_file(self, name='test.mp3', content=None):
        content = content or self.fake_mp3_content
        return SimpleUploadedFile(
            name=name,
            content=content,
            content_type='audio/mpeg',
        )

    # ------------------------------------------------------------------
    # GET /api/tracks/
    # ------------------------------------------------------------------
    def test_list_tracks(self):
        resp = self.client.get('/api/tracks/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertIn('results', data)
        self.assertIn('count', data)
        self.assertEqual(data['count'], 1)
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['title'], 'Existing Song')

    def test_list_tracks_search(self):
        Track.objects.create(
            file='mp3/other.mp3', original_filename='other.mp3',
            title='Another', artist='Someone', duration=100,
        )
        resp = self.client.get('/api/tracks/?search=Existing')
        data = resp.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['results'][0]['title'], 'Existing Song')

    def test_list_tracks_filter_genre(self):
        resp = self.client.get('/api/tracks/?genre=Rock')
        self.assertEqual(resp.json()['count'], 1)
        resp = self.client.get('/api/tracks/?genre=Jazz')
        self.assertEqual(resp.json()['count'], 0)

    def test_list_tracks_pagination(self):
        # Create 5 tracks
        for i in range(5):
            Track.objects.create(
                file=f'mp3/{i}.mp3', original_filename=f'{i}.mp3',
                title=f'Song {i}', artist=f'Artist {i}', duration=100 + i,
            )
        resp = self.client.get('/api/tracks/?page_size=3')
        data = resp.json()
        self.assertEqual(len(data['results']), 3)
        self.assertIsNotNone(data['next'])

    # ------------------------------------------------------------------
    # GET /api/tracks/{id}/
    # ------------------------------------------------------------------
    def test_retrieve_track(self):
        resp = self.client.get(f'/api/tracks/{self.track.id}/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data['title'], 'Existing Song')
        self.assertIn('file_url', data)
        self.assertIn('play_url', data)
        self.assertIn('duration_formatted', data)

    def test_retrieve_nonexistent(self):
        resp = self.client.get(f'/api/tracks/{uuid.uuid4()}/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    # ------------------------------------------------------------------
    # POST /api/tracks/ — Upload
    # ------------------------------------------------------------------
    def test_create_track_success(self):
        upload_file = self._create_upload_file('new_song.mp3')
        data = {
            'file': upload_file,
            'title': 'New Song',
            'artist': 'New Artist',
            'album': 'New Album',
            'genre': 'Pop',
            'language': 'fr',
            'duration': '210.5',
            'original_filename': 'new_song.mp3',
        }
        resp = self.client.post('/api/tracks/', data, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        result = resp.json()
        self.assertEqual(result['title'], 'New Song')
        self.assertIsNotNone(result['file_hash'])
        self.assertTrue(os.path.isfile(
            os.path.join(self.temp_media, result['file'])
        ))

    def test_create_track_no_file(self):
        data = {'title': 'No File'}
        resp = self.client.post('/api/tracks/', data, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_track_invalid_extension(self):
        upload_file = SimpleUploadedFile(
            'doc.pdf', b'fake pdf content', content_type='application/pdf'
        )
        data = {'file': upload_file, 'title': 'Bad'}
        resp = self.client.post('/api/tracks/', data, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('mp3', str(resp.json()).lower())

    def test_create_track_duplicate_hash(self):
        # First upload
        file1 = self._create_upload_file('song.mp3')
        resp = self.client.post('/api/tracks/', {
            'file': file1, 'title': 'First', 'artist': 'A',
            'duration': '100', 'original_filename': 'song.mp3',
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        # Second upload with same content
        file2 = SimpleUploadedFile(
            'song_copy.mp3', self.fake_mp3_content, content_type='audio/mpeg'
        )
        resp = self.client.post('/api/tracks/', {
            'file': file2, 'title': 'Second', 'artist': 'B',
            'duration': '100', 'original_filename': 'song_copy.mp3',
        }, format='multipart')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # PUT / PATCH
    # ------------------------------------------------------------------
    def test_update_track_metadata(self):
        resp = self.client.put(
            f'/api/tracks/{self.track.id}/',
            {'title': 'Updated Title', 'artist': 'Updated Artist',
             'album': 'New Album', 'genre': 'Jazz', 'language': 'en',
             'original_filename': 'existing.mp3'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertEqual(data['title'], 'Updated Title')
        self.assertEqual(data['genre'], 'Jazz')

    def test_partial_update(self):
        resp = self.client.patch(
            f'/api/tracks/{self.track.id}/',
            {'genre': 'Blues'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.track.refresh_from_db()
        self.assertEqual(self.track.genre, 'Blues')
        self.assertEqual(self.track.title, 'Existing Song')  # unchanged

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------
    def test_delete_track(self):
        # Create a file on disk
        track_file = os.path.join(self.temp_media, 'mp3', 'to_delete.mp3')
        os.makedirs(os.path.dirname(track_file), exist_ok=True)
        with open(track_file, 'w') as f:
            f.write('dummy')

        track = Track.objects.create(
            file='mp3/to_delete.mp3',
            original_filename='to_delete.mp3',
            title='Delete Me', artist='X', duration=50,
        )

        resp = self.client.delete(f'/api/tracks/{track.id}/')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Track.objects.filter(id=track.id).exists())
        self.assertFalse(os.path.isfile(track_file))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class TrackStreamingTests(TestCase):
    """Tests for the streaming endpoint."""

    def setUp(self):
        self.client = APIClient()
        self.temp_media = tempfile.mkdtemp()
        settings.MEDIA_ROOT = self.temp_media

        mp3_dir = os.path.join(self.temp_media, 'mp3')
        os.makedirs(mp3_dir, exist_ok=True)

        # Create a real file on disk
        self.mp3_content = b'\xff\xfb\x90\x00' + b'\x00' * 2000
        self.mp3_path = os.path.join(mp3_dir, 'stream_test.mp3')
        with open(self.mp3_path, 'wb') as f:
            f.write(self.mp3_content)

        self.track = Track.objects.create(
            file='mp3/stream_test.mp3',
            original_filename='stream_test.mp3',
            title='Stream Test', artist='Test', duration=30,
        )

    def test_stream_full_file(self):
        resp = self.client.get(f'/api/tracks/{self.track.id}/play/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'audio/mpeg')
        self.assertIn('bytes', resp['Accept-Ranges'])
        content = b''.join(resp.streaming_content)
        self.assertEqual(content, self.mp3_content)

    def test_stream_range_request(self):
        """HTTP Range request for seeking."""
        resp = self.client.get(
            f'/api/tracks/{self.track.id}/play/',
            HTTP_RANGE='bytes=0-99',
        )
        self.assertEqual(resp.status_code, status.HTTP_206_PARTIAL_CONTENT)
        self.assertIn('Content-Range', resp)
        self.assertIn('bytes 0-99/', resp['Content-Range'])

    def test_stream_nonexistent_track(self):
        resp = self.client.get(f'/api/tracks/{uuid.uuid4()}/play/')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
