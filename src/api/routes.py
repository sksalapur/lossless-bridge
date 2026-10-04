from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.resolvers.engine import ResolverEngine
from src.config.settings import settings
import httpx
import hmac
import hashlib
import time
import re
import urllib.parse
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

def _sign_url(url: str, method: str = "GET") -> dict:
    """Generate HMAC-signed headers for the LastWave addon, same as lastwave.py."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36 LastWave/1.0",
        "Accept": "*/*",
    }
    
    lw_secret = "36d96a751b12ee481c281a8a8e64c482d0c1634a22061ab72f40175017818b85"
    parsed = urllib.parse.urlparse(url)
    path = parsed.path if parsed.path else "/"
    
    match = re.search(r'/a/([^/]+)', path)
    if match:
        token = match.group(1)
        ts = str(int(time.time()))
        message = f"{ts}\n{method.upper()}\n{path}\n{token}"
        signature = hmac.new(
            lw_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        headers["X-LW-TS"] = ts
        headers["X-LW-Sign"] = signature
    
    return headers

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
    Stream endpoint — returns JSON pointing to our own /file proxy.
    Same pattern as the reference selfhosted addon's toStreamJSON().
    """
    verify_secret(secret)

    stream_url = await engine.resolve_stream_url(track_id)

    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")

    # Cache the upstream URL so /file can retrieve it
    engine.cache_stream_url(track_id, stream_url)

    # Build the self-referencing proxy URL
    file_url = str(request.url_for("proxy_file", secret=secret, track_id=track_id))

    logger.info(
        f"Stream resolved for track {track_id}: "
        f"upstream={stream_url[:120]} → file={file_url}"
    )

    return {
        "url": file_url,
        "format": "flac",
        "codec": "flac",
        "container": "flac",
        "manifest": "none",
        "encrypted": False,
        "quality": "Lossless",
        "audioQuality": "LOSSLESS",
        "bitDepth": 24,
        "sampleRate": 48000,
    }

@router.get("/{secret}/file/{track_id}", name="proxy_file")
async def proxy_file(secret: str, track_id: str, request: Request):
    """
    Byte-level file proxy — pipes upstream audio to BitChord.
    """
    verify_secret(secret)

    # Get the real upstream URL from cache
    upstream_url = engine.get_cached_stream_url(track_id)

    if not upstream_url:
        # If not cached, try resolving fresh
        upstream_url = await engine.resolve_stream_url(track_id)

    if not upstream_url:
        logger.error(f"No upstream URL for track {track_id} (not cached, resolve failed)")
        raise HTTPException(status_code=404, detail="Stream not found")

    logger.info(f"Proxy /file for track {track_id}: upstream={upstream_url[:120]}")

    # Build headers: HMAC signing for lastwaveaddons URLs + Range forwarding
    upstream_headers = _sign_url(upstream_url)
    
    if request.headers.get("Range"):
        upstream_headers["Range"] = request.headers["Range"]
    if request.headers.get("If-Range"):
        upstream_headers["If-Range"] = request.headers["If-Range"]

    try:
        client = httpx.AsyncClient(follow_redirects=True, timeout=60.0)
        req = client.build_request("GET", upstream_url, headers=upstream_headers)
        resp = await client.send(req, stream=True)

        logger.info(f"Upstream response for track {track_id}: status={resp.status_code} "
                     f"content-type={resp.headers.get('Content-Type', 'unknown')} "
                     f"content-length={resp.headers.get('Content-Length', 'unknown')}")

        if resp.status_code not in (200, 206, 416):
            body_preview = ""
            try:
                chunk = await resp.aread()
                body_preview = chunk[:256].decode("utf-8", errors="replace")
            except:
                pass
            logger.error(f"Upstream returned {resp.status_code} for track {track_id}: {body_preview}")
            await resp.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail=f"Upstream returned {resp.status_code}")

        # Forward response headers
        forwarded_headers = {}
        for name in ("Content-Type", "Content-Length", "Content-Range",
                      "Accept-Ranges", "ETag", "Last-Modified"):
            if name in resp.headers:
                forwarded_headers[name] = resp.headers[name]

        # Ensure correct content type
        ct = forwarded_headers.get("Content-Type", "")
        if "flac" not in ct and "octet" not in ct and "audio" not in ct:
            forwarded_headers["Content-Type"] = "audio/flac"

        if "Accept-Ranges" not in forwarded_headers:
            forwarded_headers["Accept-Ranges"] = "bytes"

        async def pipe_upstream():
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        return StreamingResponse(
            pipe_upstream(),
            status_code=resp.status_code,
            headers=forwarded_headers,
        )

    except httpx.HTTPError as e:
        logger.error(f"HTTP error proxying track {track_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=502, detail=f"Upstream connection failed: {type(e).__name__}")
