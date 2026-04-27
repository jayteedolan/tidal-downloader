from pydantic import BaseModel
from typing import Optional


# ── Tidal models ────────────────────────────────────────────────────────────

class AlbumResult(BaseModel):
    id: int
    title: str
    artist: str
    cover_url: Optional[str] = None
    year: Optional[str] = None
    quality: str = "LOSSLESS"
    track_count: Optional[int] = None
    explicit: bool = False
    release_type: str = "ALBUM"   # "ALBUM" | "SINGLE" | "EP"


class TrackInfo(BaseModel):
    id: int
    title: str
    track_number: int
    disc_number: int = 1
    duration: Optional[int] = None  # seconds


class AlbumDetail(BaseModel):
    album: AlbumResult
    tracks: list[TrackInfo]


class TrackStream(BaseModel):
    track_id: int
    original_url: Optional[str] = None
    manifest: Optional[str] = None          # base64-encoded DASH MPD
    manifest_mime_type: Optional[str] = None
    quality: str = "LOSSLESS"


# ── URL resolver models ──────────────────────────────────────────────────────

class UrlResolveRequest(BaseModel):
    url: str


class UrlResolveResult(BaseModel):
    tidal_album_id: int
    source_platform: str  # "tidal", "spotify", "apple"
    album_title: Optional[str] = None
    artist: Optional[str] = None


# ── MusicBrainz models ───────────────────────────────────────────────────────

class ReleaseResult(BaseModel):
    id: str
    title: str
    artist: str
    date: Optional[str] = None
    country: Optional[str] = None
    label: Optional[str] = None
    track_count: Optional[int] = None
    disc_count: Optional[int] = None
    release_type: Optional[str] = None
    format: Optional[str] = None
    barcode: Optional[str] = None


class RecordingInfo(BaseModel):
    id: str
    title: str
    track_number: int
    disc_number: int = 1
    artist_credit: Optional[str] = None
    length: Optional[int] = None  # milliseconds


class ReleaseDetail(BaseModel):
    id: str
    title: str
    artist: str
    artist_id: Optional[str] = None
    date: Optional[str] = None
    country: Optional[str] = None
    label: Optional[str] = None
    track_count: Optional[int] = None
    disc_count: Optional[int] = None
    release_type: Optional[str] = None
    format: Optional[str] = None
    recordings: list[RecordingInfo] = []


# ── Library / folder models ──────────────────────────────────────────────────

class FolderMatch(BaseModel):
    name: str
    full_path: str
    score: float


# ── Download models ──────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    tidal_album_id: int
    mb_release_id: Optional[str] = None  # None = skip MusicBrainz, use Tidal metadata only
    dest_artist_folder: str   # full path to existing artist folder, or parent for new
    new_folder_name: Optional[str] = None  # if set, create this new artist folder
    track_ids: Optional[list[int]] = None  # Tidal track IDs to download; None = all tracks


class DownloadJobResponse(BaseModel):
    job_id: str


class DownloadProgress(BaseModel):
    job_id: str
    track_num: Optional[int] = None
    total_tracks: Optional[int] = None
    track_title: Optional[str] = None
    status: str  # "starting" | "downloading" | "tagging" | "done" | "error" | "complete"
    error: Optional[str] = None


# ── Tagging metadata (internal) ──────────────────────────────────────────────

class TrackMetadata(BaseModel):
    title: str
    artist: str
    album_artist: str
    album: str
    date: Optional[str] = None
    track_number: int
    total_tracks: int
    disc_number: int = 1
    total_discs: int = 1
    label: Optional[str] = None
    country: Optional[str] = None
    cover_url: Optional[str] = None
    musicbrainz_album_id: Optional[str] = None
    musicbrainz_track_id: Optional[str] = None
    musicbrainz_artist_id: Optional[str] = None
