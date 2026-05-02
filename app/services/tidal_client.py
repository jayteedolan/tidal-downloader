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

    release_type = (
        data.get("type")
        or data.get("albumType")
        or "ALBUM"
    ).upper()
    logger.debug(
        "Album %s raw type=%r albumType=%r → %s",
        data.get("id"), data.get("type"), data.get("albumType"), release_type,
    )

    return AlbumResult(
        id=data["id"],
        title=data.get("title", ""),
        artist=artist_name,
        cover_url=_cover_url(data.get("cover")),
        year=year,
        quality=quality,
        track_count=data.get("numberOfTracks"),
        explicit=bool(data.get("explicit", False)),
        release_type=release_type,
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


async def _get(path: str, params: Optional[dict] = None) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:

        async def _try(host: str) -> dict:
            resp = await client.get(f"https://{host}{path}", params=params)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code} from {host}")
            if not resp.content:
                raise RuntimeError(f"Empty response body from {host}")
            return resp.json()

        tasks = {asyncio.create_task(_try(host)): host for host in TIDAL_HOSTS}
        pending = set(tasks)
        last_error: Optional[Exception] = None
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    exc = task.exception()
                    if exc is None:
                        return task.result()
                    last_error = exc
        finally:
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass

    raise RuntimeError(f"All Tidal hosts failed. Last error: {last_error}")


