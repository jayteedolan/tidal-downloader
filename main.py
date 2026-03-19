import logging
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.routers import tidal, musicbrainz, library, download

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")

app = FastAPI(title="Tidal Music Downloader", version="1.0.0")

app.include_router(tidal.router)
app.include_router(musicbrainz.router)
app.include_router(library.router)
app.include_router(download.router)

static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
async def root():
    return FileResponse(str(static_dir / "index.html"))
