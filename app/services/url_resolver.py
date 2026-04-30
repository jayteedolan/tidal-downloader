import re
import httpx
from typing import Optional
from urllib.parse import quote
from ..models import UrlResolveResult
from .tidal_client import get_album_id_for_track

ODESLI_API = "https://api.song.link/v1-alpha.1/links"

TIDAL_ALBUM_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?album/(\d+)", re.IGNORECASE
)
TIDAL_TRACK_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?track/(\d+)", re.IGNORECASE
)

TIMEOUT = httpx.Timeout(15.0, connect=8.0)
REDIRECT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


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


async def _resolve_tidal_url(url: str) -> str:
    """Follow redirects on a Tidal URL to get the canonical URL."""
    try:
        async with httpx.AsyncClient(timeout=REDIRECT_TIMEOUT, follow_redirects=True) as client:
            resp = await client.head(url, headers={"User-Agent": "Mozilla/5.0"})
            return str(resp.url)
    except Exception:
        return url


async def resolve_url(url: str) -> UrlResolveResult:
    platform = _detect_platform(url)

    if platform == "tidal":
        # Follow redirects first — share/universal links (e.g. /album/ID/u) may
        # redirect to a canonical URL with a different album ID.
        canonical = await _resolve_tidal_url(url)
        album_id = _extract_tidal_album_id(canonical) or _extract_tidal_album_id(url)
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
                album_id = await get_album_id_for_track(track_id)
                if album_id:
                    return UrlResolveResult(
                        tidal_album_id=album_id,
                        source_platform=platform,
                        album_title=entity.get("title"),
                        artist=entity.get("artistName"),
                    )

        raise ValueError("Could not find a Tidal album match via Odesli for this URL.")

    raise ValueError(f"Unsupported URL platform. Please provide a Tidal, Spotify, Apple Music, or Qobuz album URL.")
