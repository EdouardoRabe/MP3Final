"""
Frontend views — server-rendered pages via Django templates.
"""
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404, redirect, render

from tracks.models import Track
from playlists.models import Playlist


# ============================================================
# Authentification
# ============================================================

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    username = ''

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect(request.GET.get('next', '/'))
        error = "Nom d'utilisateur ou mot de passe incorrect."

    return render(request, 'auth/login.html', {'error': error, 'username': username})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    form_data = {}

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password_confirm = request.POST.get('password_confirm', '')
        form_data = {'username': username, 'email': email}

        if not username or not password:
            error = "Le nom d'utilisateur et le mot de passe sont obligatoires."
        elif password != password_confirm:
            error = "Les mots de passe ne correspondent pas."
        elif User.objects.filter(username=username).exists():
            error = "Ce nom d'utilisateur est déjà pris."
        else:
            user = User.objects.create_user(username=username, email=email, password=password)
            login(request, user)
            return redirect('dashboard')

    return render(request, 'auth/register.html', {'error': error, **form_data})


def logout_view(request):
    logout(request)
    return redirect('login')


# ============================================================
# Pages publiques
# ============================================================

def dashboard(request):
    """Home page with global statistics."""
    stats = {
        'total_tracks': Track.objects.count(),
        'total_playlists': Playlist.objects.count(),
        'total_duration': Track.objects.aggregate(s=Sum('duration'))['s'] or 0,
        'total_size': Track.objects.aggregate(s=Sum('file_size'))['s'] or 0,
        'genres': list(Track.objects.values_list('genre', flat=True).distinct()),
    }
    total_sec = int(stats['total_duration'])
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    stats['total_duration_formatted'] = (
        f"{hours}h {minutes}min" if hours > 0 else f"{minutes} min"
    )
    total_mb = stats['total_size'] / (1024 * 1024)
    stats['total_size_formatted'] = f"{total_mb:.1f} MB" if total_mb > 0 else "0 MB"

    recent_tracks = Track.objects.order_by('-created_at')[:5]
    recent_playlists = Playlist.objects.annotate(tc=Count('tracks')).order_by('-created_at')[:5]

    return render(request, 'dashboard.html', {
        'stats': stats,
        'recent_tracks': recent_tracks,
        'recent_playlists': recent_playlists,
    })


def track_list(request):
    """Track management page — accessible sans connexion."""
    genres = list(
        Track.objects.exclude(genre__isnull=True).exclude(genre='')
        .values_list('genre', flat=True).distinct().order_by('genre')
    )
    artists = list(
        Track.objects.values_list('artist', flat=True).distinct().order_by('artist')
    )
    languages = list(
        Track.objects.exclude(language__isnull=True).exclude(language='')
        .values_list('language', flat=True).distinct().order_by('language')
    )
    return render(request, 'tracks/list.html', {
        'genres': genres,
        'artists': artists,
        'languages': languages,
    })


# ============================================================
# Pages playlists (connexion obligatoire)
# ============================================================

@login_required
def playlist_list(request):
    """Collections de l'utilisateur connecté."""
    playlists = (
        Playlist.objects.filter(user=request.user)
        .annotate(tc=Count('tracks'))
        .order_by('-updated_at')
    )
    return render(request, 'playlists/list.html', {'playlists': playlists})


@login_required
def playlist_generate(request):
    """Playlist generator page."""
    genres = list(
        Track.objects.exclude(genre__isnull=True).exclude(genre='')
        .values_list('genre', flat=True).distinct().order_by('genre')
    )
    artists = list(
        Track.objects.values_list('artist', flat=True).distinct().order_by('artist')
    )
    languages = list(
        Track.objects.exclude(language__isnull=True).exclude(language='')
        .values_list('language', flat=True).distinct().order_by('language')
    )
    return render(request, 'playlists/generate.html', {
        'genres': genres,
        'artists': artists,
        'languages': languages,
    })


@login_required
def playlist_detail(request, playlist_id):
    """Playlist detail page — vérifie que la playlist appartient à l'utilisateur."""
    import json
    from django.core.serializers.json import DjangoJSONEncoder
    from playlists.serializers import PlaylistDetailSerializer

    playlist = get_object_or_404(
        Playlist.objects.prefetch_related('tracks__track'),
        pk=playlist_id,
        user=request.user,
    )
    serializer = PlaylistDetailSerializer(playlist, context={'request': request})
    playlist_json = json.dumps(serializer.data, ensure_ascii=False, cls=DjangoJSONEncoder)

    return render(request, 'playlists/detail.html', {
        'playlist': playlist,
        'playlist_json': playlist_json,
    })
