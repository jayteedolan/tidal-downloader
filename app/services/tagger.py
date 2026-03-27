import httpx
from typing import Optional
from pathlib import Path
from mutagen.flac import FLAC, Picture
from mutagen.id3 import PictureType
from ..models import TrackMetadata

TIMEOUT = httpx.Timeout(20.0, connect=8.0)


async def _fetch_cover(url: str) -> Optional[bytes]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
    except Exception:
        pass
    return None


async def tag_flac(file_path: Path, metadata: TrackMetadata) -> None:
    audio = FLAC(str(file_path))

    audio.clear()

    audio["title"] = metadata.title
    audio["artist"] = metadata.artist
    audio["albumartist"] = metadata.album_artist
    audio["album"] = metadata.album
    audio["tracknumber"] = str(metadata.track_number)
    audio["totaltracks"] = str(metadata.total_tracks)
    audio["tracktotal"] = str(metadata.total_tracks)
    audio["discnumber"] = str(metadata.disc_number)
    audio["totaldiscs"] = str(metadata.total_discs)
    audio["disctotal"] = str(metadata.total_discs)

    if metadata.date:
        audio["date"] = metadata.date
        audio["year"] = metadata.date[:4]

    if metadata.label:
        audio["organization"] = metadata.label
        audio["label"] = metadata.label

    if metadata.country:
        audio["releasecountry"] = metadata.country

    if metadata.musicbrainz_album_id:
        audio["musicbrainz_albumid"] = metadata.musicbrainz_album_id

    if metadata.musicbrainz_track_id:
        audio["musicbrainz_trackid"] = metadata.musicbrainz_track_id

    if metadata.musicbrainz_artist_id:
        audio["musicbrainz_artistid"] = metadata.musicbrainz_artist_id
        audio["musicbrainz_albumartistid"] = metadata.musicbrainz_artist_id

    # Embed cover art
    if metadata.cover_url:
        cover_data = await _fetch_cover(metadata.cover_url)
        if cover_data:
            pic = Picture()
            pic.type = PictureType.COVER_FRONT
            pic.mime = "image/jpeg"
            pic.desc = "Cover"
            pic.data = cover_data
            audio.add_picture(pic)

    audio.save()
