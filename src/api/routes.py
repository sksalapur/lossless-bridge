from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.resolvers.engine import ResolverEngine
from src.config.settings import settings
import httpx
import logging
import re
import asyncio
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
    verify_secret(secret)

    stream_data = await engine.resolve_stream_url(track_id)
    if not stream_data:
        raise HTTPException(status_code=404, detail="Lossless stream not found")

    mpd_url = stream_data.get("url")
    if not mpd_url:
        raise HTTPException(status_code=404, detail="No URL in upstream response")
        
    engine.cache_stream_url(track_id, mpd_url)

    file_url = str(request.url_for("proxy_dash", secret=secret, track_id=track_id))

    logger.info(f"Stream resolved: telling BitChord to fetch single file from {file_url}")

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
    async with httpx.AsyncClient() as client:
        resp = await client.get(mpd_url)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch MPD: {resp.status_code}")
        xml_text = resp.text
        
    xml_text = re.sub(r'\sxmlns="[^"]+"', '', xml_text, count=1)
    root = ET.fromstring(xml_text)
    
    segment_template = root.find(".//SegmentTemplate")
    if segment_template is None:
        raise Exception("No SegmentTemplate found in MPD")
        
    init_url = segment_template.get("initialization").replace("&amp;", "&")
    media_url_template = segment_template.get("media").replace("&amp;", "&")
    start_number = int(segment_template.get("startNumber", "1"))
    
    total_segments = 0
    timeline = segment_template.find(".//SegmentTimeline")
    if timeline is not None:
        for s in timeline.findall("S"):
            r = int(s.get("r", "0"))
            total_segments += (1 + r)
            
    if total_segments == 0:
        raise Exception("Could not determine total segments from SegmentTimeline")
        
    return init_url, media_url_template, start_number, total_segments

async def get_segment_size(client: httpx.AsyncClient, url: str) -> int:
    try:
        resp = await client.head(url)
        if resp.status_code == 200 and "Content-Length" in resp.headers:
            return int(resp.headers["Content-Length"])
    except Exception:
        pass
    return 0

@router.get("/{secret}/file/{track_id}", name="proxy_dash")
async def proxy_dash(secret: str, track_id: str, request: Request):
    """
    Downloads all DASH segments on the fly and pipes them as a single continuous file.
    Supports fast Range requests by concurrently mapping segment sizes via HEAD requests!
    """
    verify_secret(secret)
    mpd_url = engine.get_cached_stream_url(track_id)
    if not mpd_url:
        logger.info(f"MPD URL for {track_id} not in cache (multi-worker miss?), resolving dynamically...")
        stream_data = await engine.resolve_stream_url(track_id)
        if not stream_data or not stream_data.get("url"):
            raise HTTPException(status_code=404, detail="Could not resolve stream URL")
        mpd_url = stream_data["url"]
        engine.cache_stream_url(track_id, mpd_url)

    try:
        init_url, media_url_template, start_number, total_segments = await fetch_and_parse_mpd(mpd_url)
    except Exception as e:
        logger.error(f"DASH parsing failed for {track_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to parse upstream manifest")

    range_header = request.headers.get("Range")
    start_byte = 0
    end_byte = None
    if range_header and range_header.startswith("bytes="):
        parts = range_header.replace("bytes=", "").split("-")
        if parts[0]:
            start_byte = int(parts[0])
        if len(parts) > 1 and parts[1]:
            end_byte = int(parts[1])

    # Build the list of all segment URLs
    urls = [init_url] + [
        media_url_template.replace("$Number$", str(start_number + i)) 
        for i in range(total_segments)
    ]

    async def stream_segments():
        async with httpx.AsyncClient(timeout=30.0) as client:
            bytes_to_skip_globally = start_byte
            start_url_index = 0
            
            if start_byte > 0:
                logger.info(f"Mapping segment sizes for {track_id} to satisfy Range {start_byte}-")
                
                # Fetch sizes concurrently in batches of 10
                sizes = []
                for i in range(0, len(urls), 10):
                    batch = urls[i:i+10]
                    tasks = [get_segment_size(client, u) for u in batch]
                    batch_sizes = await asyncio.gather(*tasks)
                    
                    for size in batch_sizes:
                        if size == 0:
                            break
                        sizes.append(size)
                    
                    if len(sizes) < len(batch):
                        break # Stopped early due to failure or missing Content-Length
                        
                    if sum(sizes) > start_byte:
                        break

                # Safely skip entire segments that fall completely within the skip range
                total_mapped = 0
                for i, size in enumerate(sizes):
                    if total_mapped + size <= bytes_to_skip_globally:
                        total_mapped += size
                        start_url_index = i + 1
                    else:
                        break
                        
                bytes_to_skip_globally -= total_mapped
                logger.info(f"HEAD mapping skipped {start_url_index} segments ({total_mapped} bytes). Remaining to dynamically skip: {bytes_to_skip_globally}")

            bytes_yielded = 0
            urls_to_fetch = urls[start_url_index:]
            
            for seg_url in urls_to_fetch:
                try:
                    async with client.stream("GET", seg_url) as resp:
                        if resp.status_code != 200:
                            logger.error(f"Segment failed: {resp.status_code} for {seg_url}")
                            break
                            
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            # Skip logic within the stream
                            if bytes_to_skip_globally > 0:
                                if len(chunk) <= bytes_to_skip_globally:
                                    bytes_to_skip_globally -= len(chunk)
                                    continue
                                else:
                                    chunk = chunk[bytes_to_skip_globally:]
                                    bytes_to_skip_globally = 0
                                
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
