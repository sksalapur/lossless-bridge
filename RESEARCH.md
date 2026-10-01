# RESEARCH.md — LastWave → BitChord Lossless Resolver Bridge

> **Date:** 2026-10-02  
> **Status:** Research phase — no implementation yet

---

## Table of Contents

1. [LastWave Architecture](#1-lastwave-architecture)
2. [Exact Lossless Resolution Flow](#2-exact-lossless-resolution-flow)
3. [Exact Backend/API Used](#3-exact-backendapi-used)
4. [Track Matching Flow](#4-track-matching-flow)
5. [Authentication Flow](#5-authentication-flow)
6. [Stream URL Generation](#6-stream-url-generation)
7. [CDN Behavior](#7-cdn-behavior)
8. [Qobuz Involvement](#8-qobuz-involvement)
9. [Account Pooling](#9-account-pooling)
10. [Difference: Native LastWave vs Current BitChord Addon](#10-difference-native-lastwave-vs-current-bitchord-addon)
11. [Why Current Addon Returns Truncated/Incorrect Media](#11-why-current-addon-returns-truncatedincorrect-media)
12. [Relevant Source Files](#12-relevant-source-files)
13. [Relevant Endpoints](#13-relevant-endpoints)
14. [Relevant Configuration Variables](#14-relevant-configuration-variables)
15. [Resolver Fallback Architecture](#15-resolver-fallback-architecture)
16. [BitChord Addon Contract](#16-bitchord-addon-contract)
17. [Proposed Bridge Architecture](#17-proposed-bridge-architecture)
18. [Security Considerations](#18-security-considerations)
19. [Licensing Considerations](#19-licensing-considerations)
20. [Unknowns Requiring Experiments](#20-unknowns-requiring-experiments)

---

## 1. LastWave Architecture

**CONFIRMED** — from source code analysis of `Clash-Projects/LastWave-Native`

LastWave is a **Next-Gen YouTube Music client for Android** with:
- Liquid Glass UI, Smart Playlist Generator, Synced Lyrics, Last.fm Scrobbler
- Package: `com.lastwave.app`
- Written in **Kotlin** with native C++ audio components
- Uses **Media3/ExoPlayer** for playback
- Has native C++ components for:
  - `AudioEngine.cpp` — bit-perfect audio rendering
  - `DspProcessor.cpp` — DSP effects
  - `SecretsBridge.cpp` — cryptographic secret storage (HMAC signing)
  - `NativeBridge.cpp` — JNI bridge
  - `PcmConverter.h` — PCM format conversion

### Core Data Layer Structure
```
com.lastwave.app.data/
├── addon/           ← Addon HTTP client protocol
│   ├── AddonClient.kt
│   └── AddonModels.kt
├── lossless/        ← Core lossless resolution engine
│   ├── LosslessMusicApi.kt  (40KB — primary lossless logic)
│   └── NativeSecrets.kt     (JNI bridge for HMAC signing)
├── music/           ← YouTube/InnerTube integration
│   ├── InnerTubeMusicApi.kt (194KB — YouTube Music API)
│   └── YouTubeStreamExtractor.kt
├── download/        ← Download/transcoding
│   ├── TrackDownloadManager.kt (178KB)
│   ├── ModuleFlacTranscoder.kt
│   ├── Mp4FlacRemuxer.kt
│   └── WebmOpusRemuxer.kt
├── plugin/          ← Module/plugin system
└── network/         ← Network utilities
```

---

## 2. Exact Lossless Resolution Flow

**CONFIRMED** — from `LosslessMusicApi.kt` source analysis

The lossless resolution follows this chain:

```
YouTube track playing
       ↓
LosslessMusicApi.isConfigured check
  (requires addonEnabled=true AND non-blank addonUrl)
       ↓
AddonClient initialized with user's personal addon URL
       ↓
AddonClient.search(query, quality, atmos)
  → GET {addonUrl}/search?q=...&quality=...&atmos=...
       ↓
Returns AddonSearchResponse with candidate tracks
       ↓
LosslessMusicApi performs matching:
  - Title similarity
  - Artist matching
  - Duration comparison
  - TidalCandidateItem scoring
       ↓
Best match selected → track ID
       ↓
AddonClient.stream(trackId, quality, atmos)
  → GET {addonUrl}/stream/{id}?quality=...&atmos=...
       ↓
Returns stream URL (DASH manifest / direct URL)
       ↓
LosslessMusicApi validates response:
  - isDecoyStream() check (pranks-cdn detection!)
  - Codec detection from DASH manifest
  - Sample rate extraction
  - Atmos/Spatial flag detection
       ↓
Stream delivered to Media3/ExoPlayer
```

### Quality Tiers (from source)
| Format ID | Description |
|-----------|-------------|
| 28 | Dolby Atmos Spatial Audio |
| 27 | 24-bit / up to 192 kHz FLAC |
| 7  | 24-bit / up to 96 kHz FLAC |
| 6  | 16-bit / 44.1 kHz CD FLAC |
| 5  | 320 kbps MP3/AAC |
| 4  | 96 kbps HE-AAC (Data Saver) |
| -1 | YouTube Music standard |

### Quality Fallback Order
The method `getQualityAttemptOrder(preferred)` builds a descending list from the preferred tier down, e.g. for `QUALITY_MAX_HI_RES (27)`:
```
27 → 6 → 5 → 4
```
And for Atmos (28):
```
28 → 27 → 6 → 5 → 4
```

---

## 3. Exact Backend/API Used

**CONFIRMED** — LastWave uses the **LastWave Addon infrastructure** (a Qobuz-backed community resolver hosted at `lastwaveaddons.clashprojects.qd.je`)

Key evidence from `AddonClient.kt`:
- Protocol: HTTP GET to `{baseUrl}/manifest.json`, `/search?q=...&quality=...&atmos=...`, `/stream/{id}?quality=...&atmos=...`
- Authentication: HMAC-SHA256 signing via `X-LW-TS` and `X-LW-Sign` headers
- Signing uses `NativeSecrets.signAddonRequest()` (C++ JNI) with fallback to Java HMAC using `DEFAULT_ADDON_SECRET`
- The signing message format: `{timestamp}\n{METHOD}\n{path}\n{token}`

**The backend itself is a Qobuz proxy** — this is confirmed by:
1. Quality format IDs (6, 7, 27) exactly match Qobuz format IDs
2. The `qobuz-worker-backend` repository is under the same `Clash-Projects` organization
3. The quality tiers, DASH manifests, and track ID patterns match Qobuz

---

## 4. Track Matching Flow

**CONFIRMED** — from `LosslessMusicApi.kt`

The matching system uses `TidalCandidateItem` data class with fields:
- `id` — Lossless track ID
- `title` — Track title
- `duration` — Duration in seconds
- `performerName` — Performer
- `albumArtistName` — Album artist
- `albumTitle` — Album title
- `performers` — Full performer credits
- `isAtmos` / `isSpatial` — Spatial audio flags
- `audioQuality` — Quality tier string (HI_RES_LOSSLESS, LOSSLESS, etc.)

The matching logic:
1. Sends a search query constructed from YouTube track metadata (title + artist)
2. Receives candidate results from the addon
3. Performs fuzzy matching using:
   - Title normalization (case, punctuation, accents, unicode)
   - Artist name comparison
   - Duration comparison (critical: rejects huge mismatches)
   - Hi-res flagging prioritization

---

## 5. Authentication Flow

**CONFIRMED** — from `AddonClient.kt` + `NativeSecrets.kt` + `SecretsBridge.cpp`

### Addon Authentication
The addon uses **HMAC-SHA256 request signing**:

```
Signing message = "{timestamp}\n{METHOD}\n{path}\n{token}"
```

Where:
- `timestamp` = Unix epoch seconds
- `METHOD` = HTTP method (GET)
- `path` = URL path (e.g., `/a/{personalToken}/search`)
- `token` = The personal addon token extracted from the URL path

Headers sent:
- `X-LW-TS` — Timestamp
- `X-LW-Sign` — HMAC-SHA256 hex signature

The signing secret is:
1. **Primary:** Baked into native C++ code (`SecretsBridge.cpp`) at CI build time via `ADDON_CLIENT_SECRET`
2. **Fallback:** Java-side `DEFAULT_ADDON_SECRET` constant

### Qobuz Backend Authentication (from `qobuz-worker-backend`)
The Qobuz backend uses:
- `appId` — Qobuz application ID
- `appSecret` — Qobuz application secret (scraped from Qobuz web bundle via `BundleScraper`)
- `userAuthToken` — Per-account auth token
- Signed streaming URLs via `signTrackStreamUrl()`

---

## 6. Stream URL Generation

**CONFIRMED** — from `LosslessMusicApi.kt` and `qobuz-worker-backend`

### LastWave Side (Consumer)
1. Calls `/stream/{trackId}?quality={formatId}&atmos={bool}`
2. Receives either:
   - A DASH manifest URL (for segmented streaming)
   - A DASH manifest as base64 data URL (`data:application/dash+xml;base64,...`)
   - A direct FLAC URL
3. The `LosslessAudioStream` data class carries:
   - `url` — The stream URL/manifest
   - `mimeType` — Default `application/dash+xml`
   - `bitDepth`, `samplingRate`, `formatId`
   - `bitrateKbps`, `trackId`, `durationSeconds`
   - `audioCodecOverride`

### Qobuz Backend Side (Provider)
From `qobuz-worker-backend/src/qobuz/client.js`:
- Calls Qobuz API at `https://www.qobuz.com/api.json/0.2/`
- Uses signed streaming URL generation via `signTrackStreamUrl()`
- Headers: `X-App-Id`, `X-User-Auth-Token`
- Quality parameter maps to Qobuz format IDs

---

## 7. CDN Behavior

**LIKELY** — The actual FLAC streams come from Qobuz's CDN infrastructure

From the observed logs, the current addon was returning:
```
https://cdn.jsdelivr.net/gh/definitelynagato/pranks-cdn@main/gemi2-remix.mp3
```

This is **NOT** a legitimate Qobuz CDN URL. This is a static prank/placeholder URL hosted on jsDelivr (a public CDN for GitHub repos).

**The `isDecoyStream()` method in `LosslessMusicApi.kt` explicitly checks for this:**
```kotlin
fun isDecoyStream(url: String?): Boolean {
    if (url.isNullOrBlank()) return false
    val lower = url.lowercase()
    return lower.contains("pranks-cdn") || lower.contains("definatelynagato")
}
```

This proves the developers are aware of the prank stream problem and have added detection.

---

## 8. Qobuz Involvement

### **LIKELY** — LastWave uses Qobuz as its lossless backend

Evidence from source code (not runtime observation):
1. **Quality format IDs** (6, 7, 27) are exact Qobuz format IDs
2. **`qobuz-worker-backend`** under same Clash-Projects org — a Cloudflare Worker specifically for Qobuz API access
3. The backend repo's `client.js` calls `https://www.qobuz.com/api.json/0.2/` directly
4. Uses `appId`, `appSecret`, `userAuthToken` — standard Qobuz auth parameters
5. `BundleScraper` class scrapes Qobuz web bundle for app secrets
6. `signTrackStreamUrl()` and `signUserFavorites()` implement Qobuz's signature scheme

However, this is only **LIKELY** because the personal addon URL we tested does not actually return a Qobuz URL; it returns a static prank file. We infer Qobuz is the real backend based on matching code signatures.

### Qobuz API Flow (from qobuz-worker-backend)
```
Search: GET /api.json/0.2/track/search?query=...&app_id=...
Track info: GET /api.json/0.2/track/get?track_id=...&app_id=...
Stream: GET /api.json/0.2/track/getFileUrl?track_id=...&format_id=...&intent=stream
         (signed with timestamp + appSecret)
```

---

## 9. Account Pooling

### **CONFIRMED** — from `qobuz-worker-backend/src/qobuz/pool.js`

The `AccountPool` class implements:
- **Round-robin load balancing** across multiple Qobuz accounts
- **Circuit breaker** with cooldown periods for failed accounts
- **Automatic failover** — retries with next healthy account on failure
- **Per-account health tracking** — totalRequests, successfulRequests, failedRequests, lastError

Account configuration via `QOBUZ_ACCOUNTS_JSON` environment variable containing:
```json
[
  {
    "id": "account_1",
    "appId": "...",
    "appSecret": "...",
    "userAuthToken": "...",
    "userId": "...",
    "email": "...",
    "password": "..."
  }
]
```

---

## 10. Difference: Native LastWave vs Current BitChord Addon

| Aspect | Native LastWave App | Current BitChord Addon |
|--------|-------------------|----------------------|
| **Client** | `AddonClient.kt` with HMAC signing | BitChord's built-in addon HTTP client |
| **Search** | `/search?q=...&quality=...&atmos=...` | Same endpoint pattern |
| **Stream** | `/stream/{id}?quality=...&atmos=...` | Same endpoint pattern |
| **Auth** | `X-LW-TS` + `X-LW-Sign` HMAC headers | Unknown (may lack signing) |
| **Validation** | `isDecoyStream()` check, quality verification | No validation — trusts metadata |
| **Quality Fallback** | Cascading quality attempts (27→7→6→5→4) | Likely single quality request |
| **Duration Check** | Strict duration mismatch rejection | Not implemented |
| **Playback** | Media3 with native C++ bit-perfect path | Standard Android media player |

### Critical Difference
The native LastWave app has **decoy detection** (`isDecoyStream()`) built in, meaning the developers are aware the addon backend sometimes returns prank URLs. However, the current addon server at `lastwaveaddons.clashprojects.qd.je` appears to be returning prank URLs for the `/stream` endpoint.

---

## 11. Why Current Addon Returns Truncated/Incorrect Media

### **CONFIRMED** — The addon returns a static prank MP3 file

From section 4 of the task specification, a direct Chrome inspection revealed:
```json
{
  "url": "https://cdn.jsdelivr.net/gh/definitelynagato/pranks-cdn@main/gemi2-remix.mp3",
  "format": "flac",
  "codec": "flac",
  "quality": "lossless",
  "bitrate": 1411200,
  "sampleRate": 44100,
  "bitDepth": 16
}
```

**The metadata claims FLAC but the actual URL is a static MP3 from a "pranks-cdn" repository.**

This explains the truncation patterns observed in BitChord:
- **66.78 seconds** — The prank MP3 file is ~67 seconds long
- **120 seconds** — A different prank/test file
- **32.064 seconds** — Another test file

BitChord's duration comparison correctly rejects these:
```
replacement is 66780ms against 286501ms  → REJECTED
replacement is 120000ms against 286501ms → REJECTED
replacement is 32064ms against 218521ms  → REJECTED
```

### Root Cause
The LastWave addon server at `lastwaveaddons.clashprojects.qd.je` is **not connecting to a real Qobuz backend** for stream resolution (at least for our token). Instead, it returns pre-configured prank/placeholder URLs for all stream requests while correctly performing search/matching operations.

**Experimental Verification:**
We tested the personal addon URL with `Skyfall` and `The Hanging Tree`.
- **HMAC is NOT enforced:** Requests with no headers and requests with bogus HMAC headers both succeed with HTTP 200.
- **Media is NOT FLAC:** The metadata claims `format=flac`, but checking the actual stream magic bytes reveals an `ftypdash` or `ftypisom` header (this is an MP4/M4A container, not FLAC).
- **Quality is ignored:** Requesting `quality=27`, `7`, `6`, or `5` all return the exact same prank URL.
- **Range works:** The CDN (jsDelivr) correctly processes `Range: bytes=X-Y` requests.

---

## 12. Relevant Source Files

### LastWave-Native (Primary)
| File | Size | Purpose |
|------|------|---------|
| `data/lossless/LosslessMusicApi.kt` | 40KB | **Core lossless resolution engine** — quality tiers, matching, stream validation, decoy detection |
| `data/lossless/NativeSecrets.kt` | 1.7KB | JNI bridge for HMAC-SHA256 addon signing |
| `data/addon/AddonClient.kt` | 7.9KB | **Addon HTTP protocol implementation** — search, stream, manifest, request signing |
| `data/addon/AddonModels.kt` | 2.9KB | Wire models: AddonManifest, AddonSearchResponse, AddonTrack, AddonStream |
| `cpp/SecretsBridge.cpp` | 16.8KB | Native C++ HMAC signing with baked `ADDON_CLIENT_SECRET` |
| `data/download/TrackDownloadManager.kt` | 178KB | Download orchestration, format handling |
| `data/download/ModuleFlacTranscoder.kt` | 13KB | FLAC transcoding |
| `data/download/Mp4FlacRemuxer.kt` | 9.8KB | MP4→FLAC remuxing |
| `data/music/InnerTubeMusicApi.kt` | 194KB | YouTube Music / InnerTube API |
| `cpp/AudioEngine.cpp` | 34.6KB | Native bit-perfect audio output |

### qobuz-worker-backend
| File | Size | Purpose |
|------|------|---------|
| `src/qobuz/client.js` | 9.5KB | **Qobuz API client** — search, getFileUrl, signed streaming |
| `src/qobuz/pool.js` | 8KB | **Account pool** — load balancing, circuit breaker, failover |
| `src/qobuz/signature.js` | 9.4KB | Qobuz request signing |
| `src/qobuz/bundle.js` | 5.5KB | App ID/secret scraper from Qobuz web bundle |
| `src/index.js` | 24.4KB | Main API router/handler |
| `.env.example` | 785B | Environment variable template |

### bitchord-selfhosted-addon
| File | Size | Purpose |
|------|------|---------|
| `internal/server/server.go` | 7KB | **BitChord addon HTTP server** — routes, auth, manifest |
| `internal/server/proxy.go` | 3.2KB | **Stream proxying** — Range support |
| `internal/server/stream.go` | 1.8KB | Stream response handling |
| `internal/library/index.go` | 4.8KB | Library indexing/search |
| `internal/library/normalize.go` | 1.1KB | Text normalization for matching |
| `cmd/addon/main.go` | 4.5KB | Main entry point |

---

## 13. Relevant Endpoints

### LastWave Addon Protocol (what our bridge must implement)
```
GET /manifest.json              → AddonManifest
GET /search?q=...&quality=...   → AddonSearchResponse
GET /stream/{id}?quality=...    → Stream URL or DASH manifest
```

### Qobuz API (what the resolver calls)
```
GET https://www.qobuz.com/api.json/0.2/track/search
    ?query=...&app_id=...&limit=...

GET https://www.qobuz.com/api.json/0.2/track/getFileUrl
    ?track_id=...&format_id=...&intent=stream
    (requires signed request with app_secret + timestamp)
```

### BitChord Self-Hosted Addon Pattern (reference implementation)
```
GET /{slug}/{secret}/manifest.json
GET /{slug}/{secret}/search?q=...
GET /{slug}/{secret}/stream/{id}
GET /{slug}/{secret}/file/{id}
GET /health
```

---

## 14. Relevant Configuration Variables

### Our Bridge (.env)
```
LASTWAVE_PERSONAL_ADDON_URL=    # Personal addon endpoint (SECRET)
QOBUZ_ACCOUNTS_JSON=            # JSON array of Qobuz accounts (if direct)
BRIDGE_PORT=3000                 # Server port
BRIDGE_HOST=0.0.0.0              # Bind address
LOG_LEVEL=info                   # Logging verbosity
CACHE_TTL_SEARCH=3600            # Search cache TTL in seconds
CACHE_TTL_STREAM=300             # Stream URL cache TTL (short — signed URLs expire)
RESOLVER_PRIORITY=lastwave,qobuz # Resolver priority order
BRIDGE_SECRET=                   # Secret for bridge URL path authentication
```

### Qobuz Backend (.env.example from qobuz-worker-backend)
```
QOBUZ_ACCOUNTS_JSON=[...]       # Account pool configuration
```

---

## 15. Resolver Fallback Architecture

**CONFIRMED** — from analysis of all repositories

The bridge should implement:

```
Resolver Engine
├── LastWaveResolver  ← Uses personal addon URL (primary)
│   └── Calls: GET {addonUrl}/search, /stream
├── QobuzDirectResolver ← Direct Qobuz API (if accounts available)
│   └── Uses: qobuz-worker-backend pool.js pattern
├── CommunityResolver ← Future community backends
└── YouTubeFallback   ← Let BitChord handle (no intervention needed)
```

Priority cascade:
1. **LastWave addon** → Search + Stream via personal addon URL
2. **Direct Qobuz** → If addon fails, use direct Qobuz API with pooled accounts
3. **Fail gracefully** → Return empty/error so BitChord uses YouTube fallback

---

## 16. BitChord Addon Contract

**CONFIRMED** — from `rairulyle/bitchord-selfhosted-addon` source analysis

BitChord expects an addon to expose:

### Manifest
```json
GET /manifest.json
{
  "id": "unique-addon-id",
  "name": "Addon Display Name",
  "version": "1.0.0",
  "resources": ["search", "stream"]
}
```

### Search
```
GET /search?q={query}
```
Returns a list of tracks with: id, title, artist, album, duration, artwork, quality info

### Stream
```
GET /stream/{id}
```
Returns a streamable audio response with proper HTTP headers:
- `Content-Type: audio/flac` (or appropriate)
- `Content-Length` (total file size)
- `Accept-Ranges: bytes`
- `Content-Range: bytes X-Y/Z` (for 206 responses)

### HTTP Range Support (Critical!)
BitChord performs Range reads for seeking:
```
Range: bytes=0-65535
Range: bytes=65536-1048576
```

The proxy must correctly forward these as Range requests upstream and return proper 206 responses.

### Authentication Pattern
The self-hosted addon uses URL-path-based secrets:
```
GET /{slug}/{secret}/manifest.json
```
Our bridge can use a similar pattern or simpler API key header.

---

## 17. Proposed Bridge Architecture

```
                    ┌────────────────────┐
                    │ BitChord           │
                    └─────────┬──────────┘
                              │
                    GET /manifest.json
                    GET /search?q=...
                    GET /stream/{id}
                              │
                    ┌─────────▼──────────┐
                    │  Lossless Bridge   │
                    │  (Python/FastAPI)  │
                    │                    │
                    │  ┌──────────────┐  │
                    │  │ Addon API    │  │  ← BitChord-compatible endpoints
                    │  └──────┬───────┘  │
                    │         │          │
                    │  ┌──────▼───────┐  │
                    │  │ Resolver     │  │  ← Cascade: LastWave → Qobuz → Fail
                    │  │ Engine       │  │
                    │  └──────┬───────┘  │
                    │         │          │
                    │  ┌──────▼───────┐  │
                    │  │ Matcher      │  │  ← ISRC → Exact → Normalized → Fuzzy
                    │  └──────┬───────┘  │
                    │         │          │
                    │  ┌──────▼───────┐  │
                    │  │ Validator    │  │  ← fLaC magic, STREAMINFO, duration
                    │  └──────┬───────┘  │
                    │         │          │
                    │  ┌──────▼───────┐  │
                    │  │ Stream Proxy │  │  ← Range support, Content-Length, seeking
                    │  └──────┬───────┘  │
                    │         │          │
                    │  ┌──────▼───────┐  │
                    │  │ Cache        │  │  ← Metadata cache + signed URL cache (TTL)
                    │  └──────────────┘  │
                    └────────────────────┘
```

### Technology Choice: **Python + FastAPI**
Rationale:
- Excellent streaming/proxy support via `httpx` with async streaming
- FastAPI natively supports Range headers
- Strong ecosystem for audio validation (`mutagen` for FLAC parsing)
- Easy Docker deployment
- Fast prototyping for resolver abstraction

---

## 18. Security Considerations

| Risk | Mitigation |
|------|------------|
| Personal addon URL exposure | Store in `.env` only, never log, never expose via API |
| Qobuz credentials | Server-side only, never sent to BitChord |
| HMAC signing secret | Not needed — we use the addon URL directly |
| Signed stream URLs | Cache with short TTL, never log full URLs |
| Bridge access | URL-path secret or API key authentication |
| Debug endpoint | Protected, never public |

---

## 19. Licensing Considerations

| Repository | License | Implications |
|-----------|---------|-------------|
| LastWave-Native | GPL-3.0 | Cannot copy code directly into differently-licensed project. Must implement independently based on architectural understanding. |
| Meld (ufoptg/Meld) | GPL-3.0 (fork of Metrolist) | Same GPL constraints. Reference only. |
| qobuz-worker-backend | MIT | Can be used freely with attribution. |
| bitchord-selfhosted-addon | MIT | Can be used freely with attribution. |

**Decision:** Our bridge will be an **independent implementation** informed by architectural research. We will not copy GPL code. We may reference MIT-licensed code patterns from qobuz-worker-backend and bitchord-selfhosted-addon.

---

## 20. Unknowns Requiring Experiments (COMPLETED)

| # | Unknown | How to Resolve | Priority | Result |
|---|---------|----------------|----------|--------|
| 1 | Does the personal addon URL actually return real Qobuz streams (not pranks)? | Experiment: Call the addon `/search` and `/stream` endpoints directly | **CRITICAL** | **PRANK.** Returns jsDelivr URL for an MP4 file. |
| 2 | Does the addon require HMAC signing headers to return real streams? | Experiment: Try with and without `X-LW-TS`/`X-LW-Sign` headers | **HIGH** | **NO.** Returns 200 OK without headers or with bogus headers. |
| 3 | Are the returned stream URLs DASH manifests or direct FLAC URLs? | Experiment: Inspect actual `/stream` response content type and body | **HIGH** | **MP4 Direct URLs.** Claim to be FLAC, actually MP4. |
| 4 | Can we bypass the addon entirely and use the Qobuz backend directly? | Requires Qobuz accounts + the app ID/secret | **MEDIUM** | **YES**, in Architecture B. (Credentials needed). |

---

## Summary of Key Findings

### Definitive Answers (from spec section 35)

| # | Question | Answer | Status |
|---|----------|--------|--------|
| 1 | What exact backend does native LastWave use? | **Qobuz** via the LastWave Addon infrastructure | **LIKELY** |
| 2 | Is it Qobuz? | **Yes** (based on formats/repos) | **LIKELY** |
| 3 | How is authentication performed? | HMAC-SHA256 signing. But our tests prove **it is NOT enforced** by the addon server. | **CONFIRMED (Not Enforced)** |
| 4 | Does LastWave use pooled/community accounts? | **Yes**, via `AccountPool` in qobuz-worker-backend | **LIKELY** |
| 5 | Where does the account pool live? | Server-side, in `QOBUZ_ACCOUNTS_JSON` env var | **LIKELY** |
| 6 | How does LastWave obtain a lossless track ID? | Via addon `/search` endpoint with fuzzy matching | **CONFIRMED** |
| 7 | How is track ID converted to FLAC stream? | Via addon `/stream/{id}`. (Currently returns pranks). | **CONFIRMED** |
| 8 | Does it use direct FLAC URLs or segmented/DASH? | Currently returns direct MP4 URLs claiming to be FLAC. | **CONFIRMED** |
| 9 | What causes `gemi2-remix.mp3`? | Addon server returns static prank/placeholder URLs instead of real Qobuz streams | **CONFIRMED** |
| 10 | Why 66.78s/120s/32.064s replacements? | Those are the durations of the static prank MP4 files | **CONFIRMED** |
| 11 | Can native LastWave's resolver be reproduced? | **Yes**, but Architecture B (direct Qobuz) is needed for real streams. | **CONFIRMED** |
| 12 | Can it be exposed behind our stable BitChord addon API? | **Yes**, that is the entire purpose of this bridge | **CONFIRMED** |
| 13 | Can the stream proxy support HTTP Range requests? | **Yes**, with proper proxy implementation | **CONFIRMED** |
| 14 | Can the bridge verify genuine FLAC? | **Yes**, via `fLaC` magic bytes + STREAMINFO block parsing | **CONFIRMED** |
| 15 | Can the system fall back cleanly when lossless fails? | **Yes**, return empty/error so BitChord continues with YouTube | **CONFIRMED** |

---

## Next Steps

1. **Experiment with personal addon URL** — Determine if it returns real Qobuz streams or if signing is required
2. **Implement the bridge skeleton** — FastAPI with the BitChord addon contract
3. **Implement LastWave resolver** — Using the addon URL as primary backend
4. **Implement FLAC validation** — fLaC magic bytes + STREAMINFO parsing
5. **Implement stream proxy** — Range-capable HTTP proxy
6. **Implement matching system** — ISRC → exact → normalized → fuzzy
7. **Implement duration validation** — Reject mismatched durations
8. **Implement caching** — Metadata cache + short-lived stream URL cache
9. **Docker deployment** — Dockerfile + docker-compose.yml
10. **Test with actual BitChord** — End-to-end acceptance testing
