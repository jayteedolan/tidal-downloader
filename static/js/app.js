function musicApp() {
  return {
    // ── Step state ──────────────────────────────────────────────
    step: 1,  // 1=Search, 2=Tracks, 3=MusicBrainz, 4=Folder, 5=Download

    // ── Step 0: URL resolve ──────────────────────────────────────
    urlInput: '',
    urlLoading: false,
    urlError: '',

    // ── Step 1: Tidal search ─────────────────────────────────────
    searchArtist: '',
    searchAlbum: '',
    searchLoading: false,
    searchError: '',
    searchResults: [],
    selectedAlbum: null,
    albumDetail: null,

    // ── Step 2: Track selection ───────────────────────────────────
    selectedTrackIds: [],   // array of Tidal track IDs (integers)

    // ── Step 3: MusicBrainz ──────────────────────────────────────
    mbLoading: false,
    mbError: '',
    mbResults: [],
    selectedRelease: null,

    // ── Step 4: Folder ───────────────────────────────────────────
    folderQuery: '',
    folderLoading: false,
    folderError: '',
    folderResults: [],
    selectedFolder: null,
    createNewFolder: false,
    newFolderName: '',

    // ── Step 5: Download ─────────────────────────────────────────
    downloading: false,
    downloadComplete: false,
    downloadError: '',
    trackStatuses: [],   // [{track_num, display_num, disc_num, title, status}]
    overwriteNeeded: false,
    overwritePath: '',

    // ── Helpers ───────────────────────────────────────────────────

    stepLabel(n) {
      return ['Tidal Album', 'Tracks', 'MusicBrainz', 'Destination', 'Download'][n - 1];
    },

    qualityBadgeClass(q) {
      if (!q) return 'badge-flac';
      const u = q.toUpperCase();
      if (u.includes('HI') || u.includes('RES')) return 'badge-hires';
      return 'badge-flac';
    },

    statusIcon(s) {
      return {
        queued: '○',
        downloading: '↓',
        tagging: '✎',
        done: '✓',
        error: '✗',
        starting: '…',
      }[s] ?? '○';
    },

    statusClass(s) {
      return `status-${s || 'queued'}`;
    },

    // ── URL resolve ───────────────────────────────────────────────

    async resolveUrl() {
      if (!this.urlInput.trim()) return;
      this.urlLoading = true;
      this.urlError = '';
      try {
        const res = await fetch('/api/tidal/resolve-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: this.urlInput.trim() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to resolve URL');

        // Load the album directly and jump to step 1 pre-selected
        await this.loadAlbumById(data.tidal_album_id);
        if (data.album_title) this.searchAlbum = data.album_title;
        if (data.artist) this.searchArtist = data.artist;
        this.urlInput = '';
      } catch (e) {
        this.urlError = e.message;
      } finally {
        this.urlLoading = false;
      }
    },

    async loadAlbumById(albumId) {
      this.searchLoading = true;
      this.searchError = '';
      try {
        const res = await fetch(`/api/tidal/album/${albumId}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to load album');
        // Wrap as a single search result
        this.searchResults = [data.album];
        this.selectAlbum(data.album, data);
      } catch (e) {
        this.searchError = e.message;
      } finally {
        this.searchLoading = false;
      }
    },

    // ── Step 1: Tidal search ──────────────────────────────────────

    async searchTidal() {
      const q = [this.searchArtist, this.searchAlbum].filter(Boolean).join(' ');
      if (!q) return;
      this.searchLoading = true;
      this.searchError = '';
      this.searchResults = [];
      this.selectedAlbum = null;
      try {
        const res = await fetch(`/api/tidal/search?q=${encodeURIComponent(q)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Search failed');
        this.searchResults = data;
        if (!data.length) this.searchError = 'No results found.';
      } catch (e) {
        this.searchError = e.message;
      } finally {
        this.searchLoading = false;
      }
    },

    async selectAlbum(album, detail = null) {
      this.selectedAlbum = album;
      this.albumDetail = detail;
      if (!detail) {
        try {
          const res = await fetch(`/api/tidal/album/${album.id}`);
          const d = await res.json();
          if (res.ok) this.albumDetail = d;
        } catch (_) {}
      }
      // Pre-fill folder search with artist name
      this.folderQuery = album.artist || '';
      this.newFolderName = album.artist || '';
      // Pre-select all tracks
      this.selectedTrackIds = (this.albumDetail?.tracks || []).map(t => t.id);
      // Advance to track selection step
      this.step = 2;
      // Kick off MB search in background so step 3 loads instantly
      this.searchMusicBrainz();
    },

    // ── Step 2: Track selection ───────────────────────────────────

    get allTracksSelected() {
      const tracks = this.albumDetail?.tracks || [];
      return tracks.length > 0 && this.selectedTrackIds.length === tracks.length;
    },

    get someTracksSelected() {
      return this.selectedTrackIds.length > 0 && !this.allTracksSelected;
    },

    toggleTrack(trackId) {
      const idx = this.selectedTrackIds.indexOf(trackId);
      if (idx === -1) this.selectedTrackIds.push(trackId);
      else this.selectedTrackIds.splice(idx, 1);
    },

    toggleAllTracks() {
      const tracks = this.albumDetail?.tracks || [];
      if (this.allTracksSelected) {
        this.selectedTrackIds = [];
      } else {
        this.selectedTrackIds = tracks.map(t => t.id);
      }
    },

    trackDisplayNum(t) {
      const totalDiscs = Math.max(...(this.albumDetail?.tracks || []).map(x => x.disc_number), 1);
      if (totalDiscs > 1) {
        return `${t.disc_number}-${String(t.track_number).padStart(2, '0')}`;
      }
      return String(t.track_number);
    },

    confirmTrackSelection() {
      if (!this.selectedTrackIds.length) return;
      this.step = 3;
    },

    // ── Step 3: MusicBrainz ───────────────────────────────────────

    async searchMusicBrainz() {
      if (!this.selectedAlbum) return;
      this.mbLoading = true;
      this.mbError = '';
      this.mbResults = [];
      this.selectedRelease = null;
      const artist = encodeURIComponent(this.selectedAlbum.artist || '');
      const album = encodeURIComponent(this.selectedAlbum.title || '');
      try {
        const res = await fetch(`/api/musicbrainz/search?artist=${artist}&album=${album}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'MusicBrainz search failed');
        const targetYear = this.selectedAlbum.year ? String(this.selectedAlbum.year).slice(0, 4) : null;
        data.sort((a, b) => {
          const score = r => {
            let s = 0;
            if (r.format === 'Digital Media') s += 2;
            if (targetYear && r.date && String(r.date).startsWith(targetYear)) s += 1;
            return s;
          };
          return score(b) - score(a);
        });
        this.mbResults = data;
        if (!data.length) this.mbError = 'No MusicBrainz releases found.';
      } catch (e) {
        this.mbError = e.message;
      } finally {
        this.mbLoading = false;
      }
    },

    selectRelease(release) {
      this.selectedRelease = release;
      this.step = 4;
      this.searchFolders();
    },

    mbMeta(r) {
      const parts = [];
      if (r.date) parts.push(r.date);
      if (r.country) parts.push(r.country);
      if (r.label) parts.push(r.label);
      if (r.format) parts.push(r.format);
      const discs = r.disc_count > 1 ? `${r.disc_count} discs` : null;
      if (discs) parts.push(discs);
      return parts.join(' · ');
    },

    // ── Step 4: Folders ───────────────────────────────────────────

    async searchFolders() {
      if (!this.folderQuery.trim()) return;
      this.folderLoading = true;
      this.folderError = '';
      this.folderResults = [];
      this.selectedFolder = null;
      this.createNewFolder = false;
      try {
        const res = await fetch(`/api/library/folders?q=${encodeURIComponent(this.folderQuery)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Folder search failed');
        this.folderResults = data;
      } catch (e) {
        this.folderError = e.message;
      } finally {
        this.folderLoading = false;
      }
    },

    selectFolder(folder) {
      this.selectedFolder = folder;
      this.createNewFolder = false;
    },

    selectNewFolder() {
      this.selectedFolder = null;
      this.createNewFolder = true;
    },

    confirmFolder() {
      if (!this.selectedFolder && !this.createNewFolder) return;
      if (this.createNewFolder && !this.newFolderName.trim()) return;
      this.step = 5;
    },

    get destDescription() {
      if (this.createNewFolder) return `New folder: ${this.newFolderName}`;
      if (this.selectedFolder) return this.selectedFolder.name;
      return '—';
    },

    get albumFolderPreview() {
      const year = this.selectedRelease?.date?.slice(0, 4)
        || this.selectedAlbum?.year
        || '';
      const title = this.selectedRelease?.title || this.selectedAlbum?.title || '';
      return year ? `${title} (${year})` : title;
    },

    // ── Step 5: Download ──────────────────────────────────────────

    get canDownload() {
      return this.selectedAlbum && this.selectedRelease &&
        (this.selectedFolder || (this.createNewFolder && this.newFolderName.trim())) &&
        this.selectedTrackIds.length > 0;
    },

    initTrackStatuses() {
      const tracks = (this.albumDetail?.tracks || [])
        .filter(t => this.selectedTrackIds.includes(t.id));
      this.trackStatuses = tracks.map((t, idx) => ({
        track_num: idx + 1,          // sequential index matching backend emit order
        display_num: t.track_number, // actual album track number for display
        disc_num: t.disc_number,
        title: t.title,
        status: 'queued',
        error: null,
      }));
    },

    async startDownload() {
      if (!this.canDownload) return;
      this.downloadError = '';
      this.overwriteNeeded = false;
      this.overwritePath = '';

      // Check if the album folder already exists and is non-empty
      const year = this.selectedRelease?.date?.slice(0, 4) || this.selectedAlbum?.year || '';
      const albumTitle = this.selectedRelease?.title || this.selectedAlbum?.title || '';
      const params = new URLSearchParams({
        album_title: albumTitle,
        year,
        dest_artist_folder: this.selectedFolder ? this.selectedFolder.full_path : '',
        new_folder_name: this.createNewFolder ? this.newFolderName.trim() : '',
      });
      try {
        const checkRes = await fetch(`/api/library/album-folder-exists?${params}`);
        if (checkRes.ok) {
          const checkData = await checkRes.json();
          if (checkData.non_empty) {
            this.overwriteNeeded = true;
            this.overwritePath = checkData.folder_path;
            return; // wait for user confirmation
          }
        }
      } catch (_) { /* if check fails, proceed anyway */ }

      await this._doDownload();
    },

    async confirmOverwrite() {
      this.overwriteNeeded = false;
      this.overwritePath = '';
      await this._doDownload();
    },

    cancelOverwrite() {
      this.overwriteNeeded = false;
      this.overwritePath = '';
    },

    async _doDownload() {
      this.downloading = true;
      this.downloadComplete = false;
      this.downloadError = '';
      this.initTrackStatuses();

      const body = {
        tidal_album_id: this.selectedAlbum.id,
        mb_release_id: this.selectedRelease.id,
        dest_artist_folder: this.selectedFolder ? this.selectedFolder.full_path : '',
        new_folder_name: this.createNewFolder ? this.newFolderName.trim() : null,
        track_ids: this.selectedTrackIds,
      };

      let jobId;
      try {
        const res = await fetch('/api/download', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to start download');
        jobId = data.job_id;
      } catch (e) {
        this.downloadError = e.message;
        this.downloading = false;
        return;
      }

      // Connect SSE
      const es = new EventSource(`/api/download/${jobId}/stream`);

      es.addEventListener('progress', (e) => {
        const msg = JSON.parse(e.data);
        this._applyProgress(msg);
      });

      es.onerror = () => {
        es.close();
        this.downloading = false;
      };
    },

    _applyProgress(msg) {
      if (msg.status === 'complete') {
        this.downloadComplete = true;
        this.downloading = false;
        return;
      }

      if (msg.status === 'error' && !msg.track_num) {
        // Top-level error
        this.downloadError = msg.error || 'Unknown error';
        this.downloading = false;
        return;
      }

      if (msg.track_num != null) {
        const idx = this.trackStatuses.findIndex(t => t.track_num === msg.track_num);
        if (idx !== -1) {
          this.trackStatuses[idx].status = msg.status;
          if (msg.error) this.trackStatuses[idx].error = msg.error;
          if (msg.track_title) this.trackStatuses[idx].title = msg.track_title;
        }
      }
    },

    // ── Reset ─────────────────────────────────────────────────────
    resetAll() {
      this.step = 1;
      this.urlInput = '';
      this.urlError = '';
      this.searchArtist = '';
      this.searchAlbum = '';
      this.searchResults = [];
      this.searchError = '';
      this.selectedAlbum = null;
      this.albumDetail = null;
      this.selectedTrackIds = [];
      this.mbResults = [];
      this.mbError = '';
      this.selectedRelease = null;
      this.folderQuery = '';
      this.folderResults = [];
      this.folderError = '';
      this.selectedFolder = null;
      this.createNewFolder = false;
      this.newFolderName = '';
      this.downloading = false;
      this.downloadComplete = false;
      this.downloadError = '';
      this.trackStatuses = [];
      this.overwriteNeeded = false;
      this.overwritePath = '';
    },
  };
}
