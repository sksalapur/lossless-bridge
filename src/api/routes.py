from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.resolvers.engine import ResolverEngine
from src.config.settings import settings
import httpx
import logging
import re
import xml.etree.ElementTree as ET

router = APIRouter()
logger = logging.getLogger(__name__)
engine = ResolverEngine()

@router.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}

def verify_secret(secret: str):
    if settings.bridge_secret and secret != settings.bridge_secret:
        raise HTTPException(status_code=404, detail="Not Found")

@router.get("/{secret}/manifest.json")
async def get_manifest(secret: str):
    verify_secret(secret)
    return {
        "id": "lossless-bridge",
        "name": "Lossless Bridge",
        "version": "1.0.0",
        "resources": ["search", "stream"],
        "allowDownloads": 1,
    }

@router.get("/{secret}/search")
async def search_tracks(secret: str, q: str = ""):
    verify_secret(secret)
    if not q:
        return {"tracks": []}
    
    results = await engine.search(q)
    return {"tracks": results}

@router.get("/{secret}/stream/{track_id}")
async def stream_track(secret: str, track_id: str, request: Request):
    """
    Stream endpoint — acts as a bridge for BitChord.
    BitChord's player can handle DASH manifests, but its background offline
    downloader frequently fails on DASH streams from addons. 
    
    To fix downloads, we point BitChord to our own /file proxy endpoint 
    which will stitch the DASH segments together on the fly into a single file!
    """
    verify_secret(secret)

    stream_data = await engine.resolve_stream_url(track_id)
    if not stream_data:
        raise HTTPException(status_code=404, detail="Lossless stream not found")

    # Save the upstream MPD url so the proxy can use it
    mpd_url = stream_data.get("url")
    if not mpd_url:
        raise HTTPException(status_code=404, detail="No URL in upstream response")
        
    engine.cache_stream_url(track_id, mpd_url)

    file_url = str(request.url_for("proxy_dash", secret=secret, track_id=track_id))

    logger.info(f"Stream resolved: telling BitChord to fetch single file from {file_url}")

    # We tell BitChord it's a direct file (manifest: none) wrapped in m4a/mp4 container
    return {
        "url": file_url,
        "format": "m4a",
        "codec": stream_data.get("codec", "flac"),
        "container": "m4a",
        "manifest": "none",
        "encrypted": False,
        "quality": stream_data.get("quality", "Lossless"),
        "audioQuality": "LOSSLESS",
        "bitDepth": stream_data.get("bitDepth", 24),
        "sampleRate": stream_data.get("sampleRate", 48000),
    }

async def fetch_and_parse_mpd(mpd_url: str):
    """Fetches the MPD and extracts segment URLs."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(mpd_url)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch MPD: {resp.status_code}")
        xml_text = resp.text
        
    # Remove XML namespaces to make parsing easier
    xml_text = re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)
    root = ET.fromstring(xml_text)
    
    segment_template = root.find(".//SegmentTemplate")
    if segment_template is None:
        raise Exception("No SegmentTemplate found in MPD")
        
    init_url = segment_template.get("initialization")
    media_url_template = segment_template.get("media")
    start_number = int(segment_template.get("startNumber", "1"))
    
    # Calculate total segments
    total_segments = 0
    timeline = segment_template.find(".//SegmentTimeline")
    if timeline is not None:
        for s in timeline.findall("S"):
            r = int(s.get("r", "0"))
            total_segments += (1 + r)
            
    if total_segments == 0:
        raise Exception("Could not determine total segments from SegmentTimeline")
        
    return init_url, media_url_template, start_number, total_segments

@router.get("/{secret}/file/{track_id}", name="proxy_dash")
async def proxy_dash(secret: str, track_id: str, request: Request):
    """
    Downloads all DASH segments on the fly and pipes them as a single continuous file.
    Supports basic Range requests by skipping and truncating bytes dynamically.
    """
    verify_secret(secret)
    mpd_url = engine.get_cached_stream_url(track_id)
    if not mpd_url:
        raise HTTPException(status_code=404, detail="MPD URL not found in cache")

    try:
        init_url, media_url_template, start_number, total_segments = await fetch_and_parse_mpd(mpd_url)
    except Exception as e:
        logger.error(f"DASH parsing failed for {track_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to parse upstream manifest")

    init_url = init_url.replace("&amp;", "&")
    media_url_template = media_url_template.replace("&amp;", "&")

    # Parse Range header (e.g., "bytes=65536-1048575" or "bytes=65536-")
    range_header = request.headers.get("Range")
    start_byte = 0
    end_byte = None
    if range_header and range_header.startswith("bytes="):
        parts = range_header.replace("bytes=", "").split("-")
        if parts[0]:
            start_byte = int(parts[0])
        if len(parts) > 1 and parts[1]:
            end_byte = int(parts[1])

    async def stream_segments():
        bytes_yielded = 0
        bytes_skipped = 0
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # We'll put the init URL and all media URLs in a single list
            urls = [init_url] + [
                media_url_template.replace("$Number$", str(start_number + i)) 
                for i in range(total_segments)
            ]
            
            for seg_url in urls:
                try:
                    async with client.stream("GET", seg_url) as resp:
                        if resp.status_code != 200:
                            logger.error(f"Segment failed: {resp.status_code} for {seg_url}")
                            break
                            
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            # Skip logic
                            if bytes_skipped + len(chunk) <= start_byte:
                                bytes_skipped += len(chunk)
                                continue
                            elif bytes_skipped < start_byte:
                                skip = start_byte - bytes_skipped
                                chunk = chunk[skip:]
                                bytes_skipped = start_byte
                                
                            # Truncate logic if end_byte is specified
                            if end_byte is not None:
                                remaining = (end_byte - start_byte + 1) - bytes_yielded
                                if len(chunk) > remaining:
                                    chunk = chunk[:remaining]
                                    
                            if chunk:
                                yield chunk
                                bytes_yielded += len(chunk)
                                
                            if end_byte is not None and bytes_yielded >= (end_byte - start_byte + 1):
                                return # Reached end of requested range
                except Exception as e:
                    logger.error(f"Error streaming segment: {e}")
                    break

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": "audio/mp4"
    }
    
    status_code = 200
    if range_header:
        status_code = 206
        if end_byte is not None:
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/*"
        else:
            headers["Content-Range"] = f"bytes {start_byte}-/*"

    return StreamingResponse(
        stream_segments(), 
        status_code=status_code,
        headers=headers
    )
