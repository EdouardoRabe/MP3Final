/**
 * playlist-builder.js
 * Alpine.js component for the Playlist Generator page.
 * Handles filtering, DP generation, drag & drop reordering, track preview, and save.
 */
function playlistGenerator() {
    return {
        filters: {
            genre: [],
            artist: [],
            language: [],
            target_minutes: 45,
        },
        generating: false,
        replacing: false,
        saving: false,
        resultTracks: [],
        totalDuration: 0,
        algorithmMeta: null,
        error: '',

        selectedGenre: '',
        selectedArtist: '',
        selectedLanguage: '',

        previewIndex: null,
        previewPlaying: false,
        previewHowl: null,

        saveForm: { name: '', description: '' },
        saveError: '',

        saveModalInstance: null,
        sortableInstance: null,

        init() {
            this.saveModalInstance = new bootstrap.Modal(this.$refs.saveModal);
        },

        addTag(field) {
            const selectProp = 'selected' + field.charAt(0).toUpperCase() + field.slice(1);
            const value = this[selectProp];
            if (value && !this.filters[field].includes(value)) {
                this.filters[field].push(value);
            }
            this[selectProp] = '';
        },

        removeTag(field, index) {
            this.filters[field].splice(index, 1);
        },

        initSortable() {
            if (this.sortableInstance) {
                this.sortableInstance.destroy();
            }
            const el = this.$refs.sortableList;
            if (!el) return;
            this.sortableInstance = Sortable.create(el, {
                animation: 150,
                handle: '.bi-grip-vertical',
                onEnd: (evt) => {
                    const ids = [];
                    el.querySelectorAll('[data-id]').forEach(item => {
                        ids.push(item.getAttribute('data-id'));
                    });
                    const newOrder = ids
                        .map(id => this.resultTracks.find(t => t.id === id))
                        .filter(Boolean);
                    this.resultTracks = newOrder;
                },
            });
        },

        async generate() {
            this.generating = true;
            this.error = '';
            this.resultTracks = [];
            this.previewStop();

            const targetSeconds = this.filters.target_minutes * 60;

            try {
                const resp = await fetch('/api/playlists/generate/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': window.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        genre: this.filters.genre,
                        artist: this.filters.artist,
                        language: this.filters.language,
                        target_duration: targetSeconds,
                    }),
                });

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.error || err.detail || 'Erreur generation');
                }

                const data = await resp.json();
                this.resultTracks = data.tracks || [];
                this.totalDuration = data.total_duration || 0;
                this.algorithmMeta = data.algorithm_metadata || null;

                this.$nextTick(() => this.initSortable());
            } catch (e) {
                this.error = e.message;
            } finally {
                this.generating = false;
            }
        },

        removeTrack(index) {
            this.resultTracks.splice(index, 1);
            this.recalculateDuration();
        },

        async replaceTrack(index) {
            this.replacing = true;
            const excludeId = this.resultTracks[index]?.id;
            if (!excludeId) { this.replacing = false; return; }

            const excludeIds = this.resultTracks.map(t => t.id);

            try {
                const targetSeconds = this.filters.target_minutes * 60;
                const resp = await fetch('/api/playlists/generate/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': window.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        genre: this.filters.genre,
                        artist: this.filters.artist,
                        language: this.filters.language,
                        target_duration: targetSeconds,
                        exclude_ids: excludeIds,
                    }),
                });

                if (!resp.ok) throw new Error('Erreur remplacement');

                const data = await resp.json();
                const newTrack = (data.tracks || [])[0];
                if (newTrack) {
                    this.resultTracks[index] = newTrack;
                    this.recalculateDuration();
                } else {
                    window.showToast('Aucune alternative disponible.', 'warning');
                }
            } catch (e) {
                window.showToast('Erreur : ' + e.message, 'error');
            } finally {
                this.replacing = false;
            }
        },

        async refreshAll() {
            await this.generate();
        },

        recalculateDuration() {
            this.totalDuration = this.resultTracks.reduce(
                (sum, t) => sum + (t.duration || 0), 0
            );
        },

        previewStop() {
            if (this.previewHowl) {
                this.previewHowl.unload();
                this.previewHowl = null;
            }
            this.previewPlaying = false;
            this.previewIndex = null;
        },

        previewTrack(track, index) {
            if (this.previewIndex === index && this.previewPlaying) {
                this.previewStop();
                return;
            }
            this.previewStop();
            this.previewIndex = index;

            if (!track.play_url) return;

            this.previewHowl = new Howl({
                src: [track.play_url],
                html5: true,
                format: ['mp3'],
                onplay: () => { this.previewPlaying = true; },
                onend: () => { this.previewStop(); },
                onloaderror: () => { this.previewStop(); },
            });
            this.previewHowl.play();
        },

        openSaveModal() {
            this.saveForm = { name: '', description: '' };
            this.saveError = '';
            this.saveModalInstance.show();
        },

        async savePlaylist() {
            if (!this.saveForm.name.trim()) {
                this.saveError = 'Le nom est obligatoire.';
                return;
            }
            this.saving = true;
            this.saveError = '';

            const trackIds = this.resultTracks.map(t => t.id);

            try {
                const resp = await fetch('/api/playlists/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': window.getCsrfToken(),
                    },
                    body: JSON.stringify({
                        name: this.saveForm.name.trim(),
                        description: this.saveForm.description.trim(),
                        tracks: trackIds,
                    }),
                });

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(Object.values(err).flat().join(', ') || 'Erreur sauvegarde');
                }

                const playlist = await resp.json();
                this.saveModalInstance.hide();
                window.location.href = `/playlists/${playlist.id}/`;
            } catch (e) {
                this.saveError = e.message;
            } finally {
                this.saving = false;
            }
        },

        formatDuration(seconds) {
            if (!seconds || seconds <= 0) return '0:00';
            const s = Math.round(seconds);
            const m = Math.floor(s / 60);
            const sec = s % 60;
            return `${m}:${String(sec).padStart(2, '0')}`;
        },
    };
}
