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
                    return results
            except Exception as e:
                logger.error(f"Error in resolver {resolver.name} search: {e}")
        return []

    async def get_stream(self, composite_track_id: str, range_header: str = None):
        """
        composite_track_id could be just the ID if we only have one resolver,
        or format like 'lastwave:12345'. We'll assume the primary resolver for now.
        """
        # Simply try all resolvers until one gives a valid stream
        for resolver in self.resolvers:
            stream_data = await resolver.get_stream(composite_track_id)
            if not stream_data or "url" not in stream_data:
                continue
                
            stream_url = stream_data["url"]
            # Quick check if it's a known prank URL
            if "pranks-cdn" in stream_url.lower() or "definatelynagato" in stream_url.lower():
                logger.warning(f"[{resolver.name}] Prank URL detected, skipping: {stream_url}")
                continue
                
            # If it's a legitimate URL (like a pre-signed S3/DASH manifest), 
            # return the full data dict so the client gets bit/kHz metadata.
            return stream_data
                
        # If all fail, return None so the route can throw 404
        return None
