import re
import shutil
from typing import Optional
from pathlib import Path

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING = re.compile(r'[\s.]+$')


def sanitize_filename(name: str) -> str:
    name = _ILLEGAL_CHARS.sub("_", name)
    name = _TRAILING.sub("", name)
    return name.strip() or "Unknown"


def album_folder_path(artist_folder: str, album_title: str, year: Optional[str]) -> Path:
    folder_name = sanitize_filename(album_title)
    if year:
        folder_name = f"{folder_name} ({year})"
    return Path(artist_folder) / folder_name


def create_album_folder(artist_folder: str, album_title: str, year: Optional[str]) -> Path:
    album_path = album_folder_path(artist_folder, album_title, year)
    album_path.mkdir(parents=True, exist_ok=True)
    return album_path


def track_filename(track_number: int, disc_number: int, total_discs: int, title: str, ext: str = "flac") -> str:
    clean_title = sanitize_filename(title)
    if total_discs > 1:
        return f"{disc_number}-{track_number:02d} - {clean_title}.{ext}"
    return f"{track_number:02d} - {clean_title}.{ext}"


def move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
