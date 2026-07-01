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
    PlaylistMergeSerializer,
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
        Generate an optimal playlist based on filters and target duration.
        """
        gen_serializer = PlaylistGenerateSerializer(data=request.data)
        gen_serializer.is_valid(raise_exception=True)
        data = gen_serializer.validated_data

        # Build filtered queryset
        qs = Track.objects.all()
        for field in ['genre', 'artist', 'language']:
            values = data.get(field, [])
            if values:
                query = Q()
                for v in values:
                    query |= Q(**{f'{field}__iexact': v})
                qs = qs.filter(query)
        if data.get('exclude_ids'):
            qs = qs.exclude(id__in=data['exclude_ids'])

        # Run DP algorithm
        result = generate_playlist(qs, data['target_duration'])

        if not result['track_ids']:
            return Response(
                {
                    'error': 'Aucune piste ne correspond aux critères.',
                    'tracks': [],
                    'total_duration': 0,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        # Fetch full track objects in the order returned by DP
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
                'target_duration': data['target_duration'],
            },
        })

    # ------------------------------------------------------------------
    # POST /api/playlists/merge/
    # ------------------------------------------------------------------
    @action(detail=False, methods=['post'], url_path='merge')
    def merge(self, request):
        """
        Fusionne plusieurs playlists en une nouvelle, sans doublons.
        L'ordre : pistes de la 1ère playlist, puis pistes uniques de la 2ème, etc.
        """
        merge_serializer = PlaylistMergeSerializer(data=request.data)
        merge_serializer.is_valid(raise_exception=True)
        data = merge_serializer.validated_data

        # Collecte les pistes dans l'ordre, déduplique par ID de piste
        seen_ids = set()
        ordered_track_ids = []

        for pl_id in data['playlist_ids']:
            try:
                pl = Playlist.objects.get(id=pl_id)
            except Playlist.DoesNotExist:
                continue
            for pt in pl.tracks.all():
                if pt.track_id not in seen_ids:
                    seen_ids.add(pt.track_id)
                    ordered_track_ids.append(pt.track_id)

        if not ordered_track_ids:
            return Response(
                {'error': 'Aucune piste trouvée dans les playlists sélectionnées.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        new_playlist = Playlist.objects.create(
            name=data['name'],
            description=data['description'] or None,
        )
        new_playlist.set_tracks(ordered_track_ids)

        return Response(
            PlaylistDetailSerializer(new_playlist).data,
            status=status.HTTP_201_CREATED,
        )

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
