/**
 * audio-player.js
 * Alpine.js component for the Playlist Detail page.
 * Full audio player with Howler.js: play/pause, prev/next, seek, volume.
 */
function audioPlayer() {
    return {
        playlistId: '',
        playlistTracks: [],
        playlistDurationFormatted: '0:00',
        currentIndex: -1,
        currentTrack: null,
        playing: false,
        currentTime: 0,
        seekPosition: 0,
        volume: 0.8,
        howl: null,
        updateInterval: null,

        init(playlistId) {
            this.playlistId = playlistId;
            var main = document.querySelector('.main-content');
            if (main) main.style.paddingBottom = '100px';
            const dataEl = document.getElementById('playlist-data');
            if (dataEl && dataEl.textContent.trim()) {
                try {
                    const data = JSON.parse(dataEl.textContent);
                    this.loadPlaylistData(data);
                } catch (e) {
                    console.error('Failed to parse playlist data:', e);
                    this.fetchPlaylist();
                }
            } else {
                this.fetchPlaylist();
            }
        },

        loadPlaylistData(data) {
            this.playlistTracks = data.tracks || [];
            this.playlistDurationFormatted = data.duration_formatted || '0:00';
            this.updateSummary();
        },

        updateSummary() {
            const countEl = document.getElementById('track-count');
            const durEl = document.getElementById('total-duration');
            if (countEl) countEl.textContent = this.playlistTracks.length;
            if (durEl) durEl.textContent = this.playlistDurationFormatted;
        },

        async fetchPlaylist() {
            try {
                const resp = await fetch(`/api/playlists/${this.playlistId}/`);
                if (!resp.ok) throw new Error('Playlist introuvable');
                const data = await resp.json();
                this.loadPlaylistData(data);
            } catch (e) {
                console.error('fetchPlaylist:', e);
            }
        },

        playTrack(index) {
            if (index < 0 || index >= this.playlistTracks.length) return;

            if (this.currentIndex === index) {
                this.togglePlay();
                return;
            }

            this.stopPlayback();

            const entry = this.playlistTracks[index];
            const track = entry.track_detail;
            if (!track || !track.play_url) return;

            this.currentIndex = index;
            this.currentTrack = track;
            this.seekPosition = 0;
            this.currentTime = 0;

            this.howl = new Howl({
                src: [track.play_url],
                html5: true,
                format: ['mp3'],
                volume: this.volume,
                onplay: () => {
                    this.playing = true;
                    this.startProgressUpdate();
                },
                onpause: () => {
                    this.playing = false;
                    this.stopProgressUpdate();
                },
                onend: () => {
                    this.stopProgressUpdate();
                    this.nextTrack();
                },
                onstop: () => {
                    this.playing = false;
                    this.stopProgressUpdate();
                },
                onloaderror: () => {
                    console.error('Failed to load audio:', track.play_url);
                    this.nextTrack();
                },
            });
            this.howl.play();
        },

        togglePlay() {
            if (!this.howl) return;
            if (this.howl.playing()) {
                this.howl.pause();
            } else {
                this.howl.play();
            }
        },

        nextTrack() {
            if (this.currentIndex < this.playlistTracks.length - 1) {
                this.playTrack(this.currentIndex + 1);
            } else {
                this.stopPlayback();
            }
        },

        prevTrack() {
            if (this.currentIndex > 0) {
                this.playTrack(this.currentIndex - 1);
            }
        },

        stopPlayback() {
            if (this.howl) {
                this.howl.unload();
                this.howl = null;
            }
            this.playing = false;
            this.currentIndex = -1;
            this.currentTrack = null;
            this.currentTime = 0;
            this.seekPosition = 0;
            this.stopProgressUpdate();
        },

        seekTo(seconds) {
            if (!this.howl) return;
            this.howl.seek(seconds);
            this.currentTime = seconds;
        },

        setVolume(val) {
            this.volume = val;
            if (this.howl) {
                this.howl.volume(val);
            }
        },

        startProgressUpdate() {
            this.stopProgressUpdate();
            this.updateInterval = setInterval(() => {
                if (this.howl && this.howl.playing()) {
                    this.currentTime = this.howl.seek() || 0;
                    this.seekPosition = this.currentTime;
                }
            }, 250);
        },

        stopProgressUpdate() {
            if (this.updateInterval) {
                clearInterval(this.updateInterval);
                this.updateInterval = null;
            }
        },

        async removeFromPlaylist(entry) {
            var self = this;
            window.showConfirm(
                'Enlever &laquo; ' + (entry.track_detail?.title || '') + ' &raquo; de cette collection ?',
                async function() {
                    try {
                        const resp = await fetch(
                            `/api/playlists/${self.playlistId}/tracks/${entry.track}/`,
                            { method: 'DELETE', headers: { 'X-CSRFToken': window.getCsrfToken() } }
                        );
                        if (!resp.ok) throw new Error('Erreur suppression');

                        const idx = self.playlistTracks.indexOf(entry);
                        if (idx === self.currentIndex) {
                            self.stopPlayback();
                        } else if (idx < self.currentIndex) {
                            self.currentIndex--;
                        }

                        self.playlistTracks.splice(idx, 1);
                        self.updateSummary();
                        window.showToast('Titre retire de la collection.');
                    } catch (e) {
                        window.showToast('Erreur : ' + e.message, 'error');
                    }
                }
            );
        },

        deletePlaylist(playlistId) {
            var self = this;
            window.showConfirm(
                'Supprimer definitivement cette collection ?',
                async function() {
                    try {
                        const resp = await fetch(`/api/playlists/${playlistId}/`, {
                            method: 'DELETE',
                            headers: { 'X-CSRFToken': window.getCsrfToken() },
                        });
                        if (!resp.ok) throw new Error('Erreur suppression');
                        self.stopPlayback();
                        window.location.href = '/playlists/';
                    } catch (e) {
                        window.showToast('Erreur : ' + e.message, 'error');
                    }
                }
            );
        },

        formatTime(seconds) {
            if (!seconds || seconds <= 0) return '0:00';
            const s = Math.round(seconds);
            const m = Math.floor(s / 60);
            const sec = s % 60;
            return `${m}:${String(sec).padStart(2, '0')}`;
        },
    };
}
