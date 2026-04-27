import musicbrainzngs
from ..models import ReleaseResult, ReleaseDetail, RecordingInfo

musicbrainzngs.set_useragent("TidalDownloader", "1.0", "https://github.com/user/tidal-downloader")


def _release_to_result(r: dict) -> ReleaseResult:
    artist = ""
    artist_credit = r.get("artist-credit", [])
    if artist_credit:
        first = artist_credit[0]
        if isinstance(first, dict):
            artist = first.get("artist", {}).get("name", "") or first.get("name", "")

    label = None
    label_info = r.get("label-info-list", []) or r.get("label-info", [])
    if label_info:
        first_label = label_info[0]
        if isinstance(first_label, dict):
            label_obj = first_label.get("label")
            if label_obj:
                label = label_obj.get("name")

    release_type = r.get("release-group", {}).get("primary-type")
    secondary = r.get("release-group", {}).get("secondary-type-list", [])
    if secondary:
        release_type = f"{release_type} ({', '.join(secondary)})" if release_type else ", ".join(secondary)

    # Count tracks across all mediums
    track_count = 0
    disc_count = 0
    fmt = None
    mediums = r.get("medium-list", []) or r.get("medium-track-count", [])
    if isinstance(mediums, list):
        disc_count = len(mediums)
        for m in mediums:
            if isinstance(m, dict):
                track_count += int(m.get("track-count", 0))
                if fmt is None:
                    fmt = m.get("format")
    elif r.get("track-count"):
        track_count = int(r["track-count"])

    if not track_count and r.get("track-count"):
        track_count = int(r["track-count"])

    return ReleaseResult(
        id=r["id"],
        title=r.get("title", ""),
        artist=artist,
        date=r.get("date"),
        country=r.get("country"),
        label=label,
        track_count=track_count or None,
        disc_count=disc_count or None,
        release_type=release_type,
        format=fmt,
        barcode=r.get("barcode"),
    )


def search_releases(artist: str, album: str, limit: int = 25) -> list[ReleaseResult]:
    try:
        result = musicbrainzngs.search_releases(
            artist=artist,
            release=album,
            limit=limit,
        )
    except musicbrainzngs.WebServiceError as e:
        raise RuntimeError(f"MusicBrainz search failed: {e}")

    releases = result.get("release-list", [])
    results = []
    for r in releases:
        try:
            results.append(_release_to_result(r))
        except Exception:
            continue
    return results


def get_release_detail(mb_id: str) -> ReleaseDetail:
    try:
        result = musicbrainzngs.get_release_by_id(
            mb_id,
            includes=["artists", "recordings", "labels", "release-groups", "media"],
        )
    except musicbrainzngs.WebServiceError as e:
        raise RuntimeError(f"MusicBrainz lookup failed: {e}")

    r = result.get("release", {})
    base = _release_to_result(r)

    recordings: list[RecordingInfo] = []
    for medium in r.get("medium-list", []):
        disc_num = int(medium.get("position", 1))
        for track in medium.get("track-list", []):
            rec = track.get("recording", {})
            artist_credit = rec.get("artist-credit", [])
            artist_str = ""
            if artist_credit:
                parts = []
                for ac in artist_credit:
                    if isinstance(ac, dict):
                        parts.append(ac.get("artist", {}).get("name", "") or ac.get("name", ""))
                    elif isinstance(ac, str):
                        parts.append(ac)
                artist_str = "".join(parts).strip()

            recordings.append(
                RecordingInfo(
                    id=rec.get("id", ""),
                    title=track.get("title") or rec.get("title", ""),
                    track_number=int(track.get("position", 1)),
                    disc_number=disc_num,
                    artist_credit=artist_str or None,
                    length=int(rec["length"]) if rec.get("length") else None,
                )
            )

    artist_id = None
    artist_credit = r.get("artist-credit", [])
    if artist_credit and isinstance(artist_credit[0], dict):
        artist_id = artist_credit[0].get("artist", {}).get("id")

    return ReleaseDetail(
        id=base.id,
        title=base.title,
        artist=base.artist,
        artist_id=artist_id,
        date=base.date,
        country=base.country,
        label=base.label,
        track_count=base.track_count,
        disc_count=base.disc_count,
        release_type=base.release_type,
        format=base.format,
        recordings=recordings,
    )
