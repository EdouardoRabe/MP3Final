"""
URL routing for the frontend pages.
"""
from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('tracks/', views.track_list, name='track_list'),
    path('playlists/', views.playlist_list, name='playlist_list'),
    path('playlists/generate/', views.playlist_generate, name='playlist_generate'),
    path('playlists/<uuid:playlist_id>/', views.playlist_detail, name='playlist_detail'),
    # Authentification
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
]
