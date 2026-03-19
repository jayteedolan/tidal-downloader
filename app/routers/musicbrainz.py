from fastapi import APIRouter, HTTPException, Query
from ..models import ReleaseResult, ReleaseDetail
from ..services import musicbrainz_client

router = APIRouter(prefix="/api/musicbrainz", tags=["musicbrainz"])


@router.get("/search", response_model=list[ReleaseResult])
def search_releases(
    artist: str = Query(..., min_length=1),
    album: str = Query(..., min_length=1),
):
    try:
        return musicbrainz_client.search_releases(artist, album)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/release/{mb_id}", response_model=ReleaseDetail)
def get_release(mb_id: str):
    try:
        return musicbrainz_client.get_release_detail(mb_id)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
