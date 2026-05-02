function musicApp() {
  return {
    // ── Step state ──────────────────────────────────────────────
    step: 1,  // 1=Search, 2=Tracks, 3=MusicBrainz, 4=Folder, 5=Download

    // ── Step 0: URL resolve ──────────────────────────────────────
    urlInput: '',
    urlLoading: false,
    urlError: '',
    urlStatus: '',
    _urlTimers: [],

    // ── Step 1: Tidal search ─────────────────────────────────────
    searchArtist: '',
    searchAlbum: '',
    searchLoading: false,
    searchError: '',
    searchStatus: '',
    searchResults: [],
    selectedAlbum: null,
    activeSearchTab: 'albums',   // 'albums' | 'singles'
    albumDetail: null,

    // ── Step 2: Track selection ───────────────────────────────────
    selectedTrackIds: [],   // array of Tidal track IDs (integers)

    // ── Step 3: MusicBrainz ──────────────────────────────────────
    mbLoading: false,
    mbError: '',
    mbResults: [],
    selectedRelease: null,
    mbSearchArtist: '',
    mbSearchAlbum: '',

    // ── Step 2: Album detail loading ──────────────────────────────
    albumDetailLoading: false,

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
    downloadStatus: '',   // current phase label shown while downloading
    trackStatuses: [],   // [{track_num, display_num, disc_num, title, status}]
    overwriteNeeded: false,
    overwritePath: '',
    _activeJobId: null,
    _es: null,
    _spotiflacUrl: null,      // set when Tidal proxy unavailable; triggers SpotiFLAC-only path
    _isMbTrackList: false,    // albumDetail populated from MB recordings (SpotiFLAC path)

    // ── Lifecycle ─────────────────────────────────────────────────

    init() {
      this.$watch('step', () => {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      });
      this._onVisibilityChange = () => {
        if (document.visibilityState === 'visible' && this._activeJobId) {
          this._reconnectSSE();
        }
      };
      document.addEventListener('visibilitychange', this._onVisibilityChange);
    },

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

    fmtLabel(f) {
      return { 'flac-hires': 'HI-RES', flac: 'FLAC', m4a: 'AAC' }[f] ?? f?.toUpperCase() ?? '';
    },

    fmtClass(f) {
      return { 'flac-hires': 'format-hires', flac: 'format-flac', m4a: 'format-aac' }[f] ?? 'format-flac';
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

    get filteredSearchResults() {
      if (this.activeSearchTab === 'albums') {
        return this.searchResults.filter(a =>
          a.release_type === 'ALBUM' || a.release_type === 'EP'
        );
      }
      return this.searchResults.filter(a => a.release_type === 'SINGLE');
    },

    get albumCount() {
      return this.searchResults.filter(a =>
        a.release_type === 'ALBUM' || a.release_type === 'EP'
      ).length;
    },

    get singleCount() {
      return this.searchResults.filter(a => a.release_type === 'SINGLE').length;
    },

    // ── URL resolve ───────────────────────────────────────────────

    _startUrlStatus() {
      this._urlTimers.forEach(t => clearTimeout(t));
      this._urlTimers = [];
      const steps = [
        [0,     'Resolving URL…'],
        [3000,  'Checking Tidal catalog…'],
        [12000, 'Tidal hosts are slow, still trying…'],
        [25000, 'Almost there, hang tight…'],
      ];
      steps.forEach(([delay, msg]) => {
        this._urlTimers.push(setTimeout(() => {
          if (this.urlLoading) this.urlStatus = msg;
        }, delay));
      });
    },

    _clearUrlStatus() {
      this._urlTimers.forEach(t => clearTimeout(t));
      this._urlTimers = [];
      this.urlStatus = '';
    },

    async resolveUrl() {
      if (!this.urlInput.trim()) return;
      this.urlLoading = true;
      this.urlError = '';
      this._startUrlStatus();
      try {
        const res = await fetch('/api/tidal/resolve-url', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: this.urlInput.trim() }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to resolve URL');

        if (data.tidal_album_id) {
          // Normal path: load album from Tidal and jump to step 1 pre-selected
          await this.loadAlbumById(data.tidal_album_id, data.artist || '', data.album_title || '');
          if (data.album_title) this.searchAlbum = data.album_title;
          if (data.artist) this.searchArtist = data.artist;
        } else if (data.spotify_url) {
          // SpotiFLAC fallback: Tidal proxy unavailable, download direct from Spotify
          this._spotiflacUrl = data.spotify_url;
          this._isMbTrackList = false;
          this.selectedAlbum = {
            id: null,
            title: data.album_title || 'Unknown Album',
            artist: data.artist || '',
            cover_url: null,
            year: null,
            quality: 'FLAC',
            release_type: 'SINGLE',
          };
          this.albumDetail = null;
          this.albumDetailLoading = true;
          this.selectedTrackIds = [];
          this.folderQuery = data.artist || '';
          this.newFolderName = data.artist || '';
          this.mbSearchArtist = data.artist || '';
          this.mbSearchAlbum = data.album_title || '';
          // Show track selection first; MB search runs in background and
          // auto-populates the list when its top result loads
          this.step = 2;
          this.searchMusicBrainz();
        } else {
          throw new Error('Could not resolve this URL');
        }
        this.urlInput = '';
      } catch (e) {
        this.urlError = e.message;
      } finally {
        this.urlLoading = false;
        this._clearUrlStatus();
      }
    },

    async loadAlbumById(albumId, artist = '', title = '') {
      this.searchLoading = true;
      this.searchError = '';
      try {
        const params = new URLSearchParams();
        if (artist) params.set('artist', artist);
        if (title) params.set('title', title);
        const qs = params.toString() ? '?' + params : '';
        const res = await fetch(`/api/tidal/album/${albumId}${qs}`);
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
      this.searchStatus = 'Searching Tidal catalog…';
      this.searchResults = [];
      this.selectedAlbum = null;
      this.activeSearchTab = 'albums';
      try {
        const params = new URLSearchParams();
        if (this.searchArtist) params.set('artist', this.searchArtist);
        if (this.searchAlbum) params.set('album', this.searchAlbum);
        if (!params.toString()) params.set('q', q);
        const res = await fetch(`/api/tidal/search?${params}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Search failed');
        this.searchResults = data;
        if (!data.length) this.searchError = 'No results found.';
      } catch (e) {
        this.searchError = e.message;
      } finally {
        this.searchLoading = false;
        this.searchStatus = '';
      }
    },

    async selectAlbum(album, detail = null) {
      this.selectedAlbum = album;
      this.albumDetail = detail;
      this.folderQuery = album.artist || '';
      this.newFolderName = album.artist || '';
      this.selectedTrackIds = [];
      this.mbSearchArtist = album.artist || '';
      this.mbSearchAlbum = album.title || '';
      this.step = 2;
      // Kick off MB search in background so step 3 loads instantly
      this.searchMusicBrainz();

      if (detail) {
        this.selectedTrackIds = (detail.tracks || []).map(t => t.id);
      } else {
        // Load album detail (pass artist/title so backend can use search fallback)
        this.albumDetailLoading = true;
        const artistEnc = encodeURIComponent(album.artist || '');
        const titleEnc = encodeURIComponent(album.title || '');
        const qs = `?artist=${artistEnc}&title=${titleEnc}`;
        try {
          const res = await fetch(`/api/tidal/album/${album.id}${qs}`);
          const d = await res.json();
          if (res.ok && d.tracks?.length > 0) {
            this.albumDetail = d;
            this.selectedTrackIds = d.tracks.map(t => t.id);
          } else if (!res.ok) {
            throw new Error(d.detail || 'Failed to load album tracks');
          }
        } catch (e) {
          this.searchError = e.message;
        }
        this.albumDetailLoading = false;
      }
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
      if (!this.mbSearchArtist && !this.mbSearchAlbum) return;
      this.mbLoading = true;
      this.mbError = '';
      this.mbResults = [];
      this.selectedRelease = null;
      const artist = encodeURIComponent(this.mbSearchArtist);
      const album = encodeURIComponent(this.mbSearchAlbum);
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

        // SpotiFLAC path: auto-load track list from the top MB result
        if (this._spotiflacUrl && this.step === 2 && data.length > 0) {
          const top = data[0];
          this.selectedRelease = top;
          if (top.track_count !== 1) {
            await this._loadMbTracksForSpotiflac(top.id);
          } else {
            this.albumDetailLoading = false;
          }
        }
      } catch (e) {
        this.mbError = e.message;
        if (this._spotiflacUrl && this.step === 2) this.albumDetailLoading = false;
      } finally {
        this.mbLoading = false;
      }
    },

    selectRelease(release) {
      const prevId = this.selectedRelease?.id;
      this.selectedRelease = release;
      if (this._spotiflacUrl) {
        if (this._isMbTrackList && release.id === prevId) {
          // User confirmed the release whose tracks are already loaded → folder
          this.step = 4;
          this.searchFolders();
        } else if (release.track_count === 1) {
          this.step = 4;
          this.searchFolders();
        } else {
          // Different release selected — reload track list then return to step 2
          this._loadMbTracksForSpotiflac(release.id).then(() => { this.step = 2; });
        }
      } else {
        this.step = 4;
        this.searchFolders();
      }
    },

    async _loadMbTracksForSpotiflac(mbId) {
      this.albumDetailLoading = true;
      this._isMbTrackList = false;
      try {
        const res = await fetch(`/api/musicbrainz/release/${mbId}`);
        const detail = await res.json();
        if (!res.ok) throw new Error(detail.detail || 'Failed to load tracks');
        const sorted = [...detail.recordings].sort((a, b) =>
          a.disc_number !== b.disc_number
            ? a.disc_number - b.disc_number
            : a.track_number - b.track_number
        );
        this.albumDetail = {
          tracks: sorted.map((rec, i) => ({
            id: i + 1,
            track_number: rec.track_number,
            disc_number: rec.disc_number,
            title: rec.title,
          })),
        };
        this.selectedTrackIds = this.albumDetail.tracks.map(t => t.id);
        this._isMbTrackList = true;
      } catch (_) {
        // Couldn't fetch track list — user can still continue (downloads all)
        this.albumDetail = null;
        this._isMbTrackList = false;
      } finally {
        this.albumDetailLoading = false;
      }
    },

    skipMbStep() {
      this.mbLoading = false;
      this.selectedRelease = null;
      this._isMbTrackList = false;
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
        // Auto-select and skip to confirmation if there's a perfect name match
        const perfect = data.find(f => f.score === 100);
        if (perfect) {
          this.selectedFolder = perfect;
          this.createNewFolder = false;
          this.step = 5;
        }
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
      const hasDestination = this.selectedFolder || (this.createNewFolder && this.newFolderName.trim());
      const hasTracks = this.selectedTrackIds.length > 0 || this._spotiflacUrl;
      return this.selectedAlbum && hasDestination && hasTracks;
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
        format: null,
        source_fmt: null,
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
        tidal_album_id: this.selectedAlbum.id ?? null,
        spotify_url: this._spotiflacUrl ?? null,
        album_title: this.selectedAlbum.title ?? null,
        mb_release_id: this.selectedRelease?.id ?? null,
        dest_artist_folder: this.selectedFolder ? this.selectedFolder.full_path : '',
        new_folder_name: this.createNewFolder ? this.newFolderName.trim() : null,
        track_ids: (!this._isMbTrackList && this.selectedTrackIds.length) ? this.selectedTrackIds : null,
        spotiflac_track_positions: (this._isMbTrackList && this.selectedTrackIds.length) ? this.selectedTrackIds : null,
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
      this._activeJobId = jobId;
      this._connectSSE(jobId);
    },

    _connectSSE(jobId) {
      if (this._es) { this._es.close(); this._es = null; }
      const es = new EventSource(`/api/download/${jobId}/stream`);
      this._es = es;
      es.addEventListener('progress', (e) => {
        this._applyProgress(JSON.parse(e.data));
      });
      es.onerror = () => {
        this._es = null;
        es.close();
        // If hidden: do nothing — visibilitychange will reconnect when user returns
        // If visible and connection closed: probe to distinguish job-done from error
        if (document.visibilityState === 'visible' && es.readyState === EventSource.CLOSED) {
          this._handleSSEClosed();
        }
      };
    },

    async _reconnectSSE() {
      if (!this._activeJobId || !this.downloading) return;
      await new Promise(r => setTimeout(r, 300)); // let iOS fully resume
      this._connectSSE(this._activeJobId);
    },

    async _handleSSEClosed() {
      if (!this._activeJobId) return;
      try {
        const res = await fetch(`/api/download/${this._activeJobId}/stream`, {
          signal: AbortSignal.timeout(5000),
        });
        if (res.status === 404) {
          // Job completed while we were away
          this.downloadComplete = true;
          this.downloading = false;
          this._cleanup();
        } else if (res.ok) {
          // Job still running — reconnect properly via EventSource
          this._connectSSE(this._activeJobId);
        } else {
          this.downloadError = `Connection lost (HTTP ${res.status})`;
          this.downloading = false;
          this._cleanup();
        }
      } catch (_) { /* network error — visibilitychange will retry */ }
    },

    _cleanup() {
      if (this._es) { this._es.close(); this._es = null; }
      this._activeJobId = null;
    },

    _applyProgress(msg) {
      if (msg.status === 'complete') {
        this.downloadComplete = true;
        this.downloading = false;
        this.downloadStatus = '';
        this._cleanup();
        return;
      }

      if (msg.status === 'error' && !msg.track_num) {
        // Top-level error
        this.downloadError = msg.error || 'Unknown error';
        this.downloading = false;
        this.downloadStatus = '';
        this._cleanup();
        return;
      }

      // Phase label updates (no track_num — just a status string)
      if (msg.track_num == null && msg.track_title) {
        this.downloadStatus = msg.track_title;
      }

      if (msg.track_num != null) {
        let idx = this.trackStatuses.findIndex(t => t.track_num === msg.track_num);
        if (idx === -1) {
          // SpotiFLAC path: create entry dynamically
          this.trackStatuses.push({
            track_num: msg.track_num,
            display_num: msg.track_num,
            disc_num: 1,
            title: msg.track_title || `Track ${msg.track_num}`,
            status: 'queued',
            error: null,
            format: null,
            source_fmt: null,
          });
          idx = this.trackStatuses.length - 1;
        }
        this.trackStatuses[idx].status = msg.status;
        if (msg.error) this.trackStatuses[idx].error = msg.error;
        if (msg.track_title) this.trackStatuses[idx].title = msg.track_title;
        if (msg.format) this.trackStatuses[idx].format = msg.format;
        if (msg.source_format) this.trackStatuses[idx].source_fmt = msg.source_format;
      }
    },

    // ── Reset ─────────────────────────────────────────────────────
    resetAll() {
      this._cleanup();
      this.step = 1;
      this._clearUrlStatus();
      this.urlInput = '';
      this.urlError = '';
      this.searchArtist = '';
      this.searchAlbum = '';
      this.searchResults = [];
      this.activeSearchTab = 'albums';
      this.searchError = '';
      this.selectedAlbum = null;
      this.albumDetail = null;
      this.albumDetailLoading = false;
      this.selectedTrackIds = [];
      this.mbResults = [];
      this.mbError = '';
      this.mbSearchArtist = '';
      this.mbSearchAlbum = '';
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
      this.downloadStatus = '';
      this.trackStatuses = [];
      this.overwriteNeeded = false;
      this.overwritePath = '';
      this._spotiflacUrl = null;
      this._isMbTrackList = false;
    },
  };
}
