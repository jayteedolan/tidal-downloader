import asyncio
import json
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..models import (
    DownloadRequest,
    DownloadJobResponse,
    DownloadProgress,
    RecordingInfo,
    TrackMetadata,
)
from ..services import tidal_client, musicbrainz_client, dash_downloader, tagger, file_manager, plex_service
from ..config import settings

router = APIRouter(prefix="/api/download", tags=["download"])

# In-memory job store: job_id -> asyncio.Queue of DownloadProgress dicts
_jobs: dict[str, asyncio.Queue] = {}


@router.post("", response_model=DownloadJobResponse)
async def start_download(body: DownloadRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = queue
    background_tasks.add_task(_run_download, job_id, body, queue)
    return DownloadJobResponse(job_id=job_id)


@router.get("/{job_id}/stream")
async def stream_progress(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        queue = _jobs[job_id]
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=60.0)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}
                continue

            yield {"event": "progress", "data": json.dumps(item)}

            if item.get("status") in ("complete", "error"):
                _jobs.pop(job_id, None)
                break

    return EventSourceResponse(event_generator())


async def _run_download(job_id: str, req: DownloadRequest, queue: asyncio.Queue):
    async def emit(status: str, track_num=None, total=None, title=None, error=None):
        await queue.put({
            "job_id": job_id,
            "status": status,
            "track_num": track_num,
            "total_tracks": total,
            "track_title": title,
            "error": error,
        })

    try:
        await emit("starting")

        # 1. Fetch Tidal album track list
        album_detail = await tidal_client.get_album(req.tidal_album_id)
        tidal_tracks = album_detail.tracks
        tidal_album = album_detail.album

        # Filter to selected tracks if a subset was requested
        if req.track_ids:
            allowed = set(req.track_ids)
            tidal_tracks = [t for t in tidal_tracks if t.id in allowed]

        # 2. Fetch MusicBrainz release detail
        mb_release = musicbrainz_client.get_release_detail(req.mb_release_id)

        # 3. Determine destination artist folder
        if req.new_folder_name:
            artist_folder = Path(settings.music_library_path) / file_manager.sanitize_filename(req.new_folder_name)
            artist_folder.mkdir(parents=True, exist_ok=True)
        else:
            artist_folder = Path(req.dest_artist_folder)

        # 4. Create album folder
        year = (mb_release.date or tidal_album.year or "")[:4] or None
        album_folder = file_manager.create_album_folder(str(artist_folder), mb_release.title or tidal_album.title, year)

        total = len(tidal_tracks)

        # Build a mapping disc+track_number -> MB recording
        mb_map: dict[tuple[int, int], RecordingInfo] = {}
        for rec in mb_release.recordings:
            mb_map[(rec.disc_number, rec.track_number)] = rec

        total_discs = mb_release.disc_count or 1

        # 5. Download each track
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, tidal_track in enumerate(tidal_tracks, start=1):
                track_title = tidal_track.title
                await emit("downloading", track_num=idx, total=total, title=track_title)

                try:
                    # Get stream info
                    stream = await tidal_client.get_track_stream(tidal_track.id, quality="LOSSLESS")

                    tmp_path = Path(tmpdir) / f"track_{idx:03d}.flac"
                    await dash_downloader.download_flac(stream, tmp_path)

                    await emit("tagging", track_num=idx, total=total, title=track_title)

                    # Look up MusicBrainz recording by disc + track number
                    rec = mb_map.get((tidal_track.disc_number, tidal_track.track_number))

                    meta = TrackMetadata(
                        title=rec.title if rec else tidal_track.title,
                        artist=rec.artist_credit if (rec and rec.artist_credit) else tidal_album.artist,
                        album_artist=mb_release.artist or tidal_album.artist,
                        album=mb_release.title or tidal_album.title,
                        date=mb_release.date or tidal_album.year,
                        track_number=tidal_track.track_number,
                        total_tracks=total,
                        disc_number=tidal_track.disc_number,
                        total_discs=total_discs,
                        label=mb_release.label,
                        country=mb_release.country,
                        cover_url=tidal_album.cover_url,
                        musicbrainz_album_id=mb_release.id,
                        musicbrainz_track_id=rec.id if rec else None,
                        musicbrainz_artist_id=mb_release.artist_id,
                    )

                    await tagger.tag_flac(tmp_path, meta)

                    filename = file_manager.track_filename(
                        tidal_track.track_number,
                        tidal_track.disc_number,
                        total_discs,
                        rec.title if rec else tidal_track.title,
                    )
                    dest = album_folder / filename
                    file_manager.move_file(tmp_path, dest)

                    await emit("done", track_num=idx, total=total, title=track_title)

                except Exception as e:
                    await emit("error", track_num=idx, total=total, title=track_title, error=str(e))
                    # Continue with remaining tracks even if one fails

        # 6. Trigger Plex scan
        plex_service.trigger_scan()

        await emit("complete")

    except Exception as e:
        await emit("error", error=str(e))
        await emit("complete")
