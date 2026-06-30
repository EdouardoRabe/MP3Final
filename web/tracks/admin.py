"""
Django Admin configuration for the Track model.
"""
from django.contrib import admin

from .models import Track


@admin.register(Track)
class TrackAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'artist', 'album', 'genre', 'language',
        'duration_formatted', 'file_size_display', 'created_at',
    ]
    list_filter = ['genre', 'artist', 'language', 'metadata_complete', 'created_at']
    search_fields = ['title', 'artist', 'album', 'original_filename']
    readonly_fields = [
        'id', 'file', 'file_size', 'file_hash',
        'created_at', 'updated_at',
    ]
    fieldsets = (
        ('Métadonnées', {
            'fields': ('title', 'artist', 'album', 'genre', 'language', 'original_filename'),
        }),
        ('Fichier', {
            'fields': ('file', 'file_size', 'file_hash'),
        }),
        ('Technique', {
            'fields': ('id', 'duration', 'metadata_complete', 'created_at', 'updated_at'),
        }),
    )

    @admin.display(description='Taille')
    def file_size_display(self, obj):
        """Human-readable file size."""
        size = obj.file_size
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / 1024 / 1024:.1f} MB"
