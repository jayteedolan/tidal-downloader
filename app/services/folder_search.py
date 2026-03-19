from pathlib import Path
from rapidfuzz import process, fuzz
from ..models import FolderMatch
from ..config import settings


def search_artist_folders(query: str, limit: int = 8, score_cutoff: float = 30.0) -> list[FolderMatch]:
    library_path = Path(settings.music_library_path)
    if not library_path.exists():
        return []

    folders = [d for d in library_path.iterdir() if d.is_dir()]
    if not folders:
        return []

    folder_names = [f.name for f in folders]
    matches = process.extract(
        query,
        folder_names,
        scorer=fuzz.WRatio,
        limit=limit,
        score_cutoff=score_cutoff,
    )

    results = []
    for name, score, _ in matches:
        full_path = str(library_path / name)
        results.append(FolderMatch(name=name, full_path=full_path, score=round(score, 1)))

    results.sort(key=lambda m: m.score, reverse=True)
    return results
