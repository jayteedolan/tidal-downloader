import hashlib
import logging
import re
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
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

def _build_html() -> str:
    """Return index.html with the app.js version set to a hash of its current content."""
    js_path = static_dir / "js" / "app.js"
    js_hash = hashlib.md5(js_path.read_bytes()).hexdigest()[:10]
    html = (static_dir / "index.html").read_text()
    return re.sub(r'app\.js\?v=[^"\']+', f'app.js?v={js_hash}', html)

_html_cache = None

@app.get("/")
async def root():
    global _html_cache
    if _html_cache is None:
        _html_cache = _build_html()
    return HTMLResponse(_html_cache)
