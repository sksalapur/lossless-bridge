# LastWave → BitChord Lossless Bridge

A standalone Python/FastAPI bridge that translates BitChord addon requests into verified, genuinely lossless FLAC streams by querying LastWave/Qobuz infrastructure.

## Features
- Complete BitChord Addon HTTP protocol support (`/manifest.json`, `/search`, `/stream`)
- Transparent HTTP `Range` proxying for seeking
- Strict FLAC validation (`fLaC` magic + `STREAMINFO` block parsing)
- Decoy/prank URL detection and rejection
- Clean fallback (returns 404 to BitChord if real FLAC is unavailable, triggering YouTube fallback)

## Setup

1. Copy `.env.example` to `.env`
2. Add your LastWave personal addon URL to `.env`
3. Run with Docker: `docker-compose up -d`
4. Add to BitChord using `http://your-server-ip:3000/secret`

## Architecture

The bridge is designed around a Resolver Engine. It currently implements:
- `LastWaveResolver` (using personal addon URL)
- Abstraction ready for a direct Qobuz resolver

If a stream is found, the **Streaming Proxy** checks the magic bytes before forwarding data. If the LastWave backend returns a prank (e.g. an MP4 file), the validator catches the mismatched magic bytes (`ftypdash` or `ftypisom`) and gracefully aborts.
