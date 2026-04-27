from fastapi import APIRouter, HTTPException, Query
from ..models import AlbumResult, AlbumDetail, UrlResolveRequest, UrlResolveResult
from ..services import tidal_client, url_resolver

router = APIRouter(prefix="/api/tidal", tags=["tidal"])


@router.get("/search", response_model=list[AlbumResult])
async def search_albums(
    q: str = Query(""),
    artist: str = Query(""),
    album: str = Query(""),
):
    if not (q or artist or album):
        raise HTTPException(status_code=400, detail="Provide at least one of: q, artist, album")
    try:
        return await tidal_client.search_albums(query=q, artist=artist, album=album)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/album/{album_id}", response_model=AlbumDetail)
async def get_album(album_id: int):
    try:
        return await tidal_client.get_album(album_id)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/resolve-url", response_model=UrlResolveResult)
async def resolve_url(body: UrlResolveRequest):
    try:
        return await url_resolver.resolve_url(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
