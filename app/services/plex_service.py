from ..config import settings


def trigger_scan() -> None:
    if not settings.plex_token:
        return

    try:
        from plexapi.server import PlexServer
        plex = PlexServer(settings.plex_url, settings.plex_token)
        music = plex.library.section(settings.plex_music_section)
        music.update()
    except Exception as e:
        # Non-fatal — log but don't crash the download job
        print(f"[plex] Scan trigger failed: {e}")
