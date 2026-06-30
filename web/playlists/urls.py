"""
URL routing for the playlists app.
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import PlaylistViewSet

router = DefaultRouter()
router.register(r'playlists', PlaylistViewSet, basename='playlist')

urlpatterns = [
    path('', include(router.urls)),
]
