"""
Track model — represents an MP3 file with its metadata.
"""
import hashlib
import uuid

from django.db import models


class Track(models.Model):
    """
    Represents a single MP3 track stored on the server.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # File information
    file = models.CharField(max_length=500, help_text="Chemin relatif du fichier MP3 stocké")
    original_filename = models.CharField(max_length=255)

    # Metadata (extracted or manually filled)
    title = models.CharField(max_length=255, default="Sans titre")
    artist = models.CharField(max_length=255, default="Inconnu")
    album = models.CharField(max_length=255, default="", blank=True)
    genre = models.CharField(max_length=100, null=True, blank=True)
    language = models.CharField(max_length=50, null=True, blank=True)

    # Technical details
    duration = models.FloatField(default=0.0, help_text="Durée en secondes")
    file_size = models.IntegerField(default=0, help_text="Taille en bytes")
    file_hash = models.CharField(
        max_length=64, unique=True, null=True,
        help_text="SHA256 du fichier pour détection de doublons"
    )
    metadata_complete = models.BooleanField(
        default=False,
        help_text="True si l'extraction automatique a trouvé tous les champs"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Piste'
        verbose_name_plural = 'Pistes'

    def __str__(self):
        return f"{self.artist} — {self.title}"

    def duration_formatted(self) -> str:
        """Return duration as mm:ss."""
        total_seconds = int(self.duration)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def compute_hash(file_bytes: bytes) -> str:
        """Compute SHA256 hash of file content."""
        return hashlib.sha256(file_bytes).hexdigest()
