import httpx
import asyncio
import logging
from typing import Optional
from ..models import AlbumResult, AlbumDetail, TrackInfo, TrackStream

logger = logging.getLogger(__name__)

TIDAL_HOSTS = [
    "triton.squid.wtf",
    "hifi-one.spotisaver.net",
    "hifi-two.spotisaver.net",
    "tidal.kinoplus.online",
    "hund.qqdl.site",
    "katze.qqdl.site",
    "maus.qqdl.site",
    "vogel.qqdl.site",
    "wolf.qqdl.site",
    "arran.monochrome.tf",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TidalDownloader/1.0)",
    "X-Client": "TidalDownloader/1.0",
}

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def _cover_url(cover_id: Optional[str], size: int = 640) -> Optional[str]:
    if not cover_id:
        return None
    path = cover_id.replace("-", "/")
    return f"https://resources.tidal.com/images/{path}/{size}x{size}.jpg"


def _parse_album(data: dict) -> AlbumResult:
    artist_name = ""
    if isinstance(data.get("artist"), dict):
        artist_name = data["artist"].get("name", "")
    elif isinstance(data.get("artists"), list) and data["artists"]:
        artist_name = data["artists"][0].get("name", "")

    release_date = data.get("releaseDate") or data.get("streamStartDate") or ""
    year = release_date[:4] if release_date else None

    quality = data.get("audioQuality", "LOSSLESS")
    if quality == "HI_RES_LOSSLESS":
        quality = "HI-RES"
    elif quality == "LOSSLESS":
        quality = "FLAC"

    return AlbumResult(
        id=data["id"],
        title=data.get("title", ""),
        artist=artist_name,
        cover_url=_cover_url(data.get("cover")),
        year=year,
        quality=quality,
        track_count=data.get("numberOfTracks"),
    )


def _parse_track(item: dict) -> TrackInfo:
    # Handle both flat track dict and wrapped {item: ...} format
    track = item.get("item", item)
    return TrackInfo(
        id=track["id"],
        title=track.get("title", ""),
        track_number=track.get("trackNumber", 1),
        disc_number=track.get("volumeNumber", 1),
        duration=track.get("duration"),
    )


async def _get(path: str, params: dict | None = None) -> dict:
    last_error: Exception | None = None
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        for host in TIDAL_HOSTS:
            url = f"https://{host}{path}"
            try:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    return resp.json()
                last_error = Exception(f"HTTP {resp.status_code} from {host}")
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as e:
                last_error = e
                continue
    raise RuntimeError(f"All Tidal hosts failed. Last error: {last_error}")


async def search_albums(query: str) -> list[AlbumResult]:
    data = await _get("/search/", params={"al": query})

    logger.debug("search_albums response type=%s keys=%s",
                 type(data).__name__,
                 list(data.keys()) if isinstance(data, dict) else "N/A")

    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        # Try top-level items
        items = data.get("items") or []
        # Try data.items
        if not items:
            items = data.get("data", {}).get("items") or []
        # Try albums key (dict or list)
        if not items and "albums" in data:
            albums_data = data["albums"]
            if isinstance(albums_data, dict):
                items = albums_data.get("items", [])
            elif isinstance(albums_data, list):
                items = albums_data
        # Try nested data.albums
        if not items:
            data_inner = data.get("data") or {}
            if isinstance(data_inner, dict) and "albums" in data_inner:
                albums_data = data_inner["albums"]
                if isinstance(albums_data, dict):
                    items = albums_data.get("items", [])
                elif isinstance(albums_data, list):
                    items = albums_data

    logger.debug("search_albums found %d raw items", len(items))

    seen_ids: set[int] = set()
    results = []
    for item in items:
        album_data = item.get("item", item)
        # If the item is a track (has trackNumber), pull out its embedded album object
        if "trackNumber" in album_data or "volumeNumber" in album_data:
            album_data = album_data.get("album", album_data)
        try:
            parsed = _parse_album(album_data)
            if parsed.id not in seen_ids:
                seen_ids.add(parsed.id)
                results.append(parsed)
        except (KeyError, TypeError):
            continue
    return results


async def get_album(album_id: int) -> AlbumDetail:
    data = await _get("/album/", params={"id": album_id})

    logger.debug("get_album response type=%s keys=%s",
                 type(data).__name__,
                 list(data.keys()) if isinstance(data, dict) else "N/A")

    album_data = {}
    tracks_raw = []

    if isinstance(data, dict) and "data" in data:
        inner = data["data"]
        if isinstance(inner, dict):
            if "title" in inner:
                # inner IS the album object; "items" or "tracks" within it are the track list
                album_data = inner
                raw = inner.get("tracks") or inner.get("items") or data.get("tracks") or []
                if isinstance(raw, dict):
                    tracks_raw = raw.get("items", [])
                else:
                    tracks_raw = raw
            elif "items" in inner:
                # items[0] is the album wrapper
                items = inner["items"]
                if items:
                    first = items[0].get("item", items[0])
                    album_data = first
                    t = first.get("tracks") or data.get("tracks") or []
                    if isinstance(t, dict):
                        tracks_raw = t.get("items", [])
                    else:
                        tracks_raw = t
            else:
                album_data = inner
    else:
        album_data = data.get("album", data)
        tracks_raw = data.get("tracks") or []
        if isinstance(tracks_raw, dict):
            tracks_raw = tracks_raw.get("items", [])

    # Final fallback: look for tracks in common locations if still empty
    if not tracks_raw:
        tracks_raw = (
            data.get("tracks")
            or (data.get("data") or {}).get("tracks")
            or []
        )
        if isinstance(tracks_raw, dict):
            tracks_raw = tracks_raw.get("items", [])

    logger.debug("get_album parsed title=%r, track count=%d",
                 album_data.get("title"), len(tracks_raw))

    album = _parse_album(album_data)
    tracks = []
    for t in tracks_raw:
        try:
            tracks.append(_parse_track(t))
        except (KeyError, TypeError):
            continue

    tracks.sort(key=lambda t: (t.disc_number, t.track_number))
    return AlbumDetail(album=album, tracks=tracks)


async def get_track_stream(track_id: int, quality: str = "LOSSLESS") -> TrackStream:
    data = await _get("/track/", params={"id": track_id, "quality": quality})

    logger.debug("get_track_stream response keys=%s",
                 list(data.keys()) if isinstance(data, dict) else type(data).__name__)

    # Unwrap a "data" wrapper if present
    inner = data.get("data", data) if isinstance(data, dict) else {}
    if isinstance(inner, dict) and inner is not data:
        logger.debug("get_track_stream inner keys=%s", list(inner.keys()))

    def _first(*args):
        for v in args:
            if v:
                return v
        return None

    original_url = _first(
        data.get("OriginalTrackUrl"),
        data.get("originalTrackUrl"),
        inner.get("OriginalTrackUrl"),
        inner.get("originalTrackUrl"),
        inner.get("url"),
        inner.get("trackUrl"),
    )
    manifest = data.get("manifest") or inner.get("manifest")
    manifest_mime = data.get("manifestMimeType") or inner.get("manifestMimeType")

    # Also handle a top-level or inner "urls" list
    if not original_url and not manifest:
        urls = data.get("urls") or inner.get("urls")
        if isinstance(urls, list) and urls:
            original_url = urls[0]

    logger.debug("get_track_stream track_id=%s original_url=%s has_manifest=%s",
                 track_id, bool(original_url), bool(manifest))

    return TrackStream(
        track_id=track_id,
        original_url=original_url,
        manifest=manifest,
        manifest_mime_type=manifest_mime,
        quality=quality,
    )
