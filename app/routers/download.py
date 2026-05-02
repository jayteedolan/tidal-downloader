import asyncio
import json
import logging
import shutil
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, BackgroundTasks, HTTPException
from sse_starlette.sse import EventSourceResponse

from ..models import (
    DownloadRequest,
    DownloadJobResponse,
    DownloadProgress,
    RecordingInfo,
    TrackMetadata,
)
from ..services import tidal_client, musicbrainz_client, dash_downloader, tagger, file_manager, plex_service, spotiflac_service
from ..config import settings

router = APIRouter(prefix="/api/download", tags=["download"])

# In-memory job store: job_id -> asyncio.Queue of DownloadProgress dicts
_jobs: dict[str, asyncio.Queue] = {}
# Completed jobs: job_id -> terminal event payload, kept for 30s after completion
_completed_jobs: dict[str, dict] = {}


async def _cleanup_job(job_id: str, delay: int = 30) -> None:
    await asyncio.sleep(delay)
    _jobs.pop(job_id, None)
    _completed_jobs.pop(job_id, None)


@router.post("", response_model=DownloadJobResponse)
async def start_download(body: DownloadRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _jobs[job_id] = queue
    background_tasks.add_task(_run_download, job_id, body, queue)
    return DownloadJobResponse(job_id=job_id)


@router.get("/{job_id}/stream")
async def stream_progress(job_id: str):
    # If the job already completed (client reconnecting after the fact), replay terminal event
    if job_id in _completed_jobs:
        async def completed_generator():
            yield {"event": "progress", "data": json.dumps(_completed_jobs[job_id])}
        return EventSourceResponse(completed_generator())

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
                _completed_jobs[job_id] = item
                asyncio.create_task(_cleanup_job(job_id, delay=30))
                break

    return EventSourceResponse(event_generator())


async def _run_spotiflac_only(req: DownloadRequest, emit) -> None:
    """Download an album directly via SpotiFLAC when the Tidal proxy is unavailable."""
    if req.new_folder_name:
        artist_folder = Path(settings.music_library_path) / file_manager.sanitize_filename(req.new_folder_name)
        artist_folder.mkdir(parents=True, exist_ok=True)
    else:
        artist_folder = Path(req.dest_artist_folder)

    await emit("downloading", title="Connecting to SpotiFLAC (trying Tidal, then Qobuz)…")

    with tempfile.TemporaryDirectory(prefix="sf_") as tmpdir:
        await emit("downloading", title="Downloading via SpotiFLAC — this may take a minute…")
        flac_files = await spotiflac_service.download_album(req.spotify_url, Path(tmpdir))

        if not flac_files:
            raise RuntimeError("SpotiFLAC returned no files — all Tidal and Qobuz sources failed")

        album_title = req.album_title or "Unknown Album"
        album_folder = file_manager.create_album_folder(str(artist_folder), album_title, None)

        sorted_tracks = sorted(flac_files.items())
        if req.spotiflac_track_positions:
            selected = set(req.spotiflac_track_positions)
            sorted_tracks = [(tn, p) for i, (tn, p) in enumerate(sorted_tracks, start=1) if i in selected]

        total = len(sorted_tracks)

        await emit("downloading", title=f"Moving {total} track{'s' if total != 1 else ''} to library…")
        for idx, (track_num, src_path) in enumerate(sorted_tracks, start=1):
            stem = src_path.stem
            # SpotiFLAC names files "TITLE - ARTISTS"; strip the artist suffix
            title = stem.split(" - ")[0].strip() if " - " in stem else stem
            await emit("downloading", track_num=idx, total=total, title=title)
            source_fmt = tagger.detect_audio_detail(src_path)
            dest = album_folder / src_path.name
            file_manager.move_file(src_path, dest)
            fmt = tagger.detect_audio_detail(dest)
            await emit("done", track_num=idx, total=total, title=title, fmt=fmt, source_fmt=source_fmt)


async def _download_track_prefer_flac(track_id: int, tmp_path: Path) -> str:
    """Download a track; returns the source format detected before any quality retry."""
    stream = await tidal_client.get_track_stream(track_id, quality="LOSSLESS")
    await dash_downloader.download_flac(stream, tmp_path)

    source_fmt = tagger.detect_audio_detail(tmp_path)

    if tagger.detect_format(tmp_path) == "m4a":
        tmp_retry = tmp_path.with_suffix(".hires.tmp")
        try:
            stream2 = await tidal_client.get_track_stream(track_id, quality="HI_RES_LOSSLESS")
            await dash_downloader.download_flac(stream2, tmp_retry)
            if tagger.detect_format(tmp_retry) == "flac":
                tmp_retry.replace(tmp_path)
            else:
                tmp_retry.unlink(missing_ok=True)
        except Exception:
            tmp_retry.unlink(missing_ok=True)

    return source_fmt


async def _run_download(job_id: str, req: DownloadRequest, queue: asyncio.Queue):
    async def emit(status: str, track_num=None, total=None, title=None, error=None, fmt=None, source_fmt=None):
        await queue.put({
            "job_id": job_id,
            "status": status,
            "track_num": track_num,
            "total_tracks": total,
            "track_title": title,
            "error": error,
            "format": fmt,
            "source_format": source_fmt,
        })

    try:
        await emit("starting")

        # SpotiFLAC-only path when Tidal proxy is unavailable
        if req.tidal_album_id is None:
            if not req.spotify_url:
                raise ValueError("No download source available (Tidal proxy down, no Spotify URL)")
            await _run_spotiflac_only(req, emit)
            plex_service.trigger_scan()
            await emit("complete")
            return

        # 1. Fetch Tidal album track list
        album_detail = await tidal_client.get_album(req.tidal_album_id)
        tidal_tracks = album_detail.tracks
        tidal_album = album_detail.album

        # Filter to selected tracks if a subset was requested
        if req.track_ids:
            allowed = set(req.track_ids)
            tidal_tracks = [t for t in tidal_tracks if t.id in allowed]

        # 2. Fetch MusicBrainz release detail (optional — None if user skipped)
        mb_release = None
        if req.mb_release_id:
            mb_release = musicbrainz_client.get_release_detail(req.mb_release_id)

        # 3. Determine destination artist folder
        if req.new_folder_name:
            artist_folder = Path(settings.music_library_path) / file_manager.sanitize_filename(req.new_folder_name)
            artist_folder.mkdir(parents=True, exist_ok=True)
        else:
            artist_folder = Path(req.dest_artist_folder)

        # 4. Create album folder
        release_date = (mb_release.date if mb_release else None) or tidal_album.year or ""
        year = release_date[:4] or None
        album_title = (mb_release.title if mb_release else None) or tidal_album.title
        album_folder = file_manager.create_album_folder(str(artist_folder), album_title, year)

        total = len(tidal_tracks)

        # Build a mapping disc+track_number -> MB recording
        mb_map: dict[tuple[int, int], RecordingInfo] = {}
        if mb_release:
            for rec in mb_release.recordings:
                mb_map[(rec.disc_number, rec.track_number)] = rec

        total_discs = (mb_release.disc_count if mb_release else None) \
            or len({t.disc_number for t in tidal_tracks}) or 1

        # 5a. Attempt a lossless pre-download via SpotiFLAC (Tidal+Qobuz proxy pool).
        #     One Odesli call resolves the Tidal album → Spotify URL; SpotiFLAC then
        #     downloads the whole album to a temp dir.  Tracks it gets as FLAC are used
        #     in place of the Tidal proxy stream below; proxy is the fallback for any
        #     SpotiFLAC misses or failures.
        spotiflac_flac: dict[int, Path] = {}
        _sf_tmpdir_obj = tempfile.TemporaryDirectory(prefix="sf_")
        sf_tmpdir = Path(_sf_tmpdir_obj.name)
        try:
            await emit("starting", track_num=None, total=total,
                       title="Checking for lossless source…")
            spotify_url = await spotiflac_service.get_spotify_url(req.tidal_album_id)
            if spotify_url:
                spotiflac_flac = await spotiflac_service.download_album(spotify_url, sf_tmpdir)
        except Exception as sf_exc:
            logger.warning("SpotiFLAC pre-download failed: %s", sf_exc)

        # 5b. Download each track
        with tempfile.TemporaryDirectory() as tmpdir:
            for idx, tidal_track in enumerate(tidal_tracks, start=1):
                track_title = tidal_track.title
                await emit("downloading", track_num=idx, total=total, title=track_title)

                try:
                    tmp_path = Path(tmpdir) / f"track_{idx:03d}.tmp"

                    # Use SpotiFLAC FLAC when available, otherwise Tidal proxy
                    sf_file = spotiflac_flac.get(tidal_track.track_number)
                    if sf_file and sf_file.exists() and tagger.detect_format(sf_file) == "flac":
                        source_fmt = tagger.detect_audio_detail(sf_file)
                        shutil.copy2(str(sf_file), str(tmp_path))
                    else:
                        source_fmt = await _download_track_prefer_flac(tidal_track.id, tmp_path)

                    await emit("tagging", track_num=idx, total=total, title=track_title)

                    # Look up MusicBrainz recording by disc + track number
                    rec = mb_map.get((tidal_track.disc_number, tidal_track.track_number))

                    meta = TrackMetadata(
                        title=rec.title if rec else tidal_track.title,
                        artist=rec.artist_credit if (rec and rec.artist_credit) else tidal_album.artist,
                        album_artist=(mb_release.artist if mb_release else None) or tidal_album.artist,
                        album=album_title,
                        date=release_date or None,
                        track_number=tidal_track.track_number,
                        total_tracks=total,
                        disc_number=tidal_track.disc_number,
                        total_discs=total_discs,
                        label=mb_release.label if mb_release else None,
                        country=mb_release.country if mb_release else None,
                        cover_url=tidal_album.cover_url,
                        musicbrainz_album_id=mb_release.id if mb_release else None,
                        musicbrainz_track_id=rec.id if rec else None,
                        musicbrainz_artist_id=mb_release.artist_id if mb_release else None,
                    )

                    ext = await tagger.tag_file(tmp_path, meta)

                    filename = file_manager.track_filename(
                        tidal_track.track_number,
                        tidal_track.disc_number,
                        total_discs,
                        rec.title if rec else tidal_track.title,
                        ext=ext,
                    )
                    dest = album_folder / filename
                    file_manager.move_file(tmp_path, dest)

                    await emit("done", track_num=idx, total=total, title=track_title, fmt=ext, source_fmt=source_fmt)

                except Exception as e:
                    await emit("error", track_num=idx, total=total, title=track_title, error=str(e))
                    # Continue with remaining tracks even if one fails

        _sf_tmpdir_obj.cleanup()

        # 6. Trigger Plex scan
        plex_service.trigger_scan()

        await emit("complete")

    except Exception as e:
        await emit("error", error=str(e))
        await emit("complete")
