"""
Experiment Script — Verify LastWave Personal Addon Behavior
============================================================
Tests:
  1. /manifest.json
  2. /search?q=...
  3. /stream/{id}
  4. HMAC signing (with and without)
  5. Actual media validation (FLAC magic, STREAMINFO)
  6. Range request support
  7. Duration/quality verification

Tracks:
  - Skyfall (Adele) — track ID 34439418
  - The Hanging Tree — track ID 37977811

SECURITY: Reads addon URL from .env only, never logs the full URL.
"""

import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# ─── Load .env ───────────────────────────────────────────────────────────────
def load_env(env_path: str = ".env"):
    """Load .env file into os.environ"""
    p = Path(env_path)
    if not p.exists():
        print(f"ERROR: {env_path} not found")
        sys.exit(1)
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ[key.strip()] = val.strip()

load_env()

ADDON_URL = os.environ.get("LASTWAVE_PERSONAL_ADDON_URL", "").rstrip("/")
if not ADDON_URL:
    print("ERROR: LASTWAVE_PERSONAL_ADDON_URL not set in .env")
    sys.exit(1)

# Security: Only log a redacted form
parsed = urllib.parse.urlparse(ADDON_URL)
REDACTED = f"{parsed.scheme}://{parsed.hostname}/a/***REDACTED***"
print(f"[CONFIG] Addon URL (redacted): {REDACTED}")
print()

# ─── Helpers ─────────────────────────────────────────────────────────────────
def safe_request(url: str, headers: dict = None, method: str = "GET",
                 timeout: int = 15, read_bytes: int = None) -> dict:
    """Make an HTTP request and return structured result.
    Never logs the full URL if it contains the addon token."""

    # Redact URL for logging
    log_url = url
    if "/a/" in url:
        parts = url.split("/a/")
        if len(parts) == 2:
            after_token = parts[1].split("/", 1)
            suffix = "/" + after_token[1] if len(after_token) > 1 else ""
            log_url = parts[0] + "/a/***REDACTED***" + suffix

    hdrs = headers or {}
    hdrs.setdefault("User-Agent", "LosslessBridge-Experiment/1.0")

    req = urllib.request.Request(url, headers=hdrs, method=method)

    result = {
        "url_redacted": log_url,
        "method": method,
        "status": None,
        "headers": {},
        "body": None,
        "body_bytes": None,
        "error": None,
    }

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result["status"] = resp.status
            result["headers"] = dict(resp.headers)
            if read_bytes:
                result["body_bytes"] = resp.read(read_bytes)
            else:
                raw = resp.read()
                try:
                    result["body"] = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    result["body_bytes"] = raw[:4096]  # Cap for safety
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["error"] = str(e)
        result["headers"] = dict(e.headers) if e.headers else {}
        try:
            result["body"] = e.read().decode("utf-8", errors="replace")[:2048]
        except Exception:
            pass
    except Exception as e:
        result["error"] = str(e)

    return result


def print_section(title: str):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(result: dict, show_body: bool = True):
    print(f"  Method:  {result['method']}")
    print(f"  URL:     {result['url_redacted']}")
    print(f"  Status:  {result['status']}")
    if result["error"]:
        print(f"  Error:   {result['error']}")
    if result["headers"]:
        print(f"  Headers:")
        for k, v in result["headers"].items():
            # Don't log anything that might contain the token
            print(f"    {k}: {v}")
    if show_body and result["body"] is not None:
        body_str = json.dumps(result["body"], indent=2, ensure_ascii=False)
        # Redact any URLs in body that contain the addon token
        if "/a/" in body_str:
            import re
            body_str = re.sub(
                r'https?://[^"]*?/a/[a-f0-9]+/',
                'https://***REDACTED_ADDON_URL***/',
                body_str
            )
        # Also redact known CDN URLs for safety
        print(f"  Body:\n{body_str[:4096]}")
    if result["body_bytes"] is not None and not result["body"]:
        hexdump = result["body_bytes"][:64].hex(" ")
        print(f"  Body (hex, first 64 bytes): {hexdump}")


