"""
API Views for Playlist resource.
"""
import io
import os
import zipfile

from django.conf import settings
from django.http import HttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.db.models import Count, Q
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from tracks.models import Track

from .models import Playlist, PlaylistTrack
from .serializers import (
    PlaylistSerializer,
    PlaylistDetailSerializer,
    PlaylistCreateSerializer,
    PlaylistUpdateSerializer,
    PlaylistGenerateSerializer,
)
from .generator import generate_playlist


class PlaylistViewSet(viewsets.ModelViewSet):
    """
    ViewSet for CRUD operations on Playlists.

    Extra actions:
    - POST /api/playlists/generate/  → optimal playlist generation
    - GET  /api/playlists/{id}/download/ → ZIP download
    - DELETE /api/playlists/{id}/tracks/{track_id}/ → remove a track
    """
    queryset = Playlist.objects.annotate(
        track_count_annotated=Count('tracks')
    )

    def get_serializer_class(self):
        if self.action == 'create':
            return PlaylistCreateSerializer
        if self.action in ('update', 'partial_update'):
            return PlaylistUpdateSerializer
        if self.action == 'retrieve':
            return PlaylistDetailSerializer
        return PlaylistSerializer

    def perform_destroy(self, instance):
        """Delete playlist (cascade handles PlaylistTrack entries)."""
        instance.delete()

    # ------------------------------------------------------------------
    # DELETE /api/playlists/{id}/tracks/{track_id}/
    # ------------------------------------------------------------------
    @action(detail=True, methods=['delete'], url_path=r'tracks/(?P<track_id>[^/.]+)')
    def remove_track(self, request, pk=None, track_id=None):
        """Remove a specific track from the playlist."""
        playlist = self.get_object()
        entry = get_object_or_404(
            PlaylistTrack, playlist=playlist, track_id=track_id
        )
        entry.delete()

        # Re-index positions
        remaining = playlist.tracks.all()
        for idx, pt in enumerate(remaining):
            if pt.position != idx:
                pt.position = idx
                pt.save(update_fields=['position'])

        playlist.recalculate_duration()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # POST /api/playlists/generate/
    # ------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='generate')
    def generate(self, request):
        """
        Generate a playlist using priority-first selection.

        Priority tracks (matching filters) are always included first.
        Fallback tracks fill remaining time only when a target duration is set.
        """
        gen_serializer = PlaylistGenerateSerializer(data=request.data)
        gen_serializer.is_valid(raise_exception=True)
        data = gen_serializer.validated_data

        target_duration = data.get('target_duration')  # None when not provided
        has_filters = any(data.get(f, []) for f in ['genre', 'artist', 'language'])

        # At least one criterion is required
        if not has_filters and target_duration is None:
            return Response(
                {'error': 'Spécifie au moins un filtre (genre, artiste, langue) ou une durée cible.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        base_qs = Track.objects.all()
        if data.get('exclude_ids'):
            base_qs = base_qs.exclude(id__in=data['exclude_ids'])

        if has_filters:
            # Build priority queryset: tracks matching ALL specified filters (AND between fields).
            # Artist uses __icontains to catch metadata variations ("Bob Marley & The Wailers"
            # is captured when the user selects "Bob Marley").
            # Genre and language use __iexact (values are standardized).
            field_lookup = {'genre': 'iexact', 'artist': 'icontains', 'language': 'iexact'}
            priority_q = Q()
            for field in ['genre', 'artist', 'language']:
                values = data.get(field, [])
                if values:
                    lookup = field_lookup[field]
                    field_q = Q()
                    for v in values:
                        field_q |= Q(**{f'{field}__{lookup}': v})
                    priority_q &= field_q

            priority_qs = base_qs.filter(priority_q)
            priority_ids = list(priority_qs.values_list('id', flat=True))
            fallback_qs = base_qs.exclude(id__in=priority_ids)
        else:
            # No filters: all tracks have equal priority; no fallback needed
            priority_qs = base_qs
            fallback_qs = None

        result = generate_playlist(
            priority_queryset=priority_qs,
            fallback_queryset=fallback_qs,
            target_seconds=target_duration,
        )

        if not result['track_ids']:
            return Response(
                {
                    'error': 'Aucune piste ne correspond aux critères.',
                    'tracks': [],
                    'total_duration': 0,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Fetch full track objects preserving the generator's order
        track_map = {
            str(t.id): t
            for t in Track.objects.filter(id__in=result['track_ids'])
        }
        ordered_tracks = [
            track_map[str(tid)]
            for tid in result['track_ids']
            if str(tid) in track_map
        ]

        from tracks.serializers import TrackSerializer
        track_serializer = TrackSerializer(
            ordered_tracks, many=True, context={'request': request}
        )

        return Response({
            'tracks': track_serializer.data,
            'total_duration': result['total_duration'],
            'algorithm_metadata': {
                'algorithm': result['algorithm'],
                'relaxation': result['relaxation'],
                'track_count': len(ordered_tracks),
                'target_duration': target_duration,
            },
        })

    # ------------------------------------------------------------------
    # GET /api/playlists/{id}/download/
    # ------------------------------------------------------------------
    @action(detail=True, methods=['get'], url_path='download')
    def download_zip(self, request, pk=None):
        """
        Download all MP3 files of a playlist as a ZIP archive.
        """
        playlist = self.get_object()
        entries = playlist.tracks.select_related('track').all()

        if not entries:
            raise Http404("Cette playlist ne contient aucune piste.")

        # Build ZIP in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for entry in entries:
                track = entry.track
                file_path = os.path.join(settings.MEDIA_ROOT, track.file)
                if os.path.isfile(file_path):
                    # Use position-prefixed filename for ordering
                    arcname = f"{entry.position + 1:02d}_{track.original_filename}"
                    zf.write(file_path, arcname=arcname)

        zip_buffer.seek(0)

        safe_name = playlist.name.replace(' ', '_').replace('/', '_')
        response = HttpResponse(
            zip_buffer.getvalue(),
            content_type='application/zip',
        )
        response['Content-Disposition'] = (
            f'attachment; filename="playlist_{safe_name}.zip"'
        )
        response['Content-Length'] = len(zip_buffer.getvalue())
        return response