def _extract_items_from_response(data) -> list:
    """Pull the raw album items list out of a proxy search response."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for candidate in (
        data.get("items"),
        (data.get("data") or {}).get("items"),
    ):
        if candidate:
            return candidate
    for source in (data, data.get("data") or {}):
        if not isinstance(source, dict):
            continue
        albums_data = source.get("albums")
        if isinstance(albums_data, dict):
            items = albums_data.get("items", [])
            if items:
                return items
        elif isinstance(albums_data, list) and albums_data:
            return albums_data
    return []


def _albums_from_track_results(data) -> list[AlbumResult]:
    """
    Extract AlbumResult objects from a track search response.
    Track search (s=) returns tracks with embedded album info; each unique
    parent album becomes one AlbumResult with release_type='SINGLE'.
    """
    items = []
    if isinstance(data, dict):
        inner = data.get("data") or data
        if isinstance(inner, dict):
            items = inner.get("items", [])
    seen_ids: set[int] = set()
    results = []
    for item in items:
        track = item.get("item", item)
        album_sub = track.get("album")
        if not isinstance(album_sub, dict) or not album_sub.get("id"):
            continue
        album_id = int(album_sub["id"])
        if album_id in seen_ids:
            continue
        seen_ids.add(album_id)
        artist = (track.get("artists") or [{}])[0].get("name", "")
        results.append(AlbumResult(
            id=album_id,
            title=album_sub.get("title", ""),
            artist=artist,
            cover_url=_cover_url(album_sub.get("cover")),
            explicit=bool(track.get("explicit", False)),
            release_type="SINGLE",
        ))
    return results


def _parse_items(items: list) -> list[AlbumResult]:
    seen_ids: set[int] = set()
    results = []
    for item in items:
        album_data = item.get("item", item)
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


async def search_albums(query: str = "", artist: str = "", album: str = "") -> list[AlbumResult]:
    # The proxy's 'ar' (artist) parameter does not filter results — only 'al' works.
    # When both artist and album are given, run three parallel searches:
    #   al=<artist>        → artist's catalog (album/EP releases)
    #   al=<album>         → releases whose title matches the album name
    #   s=<artist> <album> → track search, which surfaces singles not indexed as
    #                        standalone album releases (e.g. JPEGMAFIA "babygirl")
    # Results are merged and ranked: releases matching both artist+title first.
    if artist and album:
        track_query = f"{artist} {album}"
        artist_data, album_data, track_data = await asyncio.gather(
            _get("/search/", params={"al": artist}),
            _get("/search/", params={"al": album}),
            _get("/search/", params={"s": track_query}),
        )
        artist_items = _extract_items_from_response(artist_data)
        album_items = _extract_items_from_response(album_data)
        track_albums = _albums_from_track_results(track_data)

        # Deduplicate raw items (artist + album searches), preserving order
        seen: set[int] = set()
        merged: list = []
        for item in artist_items + album_items:
            raw = item.get("item", item)
            id_ = raw.get("id")
            if id_ and id_ not in seen:
                seen.add(id_)
                merged.append(item)

        # Rank: items where BOTH artist name and album title match score highest
        artist_lc = artist.lower()
        album_lc = album.lower()

        def _rank(item) -> int:
            raw = item.get("item", item)
            name = ""
            if isinstance(raw.get("artist"), dict):
                name = raw["artist"].get("name", "").lower()
            elif raw.get("artists"):
                name = (raw["artists"][0] or {}).get("name", "").lower()
            title = raw.get("title", "").lower()
            score = 0
            if artist_lc in name or name in artist_lc:
                score += 10
            if album_lc in title or title in album_lc:
                score += 5
            return -score  # descending

        merged.sort(key=_rank)
        parsed = _parse_items(merged)

        # Prepend track-derived singles whose album IDs aren't already in results,
        # filtering to those where the artist name actually matches.
        parsed_ids = {r.id for r in parsed}
        for ta in track_albums:
            if ta.id not in parsed_ids and artist_lc in ta.artist.lower():
                parsed.insert(0, ta)
                parsed_ids.add(ta.id)

        logger.debug("search_albums (artist+album) total %d items", len(parsed))
        return parsed

    if artist:
        data = await _get("/search/", params={"al": artist})
    elif album:
        data = await _get("/search/", params={"al": album})
    else:
        data = await _get("/search/", params={"al": query})

    logger.debug("search_albums response type=%s keys=%s",
                 type(data).__name__,
                 list(data.keys()) if isinstance(data, dict) else "N/A")
    items = _extract_items_from_response(data)
    logger.debug("search_albums found %d raw items", len(items))
    return _parse_items(items)


def _extract_tracks_for_album(items: list, album_id: int,
                               seen: set, tracks: list, album_meta_holder: list) -> None:
    """Pull TrackInfo objects out of raw search items, filtering to album_id."""
    for t in items:
        alb = t.get("album", {})
        if not isinstance(alb, dict) or int(alb.get("id", 0)) != album_id:
            continue
        if not album_meta_holder:
            album_meta_holder.append(alb)
        track_id = t.get("id")
        if track_id and track_id not in seen:
            seen.add(track_id)
            tracks.append(TrackInfo(
                id=track_id,
                title=t.get("title", ""),
                track_number=t.get("trackNumber", 1),
                disc_number=t.get("volumeNumber", 1),
                duration=t.get("duration"),
            ))


async def _get_album_via_artist_search(album_id: int, artist_hint: str, title_hint: str = "") -> AlbumDetail:
    """
    Reconstruct AlbumDetail by running two parallel searches:
      1. a=ARTIST — broad artist catalogue (catches most tracks in one page)
      2. s=ARTIST TITLE — targeted track search (catches singles / tracks
         that fall past the first page of the artist search)
    Used when the /album/ endpoint is rate-limited (HTTP 429).
    """
    seen_track_ids: set[int] = set()
    tracks: list[TrackInfo] = []
    album_meta_holder: list[dict] = []

    queries: list[dict] = [{"a": artist_hint, "limit": 300}]
    if title_hint:
        queries.append({"s": f"{artist_hint} {title_hint}", "limit": 100})
    else:
        queries.append({"s": artist_hint, "limit": 100})

    results = await asyncio.gather(
        *[_get("/search/", params=q) for q in queries],
        return_exceptions=True,
    )

    for resp in results:
        if isinstance(resp, Exception):
            logger.debug("_get_album_via_artist_search sub-search failed: %s", resp)
            continue
        inner = resp.get("data", {})
        if not isinstance(inner, dict):
            continue

        # a= response: items are under data.tracks.items
        tracks_section = inner.get("tracks", {})
        if isinstance(tracks_section, dict):
            _extract_tracks_for_album(
                tracks_section.get("items", []),
                album_id, seen_track_ids, tracks, album_meta_holder,
            )

        # s= response: items are directly under data.items (flat list)
        flat_items = inner.get("items", [])
        for wrapped in flat_items:
            _extract_tracks_for_album(
                [wrapped.get("item", wrapped)],
                album_id, seen_track_ids, tracks, album_meta_holder,
            )

    if not tracks:
        raise RuntimeError(
            f"Tidal proxy rate-limited the album endpoint (HTTP 429) and the "
            f"search fallback found no tracks for album {album_id}. "
            f"Please try again in a moment."
        )

    tracks.sort(key=lambda t: (t.disc_number, t.track_number))

    album_meta = album_meta_holder[0] if album_meta_holder else {}
    release_date = album_meta.get("releaseDate", "")
    year = release_date[:4] if release_date else None
    album = AlbumResult(
        id=album_id,
        title=album_meta.get("title", title_hint),
        artist=artist_hint,
        cover_url=_cover_url(album_meta.get("cover")),
        year=year,
        track_count=len(tracks),
    )
    logger.info(
        "get_album fallback recovered %d tracks for album %d via search",
        len(tracks), album_id,
    )
    return AlbumDetail(album=album, tracks=tracks)


async def get_album(album_id: int, *, artist_hint: str = "", title_hint: str = "") -> AlbumDetail:
    try:
        data = await _get("/album/", params={"id": album_id})
    except RuntimeError as exc:
        if artist_hint:
            logger.warning("get_album /album/ failed (%s), trying search fallback", exc)
            return await _get_album_via_artist_search(album_id, artist_hint, title_hint)
        raise

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


async def get_album_id_for_track(track_id: int) -> Optional[int]:
    """Return the Tidal album ID that contains the given track."""
    data = await _get("/track/", params={"id": track_id})
    inner = data.get("data", data) if isinstance(data, dict) else data
    for obj in (inner, data):
        if not isinstance(obj, dict):
            continue
        album = obj.get("album")
        if isinstance(album, dict):
            album_id = album.get("id")
            if album_id:
                return int(album_id)
        for key in ("albumId", "album_id"):
            val = obj.get(key)
            if val:
                return int(val)
    return None


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
