from fastapi import APIRouter, HTTPException, Request
from src.resolvers.engine import ResolverEngine
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
        "resources": ["search", "stream"]
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
    Stream endpoint — returns JSON with the upstream URL and rich metadata.

    BitChord uses /stream for BOTH playback and downloads. It parses the
    JSON fields to determine the codec via formatOf() with this priority:
      1. codec field        → "flac"  ✅ (immediate match)
      2. container field    → "flac"  ✅
      3. mimeType field     → "flac"  ✅
      4. qualityText        → fallback
      5. URL extension      → fallback

    By including codec/container/mimeType, BitChord resolves to
    storable("flac") → Storable(extension="flac", mimeType="audio/flac")
    without needing to guess from the URL or quality text.
    """
    verify_secret(secret)

    stream_url = await engine.resolve_stream_url(track_id)

    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")

    logger.info(f"Stream resolved for track {track_id}: {stream_url[:80]}...")

    return {
        "url": stream_url,
        "format": "flac",
        "codec": "flac",
        "container": "flac",
        "mimeType": "audio/flac",
        "quality": "Lossless",
        "audioQuality": "LOSSLESS",
        "bitDepth": 24,
        "sampleRate": 48000,
    }
