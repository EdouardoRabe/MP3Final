"""
Frontend views — server-rendered pages via Django templates.
"""
from django.shortcuts import render
from django.db.models import Count, Sum

from tracks.models import Track
from playlists.models import Playlist


def dashboard(request):
    """Home page with global statistics."""
    stats = {
        'total_tracks': Track.objects.count(),
        'total_playlists': Playlist.objects.count(),
        'total_duration': Track.objects.aggregate(s=Sum('duration'))['s'] or 0,
        'total_size': Track.objects.aggregate(s=Sum('file_size'))['s'] or 0,
        'genres': list(Track.objects.values_list('genre', flat=True).distinct()),
    }
    # Format duration
    total_sec = int(stats['total_duration'])
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    stats['total_duration_formatted'] = (
        f"{hours}h {minutes}min" if hours > 0 else f"{minutes} min"
    )
    # Format size
    total_mb = stats['total_size'] / (1024 * 1024)
    stats['total_size_formatted'] = f"{total_mb:.1f} MB" if total_mb > 0 else "0 MB"

    # Recent tracks
    recent_tracks = Track.objects.order_by('-created_at')[:5]
    # Recent playlists
    recent_playlists = Playlist.objects.annotate(
        tc=Count('tracks')
    ).order_by('-created_at')[:5]

    return render(request, 'dashboard.html', {
        'stats': stats,
        'recent_tracks': recent_tracks,
        'recent_playlists': recent_playlists,
    })


def track_list(request):
    """Track management page with DataTable, search, filters."""
    tracks = Track.objects.all()

    # Collect filter options
    genres = list(
        Track.objects.exclude(genre__isnull=True)
        .exclude(genre='')
        .values_list('genre', flat=True)
        .distinct()
        .order_by('genre')
    )
    artists = list(
        Track.objects.values_list('artist', flat=True)
        .distinct()
        .order_by('artist')
    )
    languages = list(
        Track.objects.exclude(language__isnull=True)
        .exclude(language='')
        .values_list('language', flat=True)
        .distinct()
        .order_by('language')
    )

    return render(request, 'tracks/list.html', {
        'genres': genres,
        'artists': artists,
        'languages': languages,
    })


def playlist_list(request):
    """List all saved playlists."""
    playlists = Playlist.objects.annotate(
        tc=Count('tracks')
    ).order_by('-updated_at')
    return render(request, 'playlists/list.html', {
        'playlists': playlists,
    })


def playlist_generate(request):
    """Playlist generator page with filter form and interactive results."""
    # Collect available filter options for the form
    genres = list(
        Track.objects.exclude(genre__isnull=True)
        .exclude(genre='')
        .values_list('genre', flat=True)
        .distinct()
        .order_by('genre')
    )
    artists = list(
        Track.objects.values_list('artist', flat=True)
        .distinct()
        .order_by('artist')
    )
    languages = list(
        Track.objects.exclude(language__isnull=True)
        .exclude(language='')
        .values_list('language', flat=True)
        .distinct()
        .order_by('language')
    )

    return render(request, 'playlists/generate.html', {
        'genres': genres,
        'artists': artists,
        'languages': languages,
    })


def playlist_detail(request, playlist_id):
    """Playlist detail page with audio player and ZIP download."""
    import json
    from django.core.serializers.json import DjangoJSONEncoder
    from playlists.serializers import PlaylistDetailSerializer

    playlist = Playlist.objects.prefetch_related('tracks__track').get(pk=playlist_id)

    # Serialize playlist data (including tracks) to inject directly into the page
    serializer = PlaylistDetailSerializer(playlist, context={'request': request})
    playlist_json = json.dumps(serializer.data, ensure_ascii=False, cls=DjangoJSONEncoder)

    return render(request, 'playlists/detail.html', {
        'playlist': playlist,
        'playlist_json': playlist_json,
    })