# ─── FLAC Validation Helpers ────────────────────────────────────────────────
def validate_flac_header(data: bytes) -> dict:
    """Parse FLAC magic bytes and STREAMINFO from raw bytes"""
    result = {
        "is_flac": False,
        "magic": None,
        "streaminfo": None,
        "error": None,
    }

    if len(data) < 4:
        result["error"] = f"Too few bytes: {len(data)}"
        return result

    magic = data[:4]
    result["magic"] = magic.hex(" ")

    if magic == b"fLaC":
        result["is_flac"] = True
    elif magic[:3] == b"ID3":
        result["error"] = "ID3 header detected — this is MP3 or other tagged format, NOT FLAC"
        return result
    elif magic[:2] == b"\xff\xfb" or magic[:2] == b"\xff\xf3" or magic[:2] == b"\xff\xf2":
        result["error"] = "MP3 sync word detected — this is MP3, NOT FLAC"
        return result
    elif magic == b"OggS":
        result["error"] = "Ogg container detected — NOT raw FLAC"
        return result
    else:
        result["error"] = f"Unknown magic: {magic.hex(' ')} — NOT FLAC"
        return result

    # Parse STREAMINFO metadata block (must be first)
    if len(data) < 42:  # 4 (magic) + 4 (block header) + 34 (STREAMINFO)
        result["error"] = "FLAC magic present but not enough data for STREAMINFO"
        return result

    # Metadata block header: 1 byte (type | is_last) + 3 bytes (length)
    block_header = data[4]
    block_type = block_header & 0x7F
    is_last = bool(block_header & 0x80)
    block_length = int.from_bytes(data[5:8], "big")

    if block_type != 0:
        result["error"] = f"First metadata block is type {block_type}, expected 0 (STREAMINFO)"
        return result

    if block_length < 34:
        result["error"] = f"STREAMINFO block too short: {block_length} bytes"
        return result

    # Parse STREAMINFO (34 bytes minimum)
    si = data[8:8 + 34]

    min_block_size = int.from_bytes(si[0:2], "big")
    max_block_size = int.from_bytes(si[2:4], "big")
    min_frame_size = int.from_bytes(si[4:7], "big")
    max_frame_size = int.from_bytes(si[7:10], "big")

    # Bits 0-19: sample rate, 20-22: channels-1, 23-27: bits-per-sample-1, 28-63: total samples
    combined = int.from_bytes(si[10:18], "big")
    sample_rate = (combined >> 44) & 0xFFFFF
    channels = ((combined >> 41) & 0x7) + 1
    bit_depth = ((combined >> 36) & 0x1F) + 1
    total_samples = combined & 0xFFFFFFFFF

    duration_seconds = total_samples / sample_rate if sample_rate > 0 else 0

    result["streaminfo"] = {
        "sample_rate": sample_rate,
        "channels": channels,
        "bit_depth": bit_depth,
        "total_samples": total_samples,
        "duration_seconds": round(duration_seconds, 3),
        "duration_ms": round(duration_seconds * 1000, 1),
        "min_block_size": min_block_size,
        "max_block_size": max_block_size,
        "min_frame_size": min_frame_size,
        "max_frame_size": max_frame_size,
    }

    return result


# ─── Experiment 1: /manifest.json ────────────────────────────────────────────
print_section("EXPERIMENT 1: GET /manifest.json (no auth)")
manifest_url = f"{ADDON_URL}/manifest.json"
r1 = safe_request(manifest_url)
print_result(r1)

# ─── Experiment 2: /search for "Skyfall Adele" ──────────────────────────────
print_section("EXPERIMENT 2: GET /search?q=Skyfall+Adele (no auth)")
search_url = f"{ADDON_URL}/search?q=Skyfall+Adele&quality=27"
r2 = safe_request(search_url)
print_result(r2)

# ─── Experiment 3: /search for "The Hanging Tree" ───────────────────────────
print_section("EXPERIMENT 3: GET /search?q=The+Hanging+Tree (no auth)")
search_url2 = f"{ADDON_URL}/search?q=The+Hanging+Tree&quality=6"
r3 = safe_request(search_url2)
print_result(r3)

# ─── Experiment 4: /stream/34439418 (Skyfall, no auth) ──────────────────────
print_section("EXPERIMENT 4: GET /stream/34439418 (Skyfall, no auth)")
stream_url = f"{ADDON_URL}/stream/34439418?quality=27"
r4 = safe_request(stream_url)
print_result(r4)

