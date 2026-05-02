import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ODESLI_API = "https://api.song.link/v1-alpha.1/links"
ODESLI_TIMEOUT = httpx.Timeout(15.0, connect=8.0)


async def get_spotify_url(tidal_album_id: int) -> Optional[str]:
    """Return the Spotify album/track URL for a Tidal album ID via Odesli, or None."""
    try:
        async with httpx.AsyncClient(timeout=ODESLI_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                ODESLI_API,
                params={"url": f"https://tidal.com/browse/album/{tidal_album_id}"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
        sp = data.get("linksByPlatform", {}).get("spotify", {})
        return sp.get("url") or None
    except Exception as exc:
        logger.debug("get_spotify_url failed: %s", exc)
        return None


def _spotiflac_run(spotify_url: str, output_dir: Path, spotify_token: str = "") -> dict[int, Path]:
    """
    Run SpotiFLAC synchronously (intended for use in run_in_executor).
    Returns {track_number: Path} for every FLAC file successfully downloaded.
    SpotiFLAC tries Tidal first, then Qobuz, using its own pool of proxy hosts.
    """
    try:
        from SpotiFLAC import SpotiFLAC  # noqa: PLC0415
        SpotiFLAC(
            url=spotify_url,
            output_dir=str(output_dir),
            services=["tidal", "qobuz"],
            quality="HI_RES",
            embed_lyrics=True,
            # lrclib first: crowd-sourced, uncensored, synced LRC
            # Spotify as fallback (may censor explicit words via its API)
            lyrics_providers=["lrclib", "spotify", "musixmatch"],
            lyrics_spotify_token=spotify_token,
            enrich_metadata=False,
            use_track_numbers=True,
            use_album_track_numbers=True,
            log_level=30,  # WARNING — suppress SpotiFLAC's verbose output
        )
    except Exception as exc:
        logger.warning("SpotiFLAC run error: %s", exc)
        raise RuntimeError(str(exc)) from exc

    all_flacs = sorted(output_dir.rglob("*.flac"))
    result: dict[int, Path] = {}
    for f in all_flacs:
        m = re.match(r"(\d+)", f.name)
        track_num = int(m.group(1)) if m else (len(result) + 1)
        result[track_num] = f
    logger.info("SpotiFLAC downloaded %d FLAC tracks to %s", len(result), output_dir)
    return result


async def download_album(spotify_url: str, output_dir: Path, spotify_token: str = "") -> dict[int, Path]:
    """Async wrapper — runs SpotiFLAC in a thread so the event loop isn't blocked."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _spotiflac_run, spotify_url, output_dir, spotify_token)
