import logging
from typing import List, Dict, Any, Optional
from fastapi import Response
from fastapi.responses import StreamingResponse
import httpx
from src.resolvers.lastwave import LastWaveResolver
from src.validation.flac_validator import FLACValidator
from src.streaming.proxy import stream_proxy
from src.config.settings import settings

logger = logging.getLogger(__name__)

class ResolverEngine:
    def __init__(self):
        self.resolvers = []
        self._track_cache = {}  # track_id -> {title, artist, album}
        self._stream_url_cache = {}  # track_id -> upstream_url
        priorities = [p.strip().lower() for p in settings.resolver_priority.split(",")]
        
        for p in priorities:
            if p == "lastwave":
                self.resolvers.append(LastWaveResolver())
            # Can add qobuz resolver here later
            
    async def search(self, query: str) -> List[Dict[str, Any]]:
        # For search, we can just use the first working resolver
        for resolver in self.resolvers:
            try:
                results = await resolver.search(query)
                if results:
                    # Enrich results with resolver ID so we know which one to stream from
                    for r in results:
                        r["_resolver"] = resolver.name
                        # Cache track metadata for download filename generation
                        track_id = str(r.get("id", ""))
                        if track_id:
                            self._track_cache[track_id] = {
                                "title": r.get("title", ""),
                                "artist": r.get("performerName", r.get("artist", "")),
                                "album": r.get("albumTitle", r.get("album", "")),
                            }
                    return results
            except Exception as e:
                logger.error(f"Error in resolver {resolver.name} search: {e}")
        return []

    async def resolve_stream_url(self, composite_track_id: str):
        """
        Resolves a track ID to an upstream stream URL.
        composite_track_id could be just the ID if we only have one resolver,
        or format like 'lastwave:12345'. We'll assume the primary resolver for now.
        """
        # Try all resolvers until one gives a valid stream URL
        for resolver in self.resolvers:
            stream_url = await resolver.get_stream(composite_track_id)
            if not stream_url:
                continue
                
            # Quick check if it's a known prank URL
            if "pranks-cdn" in stream_url.lower() or "definatelynagato" in stream_url.lower():
                logger.warning(f"[{resolver.name}] Prank URL detected, skipping: {stream_url}")
                continue
                
            logger.info(f"[{resolver.name}] Resolved stream URL for track {composite_track_id}")
            return stream_url
                
        # If all fail, return None so the route can throw 404
        return None

    def get_cached_track(self, track_id: str) -> dict:
        """Return cached track metadata from a previous search, or empty dict."""
        return self._track_cache.get(track_id, {})

    def cache_stream_url(self, track_id: str, url: str):
        """Cache the upstream URL so the proxy endpoint can retrieve it."""
        self._stream_url_cache[track_id] = url
        # Bound the cache to prevent memory leaks
        if len(self._stream_url_cache) > 256:
            oldest = next(iter(self._stream_url_cache))
            del self._stream_url_cache[oldest]

    def get_cached_stream_url(self, track_id: str) -> str:
        """Return cached upstream stream URL, or None."""
        return self._stream_url_cache.get(track_id)