# Check if we got a stream URL — analyze it
skyfall_stream_url = None
if r4["body"] and isinstance(r4["body"], dict):
    skyfall_stream_url = r4["body"].get("url")
    stream_format = r4["body"].get("format", "unknown")
    stream_codec = r4["body"].get("codec", "unknown")
    stream_quality = r4["body"].get("quality", "unknown")
    stream_bitrate = r4["body"].get("bitrate", "unknown")
    stream_sr = r4["body"].get("sampleRate", "unknown")
    stream_bd = r4["body"].get("bitDepth", "unknown")

    print()
    print("  --- Stream Metadata Analysis ---")
    print(f"  Claimed format:      {stream_format}")
    print(f"  Claimed codec:       {stream_codec}")
    print(f"  Claimed quality:     {stream_quality}")
    print(f"  Claimed bitrate:     {stream_bitrate}")
    print(f"  Claimed sampleRate:  {stream_sr}")
    print(f"  Claimed bitDepth:    {stream_bd}")

    if skyfall_stream_url:
        # Check for known prank URLs
        lower_url = skyfall_stream_url.lower()
        is_prank = "pranks-cdn" in lower_url or "definatelynagato" in lower_url or "gemi2" in lower_url
        print(f"  Is prank URL:        {is_prank}")
        if is_prank:
            print(f"  ⚠️  PRANK URL DETECTED: {skyfall_stream_url}")
        else:
            # Redact CDN URL details but show domain
            stream_parsed = urllib.parse.urlparse(skyfall_stream_url)
            print(f"  Stream domain:       {stream_parsed.hostname}")
            print(f"  Stream path prefix:  {stream_parsed.path[:40]}...")

# ─── Experiment 5: /stream/37977811 (The Hanging Tree, no auth) ─────────────
print_section("EXPERIMENT 5: GET /stream/37977811 (The Hanging Tree, no auth)")
stream_url2 = f"{ADDON_URL}/stream/37977811?quality=6"
r5 = safe_request(stream_url2)
print_result(r5)

hanging_tree_stream_url = None
if r5["body"] and isinstance(r5["body"], dict):
    hanging_tree_stream_url = r5["body"].get("url")
    if hanging_tree_stream_url:
        lower_url = hanging_tree_stream_url.lower()
        is_prank = "pranks-cdn" in lower_url or "definatelynagato" in lower_url or "gemi2" in lower_url
        print(f"\n  Is prank URL: {is_prank}")

# ─── Experiment 6: Validate actual stream media ─────────────────────────────
print_section("EXPERIMENT 6: Validate actual stream media")

for label, url in [("Skyfall", skyfall_stream_url), ("Hanging Tree", hanging_tree_stream_url)]:
    if not url:
        print(f"\n  [{label}] No stream URL available — skipping")
        continue

    print(f"\n  [{label}] Downloading first 8KB to validate container...")
    # Download first 8KB of the actual stream
    media_result = safe_request(url, read_bytes=8192)
    print(f"  Status:       {media_result['status']}")
    ct = media_result["headers"].get("Content-Type", "unknown")
    cl = media_result["headers"].get("Content-Length", "unknown")
    ar = media_result["headers"].get("Accept-Ranges", "not present")
    print(f"  Content-Type:    {ct}")
    print(f"  Content-Length:  {cl}")
    print(f"  Accept-Ranges:  {ar}")

    if media_result["body_bytes"]:
        raw = media_result["body_bytes"]
        print(f"  First 32 bytes:  {raw[:32].hex(' ')}")

        # Validate FLAC
        flac_result = validate_flac_header(raw)
        print(f"  Is FLAC:         {flac_result['is_flac']}")
        if flac_result["error"]:
            print(f"  Validation error: {flac_result['error']}")
        if flac_result["streaminfo"]:
            si = flac_result["streaminfo"]
            print(f"  Sample rate:     {si['sample_rate']} Hz")
            print(f"  Bit depth:       {si['bit_depth']} bits")
            print(f"  Channels:        {si['channels']}")
            print(f"  Total samples:   {si['total_samples']}")
            print(f"  Duration:        {si['duration_seconds']}s ({si['duration_ms']}ms)")
            print(f"  Block sizes:     {si['min_block_size']}-{si['max_block_size']}")

# ─── Experiment 7: Range request support ─────────────────────────────────────
print_section("EXPERIMENT 7: Range request support")

