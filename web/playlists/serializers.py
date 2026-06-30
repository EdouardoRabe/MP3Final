"""
Serializers for Playlist & PlaylistTrack models.
"""
from rest_framework import serializers

from tracks.serializers import TrackSerializer

from .models import Playlist, PlaylistTrack


class PlaylistTrackSerializer(serializers.ModelSerializer):
    """Serializes a single entry in a playlist."""
    track_detail = TrackSerializer(source='track', read_only=True)

    class Meta:
        model = PlaylistTrack
        fields = [
            'id', 'track', 'track_detail', 'position', 'added_at',
        ]


class PlaylistSerializer(serializers.ModelSerializer):
    """Compact representation of a Playlist (list view)."""
    track_count = serializers.SerializerMethodField()
    duration_formatted = serializers.CharField(read_only=True)

    class Meta:
        model = Playlist
        fields = [
            'id', 'name', 'description',
            'total_duration', 'duration_formatted',
            'track_count', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'total_duration', 'created_at', 'updated_at',
        ]

    def get_track_count(self, obj) -> int:
        if hasattr(obj, 'track_count_annotated'):
            return obj.track_count_annotated
        return obj.tracks.count() if obj.pk else 0


class PlaylistDetailSerializer(serializers.ModelSerializer):
    """Full representation of a Playlist with ordered tracks."""
    tracks = PlaylistTrackSerializer(many=True, read_only=True)
    duration_formatted = serializers.CharField(read_only=True)

    class Meta:
        model = Playlist
        fields = [
            'id', 'name', 'description',
            'total_duration', 'duration_formatted',
            'tracks', 'created_at', 'updated_at',
        ]


class PlaylistCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a Playlist with optional initial tracks."""
    tracks = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
        help_text="Liste ordonnée des UUIDs de pistes",
    )

    class Meta:
        model = Playlist
        fields = ['id', 'name', 'description', 'tracks']
        read_only_fields = ['id']

    def create(self, validated_data):
        track_ids = validated_data.pop('tracks', [])
        playlist = Playlist.objects.create(**validated_data)
        if track_ids:
            playlist.set_tracks(track_ids)
        return playlist


class PlaylistUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating a Playlist's name, description, and tracks."""
    tracks = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        write_only=True,
        help_text="Liste ordonnée des UUIDs de pistes (remplace l'existant)",
    )

    class Meta:
        model = Playlist
        fields = ['name', 'description', 'tracks']

    def update(self, instance, validated_data):
        track_ids = validated_data.pop('tracks', None)
        instance.name = validated_data.get('name', instance.name)
        instance.description = validated_data.get('description', instance.description)
        instance.save()
        if track_ids is not None:
            instance.set_tracks(track_ids)
        return instance


class PlaylistGenerateSerializer(serializers.Serializer):
    """Serializer for the playlist generation request."""
    genre = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text="Liste de genres a inclure",
    )
    artist = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text="Liste d'artistes a inclure",
    )
    language = serializers.ListField(
        child=serializers.CharField(), required=False, default=list,
        help_text="Liste de langues a inclure",
    )
    target_duration = serializers.IntegerField(
        default=2700,
        min_value=60,
        max_value=36000,
        help_text="Durée cible en secondes (défaut: 2700 = 45 min)",
    )
    exclude_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        help_text="Liste d'UUIDs de pistes à exclure de la génération",
    )
