import re
import httpx
from typing import Optional
from urllib.parse import quote
from ..models import UrlResolveResult

ODESLI_API = "https://api.song.link/v1-alpha.1/links"

TIDAL_ALBUM_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?album/(\d+)", re.IGNORECASE
)
TIDAL_TRACK_RE = re.compile(
    r"tidal\.com/(?:browse/)?(?:[a-z]{2}/)?track/(\d+)", re.IGNORECASE
)

TIMEOUT = httpx.Timeout(15.0, connect=8.0)


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


async def resolve_url(url: str) -> UrlResolveResult:
    platform = _detect_platform(url)

    if platform == "tidal":
        album_id = _extract_tidal_album_id(url)
        if album_id is None:
            # It might be a track URL — we can't easily get the album from just the URL
            # without an extra API call; return what we have and let the caller handle it
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
        tidal_entity = None
        links_by_platform = data.get("linksByPlatform", {})
        if "tidal" in links_by_platform:
            tidal_url = links_by_platform["tidal"].get("url", "")
            album_id = _extract_tidal_album_id(tidal_url)
            if album_id:
                # Get title/artist from the entity map
                entity_unique_id = links_by_platform["tidal"].get("entityUniqueId", "")
                entity = data.get("entitiesByUniqueId", {}).get(entity_unique_id, {})
                return UrlResolveResult(
                    tidal_album_id=album_id,
                    source_platform=platform,
                    album_title=entity.get("title"),
                    artist=entity.get("artistName"),
                )

        raise ValueError("Could not find a Tidal album match via Odesli for this URL.")

    raise ValueError(f"Unsupported URL platform. Please provide a Tidal, Spotify, Apple Music, or Qobuz album URL.")
