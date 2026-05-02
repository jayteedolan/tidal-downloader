import httpx
from typing import Optional
from pathlib import Path
from mutagen.flac import FLAC, Picture
from mutagen.id3 import PictureType
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm
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


def detect_format(file_path: Path) -> str:
    """Return 'flac', 'm4a', or 'unknown' by reading the file header."""
    with open(file_path, "rb") as f:
        header = f.read(12)
    if header[:4] == b"fLaC":
        return "flac"
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return "m4a"
    return "unknown"


async def _tag_flac(file_path: Path, metadata: TrackMetadata) -> None:
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


def _m4a_freeform(value: str) -> list:
    return [MP4FreeForm(value.encode("utf-8"))]


async def _tag_m4a(file_path: Path, metadata: TrackMetadata) -> None:
    audio = MP4(str(file_path))
    audio.clear()

    audio["\xa9nam"] = [metadata.title]
    audio["\xa9ART"] = [metadata.artist]
    audio["aART"] = [metadata.album_artist]
    audio["\xa9alb"] = [metadata.album]
    audio["trkn"] = [(metadata.track_number, metadata.total_tracks)]
    audio["disk"] = [(metadata.disc_number, metadata.total_discs)]

    if metadata.date:
        audio["\xa9day"] = [metadata.date]

    if metadata.label:
        audio["----:com.apple.iTunes:LABEL"] = _m4a_freeform(metadata.label)

    if metadata.country:
        audio["----:com.apple.iTunes:RELEASECOUNTRY"] = _m4a_freeform(metadata.country)

    if metadata.musicbrainz_album_id:
        audio["----:com.apple.iTunes:MusicBrainz Album Id"] = _m4a_freeform(metadata.musicbrainz_album_id)

    if metadata.musicbrainz_track_id:
        audio["----:com.apple.iTunes:MusicBrainz Track Id"] = _m4a_freeform(metadata.musicbrainz_track_id)

    if metadata.musicbrainz_artist_id:
        audio["----:com.apple.iTunes:MusicBrainz Artist Id"] = _m4a_freeform(metadata.musicbrainz_artist_id)
        audio["----:com.apple.iTunes:MusicBrainz Album Artist Id"] = _m4a_freeform(metadata.musicbrainz_artist_id)

    if metadata.cover_url:
        cover_data = await _fetch_cover(metadata.cover_url)
        if cover_data:
            audio["covr"] = [MP4Cover(cover_data, MP4Cover.FORMAT_JPEG)]

    audio.save()


async def tag_file(file_path: Path, metadata: TrackMetadata) -> str:
    """Tag the audio file and return the actual format extension ('flac' or 'm4a')."""
    fmt = detect_format(file_path)
    if fmt == "m4a":
        await _tag_m4a(file_path, metadata)
        return "m4a"
    # Default to FLAC (raises mutagen error if file is genuinely corrupt)
    await _tag_flac(file_path, metadata)
    return "flac"


# Keep old name as an alias so other callers don't break
async def tag_flac(file_path: Path, metadata: TrackMetadata) -> None:
    await tag_file(file_path, metadata)
