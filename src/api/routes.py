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
async def stream_track(secret: str, track_id: str):
    """
    Stream endpoint — returns the exact JSON provided by the LastWave resolver.

    LastWave provides FLAC streams from Tidal via DASH manifests (.mpd).
    The upstream JSON includes fields like `dataUrl` (the DASH manifest itself),
    `format: "dash"`, `manifest: "dash"`, etc.

    We cannot proxy this through a simple byte-piping proxy because BitChord 
    needs to parse the DASH manifest and request the individual segments.
    Fortunately, Tidal's MPD URLs are pre-signed (Policy/Signature/Key-Pair-Id) 
    and can be fetched directly by BitChord without any special headers.

    So we just pass the upstream JSON through. BitChord's ExoPlayer handles the rest.
    """
    verify_secret(secret)

    stream_data = await engine.resolve_stream_url(track_id)

    if not stream_data:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")

    # If LastWave provides a dataUrl (inline base64 manifest), we can use it 
    # directly as the url to save BitChord a network request, but if not we
    # just return what LastWave gave us.
    if stream_data.get("dataUrl"):
        stream_data["url"] = stream_data["dataUrl"]
        del stream_data["dataUrl"]

    logger.info(
        f"Stream resolved for track {track_id}: "
        f"url={stream_data.get('url', '')[:80]}... "
        f"format={stream_data.get('format')} "
        f"manifest={stream_data.get('manifest')}"
    )

    return stream_data
