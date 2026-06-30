/**
 * track-manager.js
 * Alpine.js component for the Track list page with DataTable, CRUD modals.
 */
function trackManager() {
    return {
        tracks: [],
        loading: false,
        saving: false,
        search: '',
        filterGenre: '',
        filterArtist: '',
        filterLanguage: '',
        sortField: 'title',
        sortOrder: 'asc',
        currentPage: 1,
        pageSize: 25,
        totalCount: 0,
        totalPages: 1,

        editingTrack: null,
        selectedFile: null,
        form: { title: '', artist: '', album: '', genre: '', language: '', duration: '' },
        errorMsg: '',

        deleteTarget: null,

        trackModalInstance: null,
        deleteModalInstance: null,

        get visiblePages() {
            const pages = [];
            const start = Math.max(1, this.currentPage - 2);
            const end = Math.min(this.totalPages, this.currentPage + 2);
            for (let i = start; i <= end; i++) pages.push(i);
            return pages;
        },

        init() {
            this.fetchTracks();
            this.trackModalInstance = new bootstrap.Modal(this.$refs.trackModal);
            this.deleteModalInstance = new bootstrap.Modal(this.$refs.deleteModal);
        },

        async fetchTracks() {
            this.loading = true;
            const params = new URLSearchParams({
                page: this.currentPage,
                page_size: this.pageSize,
                ordering: (this.sortOrder === 'desc' ? '-' : '') + this.sortField,
            });
            if (this.search) params.set('search', this.search);
            if (this.filterGenre) params.set('genre', this.filterGenre);
            if (this.filterArtist) params.set('artist', this.filterArtist);
            if (this.filterLanguage) params.set('language', this.filterLanguage);

            try {
                const resp = await fetch(`/api/tracks/?${params}`);
                if (!resp.ok) throw new Error('Erreur reseau');
                const data = await resp.json();
                this.tracks = data.results || [];
                this.totalCount = data.count || 0;
                this.totalPages = Math.ceil(this.totalCount / this.pageSize) || 1;
            } catch (e) {
                console.error('fetchTracks:', e);
                this.tracks = [];
            } finally {
                this.loading = false;
            }
        },

        sortBy(field) {
            if (this.sortField === field) {
                this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
            } else {
                this.sortField = field;
                this.sortOrder = 'asc';
            }
            this.fetchTracks();
        },

        goToPage(page) {
            if (page < 1 || page > this.totalPages) return;
            this.currentPage = page;
            this.fetchTracks();
        },

        resetFilters() {
            this.search = '';
            this.filterGenre = '';
            this.filterArtist = '';
            this.filterLanguage = '';
            this.sortField = 'title';
            this.sortOrder = 'asc';
            this.currentPage = 1;
            this.fetchTracks();
        },

        openCreateModal() {
            this.editingTrack = null;
            this.selectedFile = null;
            this.form = { title: '', artist: '', album: '', genre: '', language: '', duration: '' };
            this.errorMsg = '';
            this.trackModalInstance.show();
        },

        openEditModal(track) {
            this.editingTrack = track;
            this.selectedFile = null;
            this.form = {
                title: track.title || '',
                artist: track.artist || '',
                album: track.album || '',
                genre: track.genre || '',
                language: track.language || '',
            };
            this.errorMsg = '';
            this.trackModalInstance.show();
        },

        onFileSelected(event) {
            this.selectedFile = event.target.files[0] || null;
        },

        async saveTrack() {
            this.saving = true;
            this.errorMsg = '';

            if (this.editingTrack) {
                try {
                    const resp = await fetch(`/api/tracks/${this.editingTrack.id}/`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(this.form),
                    });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        throw new Error(Object.values(err).flat().join(', ') || 'Erreur serveur');
                    }
                    this.trackModalInstance.hide();
                    this.fetchTracks();
                    window.showToast('Morceau mis a jour.');
                } catch (e) {
                    this.errorMsg = e.message;
                }
            } else {
                if (!this.selectedFile) {
                    this.errorMsg = 'Selectionne un fichier audio avant d\'importer.';
                    this.saving = false;
                    return;
                }
                try {
                    const fd = new FormData();
                    fd.append('file', this.selectedFile);
                    fd.append('title', this.form.title || 'Sans titre');
                    fd.append('artist', this.form.artist || 'Inconnu');
                    fd.append('album', this.form.album || '');
                    fd.append('genre', this.form.genre || '');
                    fd.append('language', this.form.language || '');
                    fd.append('duration', this.form.duration || '0');
                    fd.append('original_filename', this.selectedFile.name);

                    const resp = await fetch('/api/tracks/', {
                        method: 'POST',
                        body: fd,
                    });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        throw new Error(Object.values(err).flat().join(', ') || 'Erreur upload');
                    }
                    this.trackModalInstance.hide();
                    this.fetchTracks();
                    window.showToast('Morceau importe avec succes.');
                } catch (e) {
                    this.errorMsg = e.message;
                }
            }
            this.saving = false;
        },

        confirmDelete(track) {
            this.deleteTarget = track;
            this.deleteModalInstance.show();
        },

        async deleteTrack() {
            if (!this.deleteTarget) return;
            try {
                const resp = await fetch(`/api/tracks/${this.deleteTarget.id}/`, {
                    method: 'DELETE',
                });
                if (!resp.ok) throw new Error('Erreur suppression');
                this.deleteModalInstance.hide();
                this.deleteTarget = null;
                this.fetchTracks();
                window.showToast('Morceau retire.');
            } catch (e) {
                window.showToast('Erreur lors de la suppression : ' + e.message, 'error');
            }
        },
    };
}
