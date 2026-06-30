"""
API Views for the Track resource.
"""
import os
import mimetypes

from django.conf import settings
from django.http import StreamingHttpResponse, Http404
from django.shortcuts import get_object_or_404
from django.db.models import Q
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from django_filters.rest_framework import DjangoFilterBackend

from .models import Track
from .serializers import TrackSerializer, TrackCreateSerializer, TrackUpdateSerializer


class TrackViewSet(viewsets.ModelViewSet):
    """
    ViewSet for CRUD operations on Tracks.

    Supports:
    - List with pagination, search, and filtering
    - Create with file upload
    - Retrieve
    - Update / Partial update (metadata only)
    - Delete (removes file as well)
    - Streaming audio playback
    """
    queryset = Track.objects.all()
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields: list[str] = []
    search_fields = ['title', 'artist', 'album', 'genre', 'original_filename']
    ordering_fields = ['title', 'artist', 'album', 'duration', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        for field in ['genre', 'artist', 'language']:
            value = self.request.query_params.get(field, '')
            if value:
                values = [v.strip() for v in value.split(',') if v.strip()]
                if values:
                    query = Q()
                    for v in values:
                        query |= Q(**{f'{field}__iexact': v})
                    qs = qs.filter(query)
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return TrackCreateSerializer
        if self.action in ('update', 'partial_update'):
            return TrackUpdateSerializer
        return TrackSerializer

    def perform_destroy(self, instance):
        """Delete the physical MP3 file before deleting the DB record."""
        file_path = os.path.join(settings.MEDIA_ROOT, instance.file)
        if os.path.isfile(file_path):
            os.remove(file_path)
        instance.delete()

    @action(detail=True, methods=['get'], url_path='play')
    def play(self, request, pk=None):
        """
        Stream the MP3 file for audio playback.
        Supports HTTP Range requests for seeking.
        """
        track = get_object_or_404(Track, pk=pk)
        file_path = os.path.join(settings.MEDIA_ROOT, track.file)

        if not os.path.isfile(file_path):
            raise Http404("Fichier audio introuvable sur le disque.")

        content_type, _ = mimetypes.guess_type(file_path)
        content_type = content_type or 'audio/mpeg'

        file_size = os.path.getsize(file_path)

        # Handle Range requests for seeking
        range_header = request.headers.get('Range', '')
        if range_header:
            from django.http import HttpResponseNotAllowed
            start, end = 0, file_size - 1
            try:
                range_spec = range_header.replace('bytes=', '').split('-')
                start = int(range_spec[0]) if range_spec[0] else 0
                end = int(range_spec[1]) if range_spec[1] else file_size - 1
            except (ValueError, IndexError):
                pass

            length = end - start + 1

            def range_stream():
                with open(file_path, 'rb') as f:
                    f.seek(start)
                    remaining = length
                    chunk_size = 8192
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)

            response = StreamingHttpResponse(
                range_stream(),
                status=206,
                content_type=content_type,
            )
            response['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            response['Content-Length'] = str(length)
            response['Accept-Ranges'] = 'bytes'
            return response

        # Full file stream
        def full_stream():
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    yield chunk

        response = StreamingHttpResponse(
            full_stream(),
            content_type=content_type,
        )
        response['Content-Length'] = str(file_size)
        response['Accept-Ranges'] = 'bytes'
        response['Content-Disposition'] = f'inline; filename="{track.original_filename}"'
        return response
