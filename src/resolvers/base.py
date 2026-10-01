from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class BaseResolver(ABC):
    @abstractmethod
    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Search for a track and return BitChord compatible track list"""
        pass

    @abstractmethod
    async def get_stream(self, track_id: str) -> Optional[str]:
        """Get the direct stream URL for a track_id"""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        pass
