import httpx
import logging
import time
import hmac
import hashlib
import urllib.parse
import re
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

    def _get_headers(self, url: str, method: str) -> dict:
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36 LastWave/1.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9"
        }
        
        secret = "36d96a751b12ee481c281a8a8e64c482d0c1634a22061ab72f40175017818b85"
        parsed = urllib.parse.urlparse(url)
        path = parsed.path if parsed.path else "/"
        
        match = re.search(r'/a/([^/]+)', path)
        if match:
            token = match.group(1)
            ts = str(int(time.time()))
            message = f"{ts}\n{method.upper()}\n{path}\n{token}"
            
            signature = hmac.new(
                secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            headers["X-LW-TS"] = ts
            headers["X-LW-Sign"] = signature
            
        return headers

    async def search(self, query: str) -> List[Dict[str, Any]]:
        if not self.addon_url:
            return []
            
        url = f"{self.addon_url}/search"
        try:
            headers = self._get_headers(url, "GET")
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, params={"q": query, "quality": "27"}, headers=headers)
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
            headers = self._get_headers(url, "GET")
            async with httpx.AsyncClient() as client:
                # Quality 27 = Hi-Res 192k
                resp = await client.get(url, params={"quality": "27"}, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # We return the URL here, but it will be validated by the engine/proxy
                    return data.get("url")
        except Exception as e:
            logger.error(f"LastWave resolver get_stream error: {e}")
        return None
