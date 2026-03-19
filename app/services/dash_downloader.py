import base64
import re
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx
from ..models import TrackStream

TIMEOUT = httpx.Timeout(60.0, connect=15.0)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; TidalDownloader/1.0)",
}

NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
}


def _parse_mpd(manifest_text: str) -> tuple[str, list[str]]:
    """
    Parse a DASH MPD manifest and return (init_url, [segment_urls]).
    Handles both SegmentTemplate and BaseURL single-file manifests.
    """
    root = ET.fromstring(manifest_text)

    # Walk down to the first Representation under AudioAdaptationSet
    adaptation_sets = (
        root.findall(".//mpd:AdaptationSet", NS)
        or root.findall(".//AdaptationSet")
    )

    seg_template = None
    base_url_el = None
    rep_el = None

    for ads in adaptation_sets:
        content_type = ads.get("contentType", "")
        mime = ads.get("mimeType", "")
        if content_type not in ("audio", "") and "audio" not in mime:
            continue

        # SegmentTemplate at AdaptationSet level
        seg_template = (
            ads.find("mpd:SegmentTemplate", NS)
            or ads.find("SegmentTemplate")
        )

        reps = ads.findall("mpd:Representation", NS) or ads.findall("Representation")
        if reps:
            rep_el = reps[0]
            # SegmentTemplate at Representation level overrides
            rep_st = (
                rep_el.find("mpd:SegmentTemplate", NS)
                or rep_el.find("SegmentTemplate")
            )
            if rep_st is not None:
                seg_template = rep_st

            base_url_el = (
                rep_el.find("mpd:BaseURL", NS)
                or rep_el.find("BaseURL")
            )
        break

    if base_url_el is not None:
        # Single-file manifest — the BaseURL is the direct FLAC URL
        return base_url_el.text.strip(), []

    if seg_template is None:
        raise ValueError("Could not find SegmentTemplate or BaseURL in DASH manifest")

    init_template = seg_template.get("initialization", "")
    media_template = seg_template.get("media", "")

    # Determine segment count from SegmentTimeline or duration/timescale
    timeline = (
        seg_template.find("mpd:SegmentTimeline", NS)
        or seg_template.find("SegmentTimeline")
    )

    segment_numbers: list[int] = []

    if timeline is not None:
        segments_els = (
            timeline.findall("mpd:S", NS)
            or timeline.findall("S")
        )
        number = int(seg_template.get("startNumber", "1"))
        for s in segments_els:
            count = int(s.get("r", "0")) + 1
            for _ in range(count):
                segment_numbers.append(number)
                number += 1
    else:
        # Use Period duration and segment duration to compute count
        period = root.find("mpd:Period", NS) or root.find("Period")
        period_duration = _parse_iso8601_duration(
            (period.get("duration") if period is not None else None)
            or root.get("mediaPresentationDuration", "PT0S")
        )
        seg_duration = int(seg_template.get("duration", "0"))
        timescale = int(seg_template.get("timescale", "1"))
        if seg_duration and timescale and period_duration:
            count = int(period_duration * timescale / seg_duration) + 1
            start = int(seg_template.get("startNumber", "1"))
            segment_numbers = list(range(start, start + count))

    # Replace $RepresentationID$ in templates
    rep_id = rep_el.get("id", "0") if rep_el is not None else "0"
    init_template = init_template.replace("$RepresentationID$", rep_id)
    media_template = media_template.replace("$RepresentationID$", rep_id)

    # Build absolute URLs — detect relative vs absolute
    def make_abs(template: str) -> str:
        if template.startswith("http"):
            return template
        # Try to find BaseURL at Period or MPD level
        for el in [root]:
            bu = el.find("mpd:BaseURL", NS) or el.find("BaseURL")
            if bu is not None:
                return bu.text.strip().rstrip("/") + "/" + template.lstrip("/")
        return template

    init_url = make_abs(init_template)
    segment_urls = [
        make_abs(media_template.replace("$Number$", str(n)))
        for n in segment_numbers
    ]

    return init_url, segment_urls


def _parse_iso8601_duration(duration: str | None) -> float:
    if not duration:
        return 0.0
    m = re.match(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?",
        duration,
    )
    if not m:
        return 0.0
    hours = float(m.group(4) or 0)
    minutes = float(m.group(5) or 0)
    seconds = float(m.group(6) or 0)
    return hours * 3600 + minutes * 60 + seconds


async def download_flac(stream: TrackStream, dest_path: Path) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        if stream.original_url:
            await _stream_to_file(client, stream.original_url, dest_path)
            return

        if stream.manifest:
            manifest_text = base64.b64decode(stream.manifest).decode("utf-8", errors="replace")

            # Check if it's a JSON manifest (not XML)
            manifest_text_stripped = manifest_text.strip()
            if manifest_text_stripped.startswith("{"):
                import json
                manifest_json = json.loads(manifest_text_stripped)
                urls = manifest_json.get("urls", [])
                if urls:
                    await _stream_to_file(client, urls[0], dest_path)
                    return
                raise ValueError("JSON manifest has no 'urls' field")

            init_url, seg_urls = _parse_mpd(manifest_text)

            if not seg_urls:
                # BaseURL single-file
                await _stream_to_file(client, init_url, dest_path)
                return

            # Download init + segments and concatenate
            with open(dest_path, "wb") as f:
                for url in [init_url] + seg_urls:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    f.write(resp.content)
            return

    raise ValueError("TrackStream has neither original_url nor manifest")


async def _stream_to_file(client: httpx.AsyncClient, url: str, dest_path: Path) -> None:
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                f.write(chunk)
