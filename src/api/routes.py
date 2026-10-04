from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from src.resolvers.engine import ResolverEngine
from src.config.settings import settings
import httpx
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
async def stream_track(secret: str, track_id: str, request: Request):
    """
    Stream endpoint — returns JSON pointing to our own /file proxy.

    BitChord uses /stream for BOTH playback and downloads. The reference
    selfhosted addon (bitchord-selfhosted-addon) does the same thing:
    its toStreamJSON() returns URL: base + "/file/" + track.ID — pointing
    back to its own byte-level proxy, not a raw CDN link.

    Key fields from the BitChord addon developer docs:
      - url: absolute, directly playable media URL
      - codec: "flac" (most reliable codec signal, step 1 in formatOf())
      - container: "flac" (self-describing container)
      - manifest: "none" (critical: tells BitChord it's NOT DASH/HLS)
      - encrypted: false (required, protected renditions are refused)
    """
    verify_secret(secret)

    stream_url = await engine.resolve_stream_url(track_id)

    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")

    # Cache the upstream URL so /file can retrieve it
    engine.cache_stream_url(track_id, stream_url)

    # Build the self-referencing proxy URL — same pattern as the reference addon
    # The reference addon does: URL = base + "/file/" + track.ID
    file_url = str(request.url_for("proxy_file", secret=secret, track_id=track_id))

    logger.info(
        f"Stream resolved for track {track_id}: "
        f"upstream={stream_url[:80]}... → file={file_url}"
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
    Byte-level file proxy — same role as /file in the reference selfhosted addon.

    The reference addon's proxy.go:
    1. Resolves the track from its library
    2. Calls Backend.OpenFile() to get an HTTP response from upstream
    3. Forwards Range/If-Range request headers
    4. Forwards Content-Type/Content-Length/Content-Range/Accept-Ranges/ETag
       response headers
    5. Pipes upstream.Body to the client

    We do the same: fetch from the cached upstream URL, forward range headers,
    and pipe the bytes through without any content validation (the upstream
    is already verified by the resolver's prank-URL detection).
    """
    verify_secret(secret)

    # Get the real upstream URL from cache
    upstream_url = engine.get_cached_stream_url(track_id)

    if not upstream_url:
        # If not cached, try resolving fresh
        upstream_url = await engine.resolve_stream_url(track_id)

    if not upstream_url:
        raise HTTPException(status_code=404, detail="Stream not found")

    # Forward range headers like the reference addon does
    upstream_headers = {}
    if request.headers.get("Range"):
        upstream_headers["Range"] = request.headers["Range"]
    if request.headers.get("If-Range"):
        upstream_headers["If-Range"] = request.headers["If-Range"]

    try:
        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        req = client.build_request("GET", upstream_url, headers=upstream_headers)
        resp = await client.send(req, stream=True)

        if resp.status_code not in (200, 206, 416):
            logger.error(f"Upstream returned {resp.status_code} for track {track_id}")
            await resp.aclose()
            await client.aclose()
            raise HTTPException(status_code=502, detail="Upstream error")

        # Forward response headers like the reference addon's proxy.go
        forwarded_headers = {}
        for name in ("Content-Type", "Content-Length", "Content-Range",
                      "Accept-Ranges", "ETag", "Last-Modified"):
            if name in resp.headers:
                forwarded_headers[name] = resp.headers[name]

        # Override Content-Type to audio/flac if upstream doesn't set it properly
        if "Content-Type" not in forwarded_headers or "flac" not in forwarded_headers.get("Content-Type", ""):
            forwarded_headers["Content-Type"] = "audio/flac"

        # Ensure Accept-Ranges is set for seeking support
        if "Accept-Ranges" not in forwarded_headers:
            forwarded_headers["Accept-Ranges"] = "bytes"

        async def pipe_upstream():
            try:
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        logger.info(f"Proxying track {track_id}: status={resp.status_code}")

        return StreamingResponse(
            pipe_upstream(),
            status_code=resp.status_code,
            headers=forwarded_headers,
        )

    except httpx.HTTPError as e:
        logger.error(f"HTTP error proxying track {track_id}: {e}")
        raise HTTPException(status_code=502, detail="Upstream connection failed")
