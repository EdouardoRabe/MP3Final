"""
Playlist & PlaylistTrack models.
"""
import uuid

from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError


class Playlist(models.Model):
    """A named collection of tracks with a calculated total duration."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='playlists',
        verbose_name='utilisateur',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    total_duration = models.FloatField(default=0.0, help_text="Durée totale calculée en secondes")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Playlist'
        verbose_name_plural = 'Playlists'

    def __str__(self):
        nb = self.tracks.count() if self.pk else 0
        return f"{self.name} ({nb} piste(s))"

    def duration_formatted(self) -> str:
        """Return total_duration as h:mm:ss or mm:ss."""
        total = int(self.total_duration)
        hours = total // 3600
        minutes = (total % 3600) // 60
        seconds = total % 60
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def recalculate_duration(self):
        """Recalculate total_duration from associated tracks."""
        total = self.tracks.aggregate(
            total=models.Sum('track__duration')
        )['total'] or 0.0
        self.total_duration = round(total, 2)
        self.save(update_fields=['total_duration', 'updated_at'])

    def set_tracks(self, track_ids: list):
        """
        Replace the playlist's tracks with a new ordered list of track IDs.

        Args:
            track_ids: Ordered list of track UUIDs (as strings or UUID objects).
        """
        # Delete existing associations
        self.tracks.all().delete()

        # Create new associations
        from tracks.models import Track
        entries = []
        for position, track_id in enumerate(track_ids):
            track = Track.objects.get(pk=track_id)
            entries.append(
                PlaylistTrack(playlist=self, track=track, position=position)
            )
        PlaylistTrack.objects.bulk_create(entries)
        self.recalculate_duration()


class PlaylistTrack(models.Model):
    """
    Ordered association between a Playlist and a Track.
    Uniqueness: (playlist, position) and (playlist, track).
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    playlist = models.ForeignKey(
        Playlist, on_delete=models.CASCADE,
        related_name='tracks',
    )
    track = models.ForeignKey(
        'tracks.Track', on_delete=models.CASCADE,
        related_name='playlist_entries',
    )
    position = models.IntegerField(help_text="Ordre dans la playlist (0-indexé)")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(
                fields=['playlist', 'position'],
                name='unique_position_per_playlist',
            ),
            models.UniqueConstraint(
                fields=['playlist', 'track'],
                name='unique_track_per_playlist',
            ),
        ]

    def __str__(self):
        return f"{self.playlist.name} #{self.position}: {self.track.title}"
