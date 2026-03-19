from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from ..models import FolderMatch
from ..services import folder_search, file_manager
from ..config import settings

router = APIRouter(prefix="/api/library", tags=["library"])


@router.get("/folders", response_model=list[FolderMatch])
def search_folders(q: str = Query(..., min_length=1)):
    try:
        return folder_search.search_artist_folders(q)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/album-folder-exists")
def check_album_folder(
    album_title: str = Query(...),
    year: str = Query(""),
    dest_artist_folder: str = Query(""),
    new_folder_name: str = Query(""),
):
    try:
        if new_folder_name:
            artist_folder = str(
                Path(settings.music_library_path) / file_manager.sanitize_filename(new_folder_name)
            )
        else:
            artist_folder = dest_artist_folder

        album_path = file_manager.album_folder_path(artist_folder, album_title, year or None)

        if not album_path.exists():
            return {"folder_path": str(album_path), "non_empty": False}

        non_empty = any(album_path.iterdir())
        return {"folder_path": str(album_path), "non_empty": non_empty}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
