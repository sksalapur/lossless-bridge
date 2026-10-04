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
    Stream endpoint — returns JSON with a URL pointing back to this bridge's
    own /proxy endpoint, so all bytes flow through FLAC validation.

    BitChord uses /stream for BOTH playback and downloads:
    1. Calls GET /stream/{id} → receives JSON with {url, format, codec, ...}
    2. Opens the url for playback OR downloads directly from it

    Previously this returned the raw upstream URL, which served MP4/M4A bytes
    despite claiming FLAC metadata. Now the url points to our own /proxy
    endpoint, which fetches from upstream, validates the fLaC magic bytes,
    and serves verified FLAC with correct Content-Type and Content-Disposition.
    """
    verify_secret(secret)
    
    stream_url = await engine.resolve_stream_url(track_id)
    
    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")
    
    # Cache the upstream URL so /proxy can retrieve it
    engine.cache_stream_url(track_id, stream_url)
    
    # Build the self-referencing proxy URL
    # BitChord will hit this URL for both playback and download
    proxy_url = str(request.url_for("proxy_stream", secret=secret, track_id=track_id))
    
    # Build filename from cached metadata for Content-Disposition
    track_meta = engine.get_cached_track(track_id)
    
    logger.info(
        f"Stream request for track {track_id}: "
        f"upstream={stream_url[:80]}... → proxy={proxy_url}"
    )

    return {
        "url": proxy_url,
        "format": "flac",
        "codec": "flac",
        "container": "flac",
        "mimeType": "audio/flac",
        "quality": "Lossless",
        "audioQuality": "LOSSLESS",
        "bitDepth": 24,
        "sampleRate": 48000,
    }

@router.get("/{secret}/proxy/{track_id}", name="proxy_stream")
async def proxy_stream(secret: str, track_id: str, request: Request):
    """
    Byte-level FLAC proxy — the URL that BitChord actually fetches audio from.

    This endpoint:
    1. Retrieves the cached upstream URL for this track
    2. Fetches the actual bytes from the upstream source
    3. Validates the stream starts with fLaC magic bytes + STREAMINFO
    4. Sets Content-Type: audio/flac
    5. Sets Content-Disposition with .flac filename
    6. Supports HTTP Range requests for seeking/resume
    7. Returns 404 if validation fails (upstream is not genuine FLAC)

    This is what makes the download produce a .flac file instead of .m4a:
    BitChord downloads from HERE (validated FLAC bytes + correct headers)
    instead of from the upstream (which may serve MP4/M4A despite claiming FLAC).
    """
    verify_secret(secret)
    
    # Get the real upstream URL from cache
    upstream_url = engine.get_cached_stream_url(track_id)
    
    if not upstream_url:
        # If not cached, try resolving fresh
        upstream_url = await engine.resolve_stream_url(track_id)
    
    if not upstream_url:
        raise HTTPException(status_code=404, detail="Stream not found")
    
    # Build a proper filename from cached search metadata
    track_meta = engine.get_cached_track(track_id)
    if track_meta and track_meta.get("title"):
        parts = [track_meta["title"]]
        if track_meta.get("artist"):
            parts.append(track_meta["artist"])
        filename = " - ".join(parts) + ".flac"
    else:
        filename = f"{track_id}.flac"
    
    logger.info(f"Proxy request for track {track_id} → filename: {filename}")
    
    # Proxy the actual bytes through with FLAC validation
    response = await stream_proxy(
        url=upstream_url,
        range_header=request.headers.get("Range"),
        filename=filename
    )
    
    if response is None:
        logger.error(
            f"Proxy failed for track {track_id}: "
            f"stream validation failed — upstream did not return genuine FLAC"
        )
        raise HTTPException(
            status_code=404,
            detail="Stream validation failed — upstream did not return genuine FLAC"
        )
    
    return response
