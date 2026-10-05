from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, RedirectResponse
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
    timescale = int(segment_template.get("timescale", "48000"))
    
    segment_durations = []
    timeline = segment_template.find(".//SegmentTimeline")
    if timeline is not None:
        for s in timeline.findall("S"):
            d = int(s.get("d"))
            r = int(s.get("r", "0"))
            for _ in range(1 + r):
                segment_durations.append(d)
            
    if not segment_durations:
        raise Exception("Could not determine segment durations from SegmentTimeline")
        
    return init_url, media_url_template, start_number, segment_durations, timescale

async def get_segment_size(client: httpx.AsyncClient, url: str) -> int:
    try:
        resp = await client.head(url)
        if resp.status_code == 200 and "Content-Length" in resp.headers:
            return int(resp.headers["Content-Length"])
    except Exception:
        pass
    return 0

import struct

@router.get("/{secret}/file/{track_id}", name="proxy_dash")
async def proxy_dash(secret: str, track_id: str, request: Request):
    """
    Downloads DASH segments on the fly and pipes them as a single continuous file.
    Dynamically injects a `sidx` (Segment Index) box to make the fMP4 file fully seekable!
    """
    verify_secret(secret)
    mpd_url = engine.get_cached_stream_url(track_id)
    if not mpd_url:
        logger.info(f"MPD URL for {track_id} not in cache, resolving dynamically...")
        stream_data = await engine.resolve_stream_url(track_id)
        if not stream_data or not stream_data.get("url"):
            raise HTTPException(status_code=404, detail="Could not resolve stream URL")
        mpd_url = stream_data["url"]
        engine.cache_stream_url(track_id, mpd_url)

    # Check cache for byte map
    byte_map = engine.get_cached_byte_map(track_id)
    
    if not byte_map:
        is_dash = ".mpd" in mpd_url or "manifest" in mpd_url or "dash" in mpd_url.lower()
        if not is_dash:
            # LastWave sometimes returns direct .m4a or .flac files (e.g. from cache or fallback CDNs)
            # We don't need to inject a sidx box for these because they are already complete files!
            # We can just redirect or proxy directly.
            logger.info(f"URL {mpd_url} does not look like an MPD. Returning direct redirect.")
            return RedirectResponse(mpd_url)

        try:
            init_url, media_url_template, start_number, segment_durations, timescale = await fetch_and_parse_mpd(mpd_url)
        except Exception as e:
            # If it failed to parse, maybe it wasn't XML after all.
            logger.error(f"DASH parsing failed for {track_id} (URL: {mpd_url}): {e}")
            logger.info("Falling back to direct redirect...")
            return RedirectResponse(mpd_url)

        total_segments = len(segment_durations)
        # Build the list of all media segment URLs
        media_urls = [
            media_url_template.replace("$Number$", str(start_number + i)) 
            for i in range(total_segments)
        ]

        # 1. Fetch the init segment fully (it's tiny, ~800 bytes)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(init_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail="Failed to fetch init segment")
            init_data = resp.content
            
            # 2. Get the sizes of all media segments concurrently
            logger.info(f"Building byte map and sidx box for {track_id} ({total_segments} segments)...")
            tasks = [get_segment_size(client, u) for u in media_urls]
            segment_sizes = await asyncio.gather(*tasks)

        # Ensure all sizes were retrieved successfully
        if 0 in segment_sizes:
            logger.warning("Failed to retrieve some segment sizes. Seek capability will be disabled.")
            sidx_data = b""
        else:
            # 3. Build the sidx box
            reference_count = len(segment_sizes)
            body = bytearray()
            body.extend(struct.pack(">IIIIHH", 1, timescale, 0, 0, 0, reference_count))
            
            for i, size in enumerate(segment_sizes):
                ref_info = size & 0x7FFFFFFF
                subseg_duration = segment_durations[i]
                sap_info = 0x90000000
                body.extend(struct.pack(">III", ref_info, subseg_duration, sap_info))
                
            box_size = 12 + len(body)
            header = struct.pack(">I4sI", box_size, b'sidx', 0)
            sidx_data = header + body
            
        # 4. Construct byte offsets
        segments_info = []
        current_offset = len(init_data) + len(sidx_data)
        for i, size in enumerate(segment_sizes):
            segments_info.append({
                "url": media_urls[i],
                "offset": current_offset,
                "size": size
            })
            if size > 0:
                current_offset += size
                
        byte_map = {
            "init_data": init_data,
            "sidx_data": sidx_data,
            "segments": segments_info,
            "total_size": current_offset if 0 not in segment_sizes else None
        }
        engine.cache_byte_map(track_id, byte_map)

    # --- Serve the stream ---
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
        current_byte_pos = start_byte
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Yield init data if requested
            if current_byte_pos < len(byte_map["init_data"]):
                skip = current_byte_pos
                chunk = byte_map["init_data"][skip:]
                if end_byte is not None:
                    remaining = (end_byte - current_byte_pos + 1) - bytes_yielded
                    chunk = chunk[:remaining]
                yield chunk
                bytes_yielded += len(chunk)
                current_byte_pos += len(chunk)
                
                if end_byte is not None and bytes_yielded >= (end_byte - start_byte + 1):
                    return

            # Yield sidx data if requested
            sidx_start_offset = len(byte_map["init_data"])
            sidx_end_offset = sidx_start_offset + len(byte_map["sidx_data"])
            if current_byte_pos < sidx_end_offset:
                skip = current_byte_pos - sidx_start_offset
                chunk = byte_map["sidx_data"][skip:]
                if end_byte is not None:
                    remaining = (end_byte - start_byte + 1) - bytes_yielded
                    chunk = chunk[:remaining]
                yield chunk
                bytes_yielded += len(chunk)
                current_byte_pos += len(chunk)
                
                if end_byte is not None and bytes_yielded >= (end_byte - start_byte + 1):
                    return
                    
            # Stream media segments
            for seg in byte_map["segments"]:
                seg_start = seg["offset"]
                seg_end = seg_start + seg["size"] if seg["size"] > 0 else float('inf')
                
                if current_byte_pos >= seg_end:
                    continue # Segment is entirely before our current position
                    
                # We need to stream this segment
                try:
                    async with client.stream("GET", seg["url"]) as resp:
                        if resp.status_code != 200:
                            logger.error(f"Failed to fetch {seg['url']}")
                            break
                            
                        # If size is known, we can verify boundaries
                        bytes_skipped_in_seg = current_byte_pos - seg_start if current_byte_pos > seg_start else 0
                        
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            if bytes_skipped_in_seg > 0:
                                if len(chunk) <= bytes_skipped_in_seg:
                                    bytes_skipped_in_seg -= len(chunk)
                                    continue
                                else:
                                    chunk = chunk[bytes_skipped_in_seg:]
                                    bytes_skipped_in_seg = 0
                                    
                            if end_byte is not None:
                                remaining = (end_byte - start_byte + 1) - bytes_yielded
                                if len(chunk) > remaining:
                                    chunk = chunk[:remaining]
                                    
                            if chunk:
                                yield chunk
                                bytes_yielded += len(chunk)
                                current_byte_pos += len(chunk)
                                
                            if end_byte is not None and bytes_yielded >= (end_byte - start_byte + 1):
                                return
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
            total_str = str(byte_map["total_size"]) if byte_map["total_size"] else "*"
            headers["Content-Range"] = f"bytes {start_byte}-{end_byte}/{total_str}"
        else:
            total_str = str(byte_map["total_size"]) if byte_map["total_size"] else "*"
            headers["Content-Range"] = f"bytes {start_byte}-/{total_str}"
            
    # Set Content-Length if we know the exact response size
    if byte_map["total_size"]:
        if end_byte is not None:
            headers["Content-Length"] = str((end_byte - start_byte) + 1)
        elif not range_header:
            headers["Content-Length"] = str(byte_map["total_size"])
        else:
            headers["Content-Length"] = str(byte_map["total_size"] - start_byte)

    return StreamingResponse(
        stream_segments(), 
        status_code=status_code,
        headers=headers
    )
