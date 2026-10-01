from fastapi import APIRouter, HTTPException, Request, Response
from typing import Dict, Any, List
from src.resolvers.engine import ResolverEngine
from src.config.settings import settings
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
        "resources": ["search", "stream"]
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
    
    stream_url = await engine.get_stream(track_id, request.headers.get("Range"))
    
    if not stream_url:
        raise HTTPException(status_code=404, detail="Lossless stream not found or invalid")
        
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=stream_url)
