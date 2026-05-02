import re
import httpx
from typing import Optional
from urllib.parse import quote
from ..models import UrlResolveResult
from .tidal_client import get_album_id_for_track, search_albums

ODESLI_API = "https://api.song.link/v1-alpha.1/links"

TIDAL_ALBUM_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?album/(\d+)", re.IGNORECASE
)
TIDAL_TRACK_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?track/(\d+)", re.IGNORECASE
)

TIMEOUT = httpx.Timeout(15.0, connect=8.0)

# Matches Tidal URLs that have extra path components after the album ID,
# e.g. /album/510761404/u — these are share/universal links whose numeric
# ID may not be the real Tidal album ID and need Odesli to canonicalise.
TIDAL_SHARE_LINK_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?album/\d+/.+", re.IGNORECASE
)


def _detect_platform(url: str) -> str:
    url_lower = url.lower()
    if "tidal.com" in url_lower:
        return "tidal"
    if "spotify.com" in url_lower or "open.spotify" in url_lower:
        return "spotify"
    if "music.apple.com" in url_lower:
        return "apple"
    if "qobuz.com" in url_lower:
        return "qobuz"
    return "unknown"


def _extract_tidal_album_id(url: str) -> Optional[int]:
    m = TIDAL_ALBUM_RE.search(url)
    if m:
        return int(m.group(1))
    return None


def _extract_tidal_track_id(url: str) -> Optional[int]:
    m = TIDAL_TRACK_RE.search(url)
    if m:
        return int(m.group(1))
    return None


async def _odesli_lookup(url: str) -> Optional[int]:
    """Return the Tidal album ID for a URL by querying Odesli, or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(ODESLI_API, params={"url": url, "songIfSingle": "true"})
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None

    links_by_platform = data.get("linksByPlatform", {})
    tidal_entry = links_by_platform.get("tidal", {})
    tidal_url = tidal_entry.get("url", "")

    album_id = _extract_tidal_album_id(tidal_url)
    if album_id:
        return album_id

    # Odesli returned a track URL — resolve to parent album
    track_id = _extract_tidal_track_id(tidal_url)
    if track_id:
        try:
            return await get_album_id_for_track(track_id)
        except RuntimeError:
            return None

    return None


async def resolve_url(url: str) -> UrlResolveResult:
    platform = _detect_platform(url)

    if platform == "tidal":
        # Share/universal links (e.g. /album/ID/u) append a slug after the numeric
        # segment; the number may not be a valid Tidal album ID.  Resolve via Odesli
        # first and fall back to direct extraction if Odesli can't find it.
        if TIDAL_SHARE_LINK_RE.search(url):
            album_id = await _odesli_lookup(url)
            if album_id is None:
                album_id = _extract_tidal_album_id(url)
            if album_id is None:
                raise ValueError("Could not resolve this Tidal share link. Try using a direct Tidal album URL.")
            return UrlResolveResult(tidal_album_id=album_id, source_platform="tidal")

        album_id = _extract_tidal_album_id(url)
        if album_id is None:
            raise ValueError("Could not extract a Tidal album ID from this URL. Try using a Tidal album URL (not a track URL).")
        return UrlResolveResult(
            tidal_album_id=album_id,
            source_platform="tidal",
        )

    if platform in ("spotify", "apple", "qobuz"):
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                ODESLI_API,
                params={"url": url, "songIfSingle": "true"},
            )
            if resp.status_code != 200:
                raise ValueError(f"Odesli API returned {resp.status_code}. The URL may not be supported.")
            data = resp.json()

        # Find the Tidal entity
        links_by_platform = data.get("linksByPlatform", {})
        if "tidal" in links_by_platform:
            tidal_url = links_by_platform["tidal"].get("url", "")
            entity_unique_id = links_by_platform["tidal"].get("entityUniqueId", "")
            entity = data.get("entitiesByUniqueId", {}).get(entity_unique_id, {})

            album_id = _extract_tidal_album_id(tidal_url)
            if album_id:
                return UrlResolveResult(
                    tidal_album_id=album_id,
                    source_platform=platform,
                    album_title=entity.get("title"),
                    artist=entity.get("artistName"),
                )

            # Odesli linked to a Tidal track instead of an album (common for singles).
            # Look up the track's parent album via the Tidal API.
            track_id = _extract_tidal_track_id(tidal_url)
            if track_id:
                album_id = None
                try:
                    album_id = await get_album_id_for_track(track_id)
                except RuntimeError:
                    # /track/ endpoint down — search by artist+title from Odesli entity
                    artist_hint = entity.get("artistName", "")
                    title_hint = entity.get("title", "")
                    if artist_hint and title_hint:
                        try:
                            results = await search_albums(artist=artist_hint, album=title_hint)
                            album_id = results[0].id if results else None
                        except RuntimeError:
                            pass
                if album_id:
                    return UrlResolveResult(
                        tidal_album_id=album_id,
                        source_platform=platform,
                        album_title=entity.get("title"),
                        artist=entity.get("artistName"),
                    )
                # Tidal proxy entirely unavailable — fall back to SpotiFLAC
                if platform == "spotify":
                    return UrlResolveResult(
                        tidal_album_id=None,
                        spotify_url=url,
                        source_platform=platform,
                        album_title=entity.get("title"),
                        artist=entity.get("artistName"),
                    )

        # No Tidal link at all — try SpotiFLAC fallback for Spotify URLs
        if platform == "spotify":
            return UrlResolveResult(
                tidal_album_id=None,
                spotify_url=url,
                source_platform=platform,
            )
        raise ValueError("Could not find a Tidal album match via Odesli for this URL.")

    raise ValueError(f"Unsupported URL platform. Please provide a Tidal, Spotify, Apple Music, or Qobuz album URL.")
