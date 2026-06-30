"""
Serializers for the Track model.
"""
import hashlib
import os

from django.conf import settings
from rest_framework import serializers

from .models import Track


class TrackSerializer(serializers.ModelSerializer):
    """Full read-only representation of a Track."""
    file_url = serializers.SerializerMethodField()
    play_url = serializers.SerializerMethodField()
    duration_formatted = serializers.SerializerMethodField()

    class Meta:
        model = Track
        fields = [
            'id',
            'file',
            'file_url',
            'play_url',
            'original_filename',
            'title',
            'artist',
            'album',
            'genre',
            'language',
            'duration',
            'duration_formatted',
            'file_size',
            'file_hash',
            'metadata_complete',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'file', 'file_size', 'file_hash',
            'metadata_complete', 'created_at', 'updated_at',
        ]

    def get_file_url(self, obj):
        if obj.file:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(settings.MEDIA_URL + obj.file)
        return None

    def get_play_url(self, obj):
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(f'/api/tracks/{obj.id}/play/')
        return None

    def get_duration_formatted(self, obj):
        return obj.duration_formatted()


class TrackCreateSerializer(serializers.ModelSerializer):
    """
    Serializer used for track creation (POST).
    Requires a file upload + optional metadata.
    """
    file = serializers.FileField(
        required=True,
        help_text="Fichier MP3 à uploader (max 50 MB)"
    )

    class Meta:
        model = Track
        fields = [
            'file', 'title', 'artist', 'album',
            'genre', 'language', 'duration', 'original_filename',
        ]

    def validate_file(self, value):
        # Validate extension
        ext = os.path.splitext(value.name)[1].lower()
        if ext != '.mp3':
            raise serializers.ValidationError("Seuls les fichiers .mp3 sont acceptés.")

        # Validate MIME type
        if (value.content_type or '').lower() not in ('audio/mpeg', 'audio/mp3', 'audio/mpeg3'):
            raise serializers.ValidationError(
                f"Type MIME invalide : {value.content_type}. Attendu : audio/mpeg."
            )

        # Validate size (50 MB max)
        max_size = 50 * 1024 * 1024
        if value.size > max_size:
            raise serializers.ValidationError(
                f"Le fichier dépasse la taille maximale de 50 MB (taille : {value.size / 1024 / 1024:.1f} MB)."
            )

        return value

    def create(self, validated_data):
        uploaded_file = validated_data.pop('file')

        # Read file content for hashing
        file_bytes = uploaded_file.read()
        uploaded_file.seek(0)

        # Compute hash
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        # Check for duplicates
        if Track.objects.filter(file_hash=file_hash).exists():
            raise serializers.ValidationError(
                {"file": "Un fichier identique (même hash SHA256) existe déjà dans la base."}
            )

        # Generate safe filename
        safe_name = f"{file_hash[:16]}_{uploaded_file.name}"
        relative_path = f"mp3/{safe_name}"

        # Save file to media/mp3/
        full_path = os.path.join(settings.MEDIA_ROOT, relative_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as dst:
            dst.write(file_bytes)

        # Use original_filename from request, fall back to uploaded filename
        original_filename = validated_data.pop('original_filename', uploaded_file.name)

        track = Track.objects.create(
            file=relative_path,
            original_filename=original_filename,
            file_size=uploaded_file.size,
            file_hash=file_hash,
            metadata_complete=self._is_metadata_complete(validated_data),
            **validated_data,
        )
        return track

    def _is_metadata_complete(self, data: dict) -> bool:
        """Check if all meaningful metadata fields are filled."""
        DEFAULTS = ('', 'sans titre', 'inconnu')
        required = ['title', 'artist']
        return all(
            data.get(field) and data[field].strip().lower() not in DEFAULTS
            for field in required
        )


class TrackUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating track metadata (no file)."""

    class Meta:
        model = Track
        fields = [
            'title', 'artist', 'album', 'genre', 'language', 'original_filename',
        ]
