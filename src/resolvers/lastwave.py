import httpx
import logging
from typing import List, Dict, Any, Optional
from src.resolvers.base import BaseResolver
from src.config.settings import settings

logger = logging.getLogger(__name__)

class LastWaveResolver(BaseResolver):
    def __init__(self):
        self.addon_url = settings.lastwave_personal_addon_url
        if self.addon_url:
            self.addon_url = self.addon_url.rstrip('/')

    @property
    def name(self) -> str:
        return "lastwave"

    async def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.addon_url:
            return []
            
        url = f"{self.addon_url}/search"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params={"q": query, "quality": "27"})
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("tracks", [])
        except Exception as e:
            logger.error(f"LastWave resolver search error: {e}")
        return []

    async def get_stream(self, track_id: str) -> Optional[str]:
        if not self.addon_url:
            return None
            
        url = f"{self.addon_url}/stream/{track_id}"
        try:
            async with httpx.AsyncClient() as client:
                # Quality 27 = Hi-Res 192k
                resp = await client.get(url, params={"quality": "27"})
                if resp.status_code == 200:
                    data = resp.json()
                    # We return the URL here, but it will be validated by the engine/proxy
                    return data.get("url")
        except Exception as e:
            logger.error(f"LastWave resolver get_stream error: {e}")
        return None
