import httpx
from fastapi.responses import StreamingResponse
from fastapi import Response
import logging
from src.validation.flac_validator import FLACValidator

logger = logging.getLogger(__name__)

async def stream_proxy(url: str, range_header: str = None):
    """
    Proxies a stream from the upstream URL, supporting Range requests.
    Validates that the stream is actually FLAC on initial request (or range 0-).
    """
    headers = {}
    if range_header:
        headers["Range"] = range_header
        
    client = httpx.AsyncClient(follow_redirects=True)
    
    # Send request upstream
    req = client.build_request("GET", url, headers=headers)
    resp = await client.send(req, stream=True)
    
    if resp.status_code not in (200, 206):
        logger.error(f"Upstream returned {resp.status_code}")
        await resp.aclose()
        return None
        
    # Validation step: If we are starting from byte 0, check magic bytes
    if not range_header or range_header.startswith("bytes=0-"):
        # We need to peek at the first 42+ bytes to validate
        # We'll read a small chunk, validate, then yield it along with the rest
        try:
            chunk = await resp.aiter_bytes(chunk_size=8192).__anext__()
            val = FLACValidator.validate_header(chunk)
            if not val["is_valid"]:
                # Try to decode the chunk as text to see if it's an XML/HTML error message
                error_body = ""
                try:
                    error_body = " - Body: " + chunk[:256].decode("utf-8")
                except:
                    pass
                logger.error(f"Stream validation failed: {val['error']}{error_body}")
                await resp.aclose()
                return None
            logger.info(f"Stream validated successfully. Meta: {val['metadata']}")
            
            # Create an async generator that yields the first chunk then the rest
            async def generate_with_peek():
                yield chunk
                async for c in resp.aiter_bytes(chunk_size=65536):
                    yield c
                await resp.aclose()
                await client.aclose()
                
            gen = generate_with_peek()
        except StopAsyncIteration:
            logger.error("Upstream stream ended immediately")
            await resp.aclose()
            return None
    else:
        # Just stream it (it's a mid-file seek, we already validated on 0-)
        async def generate():
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                yield chunk
            await resp.aclose()
            await client.aclose()
            
        gen = generate()
        
    # Build response headers
    resp_headers = {}
    
    # Forward critical headers
    if "Content-Length" in resp.headers:
        resp_headers["Content-Length"] = resp.headers["Content-Length"]
    if "Content-Range" in resp.headers:
        resp_headers["Content-Range"] = resp.headers["Content-Range"]
    if "Accept-Ranges" in resp.headers:
        resp_headers["Accept-Ranges"] = resp.headers["Accept-Ranges"]
    else:
        resp_headers["Accept-Ranges"] = "bytes"
        
    # Force content type to audio/flac for valid streams
    resp_headers["Content-Type"] = "audio/flac"
    
    return StreamingResponse(
        gen, 
        status_code=resp.status_code, 
        headers=resp_headers
    )
