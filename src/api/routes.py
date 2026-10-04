from fastapi import APIRouter, HTTPException, Request, Response
from typing import Dict, Any, List
from src.resolvers.engine import ResolverEngine
from src.streaming.proxy import stream_proxy
from src.config.settings import settings
import logging

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
        "resources": ["search", "stream", "file"]
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
    Playback endpoint — returns the stream URL as JSON.
    BitChord uses this for real-time playback (already working).
    """
    verify_secret(secret)
    
    stream_url = await engine.resolve_stream_url(track_id)
    
    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")
        
    return {
        "url": stream_url,
        "format": "flac",
        "quality": "Lossless",
        "audioQuality": "LOSSLESS"
    }

@router.get("/{secret}/file/{track_id}")
async def download_track(secret: str, track_id: str, request: Request):
    """
    Download endpoint — proxies genuine FLAC bytes through the bridge.
    
    Unlike /stream (which returns a URL for playback), this endpoint:
    1. Fetches the actual bytes from the upstream FLAC source
    2. Validates the stream starts with fLaC magic bytes + STREAMINFO
    3. Sets Content-Type: audio/flac
    4. Sets Content-Disposition: attachment; filename="Track - Artist.flac"
    5. Supports HTTP Range requests for resume/seeking
    
    This ensures BitChord saves a genuine .flac file, not .m4a.
    """
    verify_secret(secret)
    
    stream_url = await engine.resolve_stream_url(track_id)
    
    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")
    
    # Build a proper filename from cached search metadata
    track_meta = engine.get_cached_track(track_id)
    if track_meta and track_meta.get("title"):
        parts = [track_meta["title"]]
        if track_meta.get("artist"):
            parts.append(track_meta["artist"])
        filename = " - ".join(parts) + ".flac"
    else:
        filename = f"{track_id}.flac"
    
    logger.info(f"Download request for track {track_id} -> filename: {filename}")
    
    # Proxy the actual FLAC bytes through the bridge with validation
    response = await stream_proxy(
        url=stream_url,
        range_header=request.headers.get("Range"),
        filename=filename
    )
    
    if response is None:
        logger.error(f"Download failed for track {track_id}: stream validation failed (not genuine FLAC)")
        raise HTTPException(
            status_code=404,
            detail="Stream validation failed — upstream did not return genuine FLAC"
        )
    
    return response
