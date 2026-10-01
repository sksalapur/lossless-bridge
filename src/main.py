import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api import routes
from src.config.settings import settings

app = FastAPI(
    title="LastWave → BitChord Lossless Bridge",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", settings.bridge_port))
    uvicorn.run("src.main:app", host=settings.bridge_host, port=port, reload=True)