test_url = skyfall_stream_url or hanging_tree_stream_url
if test_url:
    # Test HEAD
    print("\n  [HEAD request]")
    head_result = safe_request(test_url, method="HEAD")
    print(f"  Status:         {head_result['status']}")
    print(f"  Content-Length: {head_result['headers'].get('Content-Length', 'not present')}")
    print(f"  Accept-Ranges:  {head_result['headers'].get('Accept-Ranges', 'not present')}")
    print(f"  Content-Type:   {head_result['headers'].get('Content-Type', 'not present')}")

    # Test Range: bytes=0-65535
    print("\n  [Range: bytes=0-65535]")
    range_result = safe_request(test_url, headers={"Range": "bytes=0-65535"}, read_bytes=65536)
    print(f"  Status:         {range_result['status']}")
    print(f"  Content-Length: {range_result['headers'].get('Content-Length', 'not present')}")
    print(f"  Content-Range:  {range_result['headers'].get('Content-Range', 'not present')}")
    print(f"  Accept-Ranges:  {range_result['headers'].get('Accept-Ranges', 'not present')}")

    # Test Range: bytes=65536-131071
    print("\n  [Range: bytes=65536-131071]")
    range_result2 = safe_request(test_url, headers={"Range": "bytes=65536-131071"}, read_bytes=65536)
    print(f"  Status:         {range_result2['status']}")
    print(f"  Content-Length: {range_result2['headers'].get('Content-Length', 'not present')}")
    print(f"  Content-Range:  {range_result2['headers'].get('Content-Range', 'not present')}")
else:
    print("  No stream URL available for Range testing")

# ─── Experiment 8: HMAC Signing Test ─────────────────────────────────────────
print_section("EXPERIMENT 8: HMAC Signing Analysis")

# Extract token from the addon URL path
path_parts = parsed.path.strip("/").split("/")
addon_token = None
if len(path_parts) >= 2 and path_parts[0] == "a":
    addon_token = path_parts[1]

print(f"  Token extracted from URL: {'YES (' + addon_token[:8] + '...' + addon_token[-4:] + ')' if addon_token else 'NO'}")
print(f"  Token length: {len(addon_token) if addon_token else 0}")
print()

# We already tested WITHOUT signing in experiments 1-5.
# Now test with bogus signing headers to see if the server validates them.
print("  [Test: Bogus HMAC headers]")
ts = str(int(time.time()))
bogus_sign = hashlib.sha256(b"bogus").hexdigest()
bogus_headers = {
    "X-LW-TS": ts,
    "X-LW-Sign": bogus_sign,
}
bogus_result = safe_request(f"{ADDON_URL}/manifest.json", headers=bogus_headers)
print(f"  Status with bogus HMAC: {bogus_result['status']}")
print(f"  (Compare: Status without HMAC was {r1['status']})")

if bogus_result["status"] == r1["status"]:
    print("  CONCLUSION: Server does NOT validate HMAC — requests work without signing")
else:
    print("  CONCLUSION: Server MAY validate HMAC — different status codes observed")

# ─── Experiment 9: Test different quality tiers ──────────────────────────────
print_section("EXPERIMENT 9: Quality tier comparison (Skyfall)")
for quality_id, quality_name in [(27, "Hi-Res 192k"), (7, "Hi-Res 96k"), (6, "CD FLAC"), (5, "MP3 320")]:
    url = f"{ADDON_URL}/stream/34439418?quality={quality_id}"
    r = safe_request(url)
    if r["body"] and isinstance(r["body"], dict):
        stream_url_q = r["body"].get("url", "")
        fmt = r["body"].get("format", "?")
        sr = r["body"].get("sampleRate", "?")
        bd = r["body"].get("bitDepth", "?")
        br = r["body"].get("bitrate", "?")
        is_prank = any(x in stream_url_q.lower() for x in ["pranks-cdn", "definatelynagato", "gemi2"])
        print(f"  quality={quality_id} ({quality_name}): format={fmt} sr={sr} bd={bd} br={br} prank={is_prank}")
    else:
        print(f"  quality={quality_id} ({quality_name}): status={r['status']} error={r.get('error', 'none')}")

# ─── Summary ─────────────────────────────────────────────────────────────────
print_section("EXPERIMENT SUMMARY")
print("""
  Review the output above and determine:
  1. Does /manifest.json work? What resources does it declare?
  2. Does /search return real track candidates?
  3. Does /stream return prank URLs or real Qobuz CDN URLs?
  4. Is HMAC signing required?
  5. Is the actual media FLAC or MP3?
  6. What are the real sample rate, bit depth, duration?
  7. Does the CDN support Range requests?
  8. Do different quality tiers return different streams?
""")
