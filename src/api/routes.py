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
async def proxy_dash(secret: str, track_id: str):
    """
    Downloads all DASH segments on the fly and pipes them as a single continuous file.
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

    # Replace XML entities if they exist
    init_url = init_url.replace("&amp;", "&")
    media_url_template = media_url_template.replace("&amp;", "&")

    async def stream_segments():
        # Using a single client for connection pooling across the 70+ segments
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Stream the initialization segment
            try:
                async with client.stream("GET", init_url) as resp:
                    if resp.status_code == 200:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            yield chunk
                    else:
                        logger.error(f"Init segment failed: {resp.status_code}")
                        return
            except Exception as e:
                logger.error(f"Error streaming init segment: {e}")
                return

            # 2. Stream all media segments sequentially
            for i in range(total_segments):
                seg_num = start_number + i
                seg_url = media_url_template.replace("$Number$", str(seg_num))
                try:
                    async with client.stream("GET", seg_url) as resp:
                        if resp.status_code == 200:
                            async for chunk in resp.aiter_bytes(chunk_size=65536):
                                yield chunk
                        else:
                            logger.error(f"Segment {seg_num} failed: {resp.status_code}")
                            break
                except Exception as e:
                    logger.error(f"Error streaming segment {seg_num}: {e}")
                    break

    return StreamingResponse(
        stream_segments(), 
        media_type="audio/mp4",
        headers={"Accept-Ranges": "none"} # Prevent range requests since we generate on the fly
    )
