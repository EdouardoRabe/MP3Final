"""
Django Admin configuration for Playlist & PlaylistTrack.
"""
from django.contrib import admin

from .models import Playlist, PlaylistTrack


class PlaylistTrackInline(admin.TabularInline):
    model = PlaylistTrack
    extra = 0
    ordering = ['position']
    readonly_fields = ['id', 'added_at']
    fields = ['position', 'track', 'added_at']


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'track_count', 'duration_formatted', 'created_at', 'updated_at',
    ]
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'total_duration', 'created_at', 'updated_at']
    inlines = [PlaylistTrackInline]
    fieldsets = (
        ('Informations', {
            'fields': ('id', 'name', 'description'),
        }),
        ('Calculé', {
            'fields': ('total_duration', 'created_at', 'updated_at'),
        }),
    )

    @admin.display(description='Pistes')
    def track_count(self, obj):
        return obj.tracks.count()
