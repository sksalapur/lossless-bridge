import struct
import logging

logger = logging.getLogger(__name__)

class FLACValidator:
    @staticmethod
    def validate_header(data: bytes) -> dict:
        """
        Validates FLAC magic bytes and extracts STREAMINFO.
        Returns a dict with 'is_valid', 'error', and 'metadata'.
        """
        result = {
            "is_valid": False,
            "error": None,
            "metadata": None
        }

        if len(data) < 42:
            result["error"] = "Insufficient data for FLAC header validation"
            return result

        magic = data[:4]
        if magic != b"fLaC":
            if magic[:3] == b"ID3":
                result["error"] = "ID3 header detected (MP3), not FLAC"
            elif magic[:2] in [b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"]:
                result["error"] = "MP3 sync word detected, not FLAC"
            elif magic == b"OggS":
                result["error"] = "Ogg container detected, not FLAC"
            elif data[4:8] == b"ftyp":
                result["error"] = "MP4/M4A container detected, not FLAC"
            else:
                result["error"] = f"Invalid magic bytes: {magic.hex()}"
            return result

        # Check for STREAMINFO block
        block_header = data[4]
        block_type = block_header & 0x7F
        
        if block_type != 0:
            result["error"] = f"Expected STREAMINFO block (0), got {block_type}"
            return result
            
        block_length = int.from_bytes(data[5:8], "big")
        if block_length < 34:
            result["error"] = f"STREAMINFO block too short: {block_length}"
            return result
            
        si = data[8:8+34]
        combined = int.from_bytes(si[10:18], "big")
        
        sample_rate = (combined >> 44) & 0xFFFFF
        channels = ((combined >> 41) & 0x7) + 1
        bit_depth = ((combined >> 36) & 0x1F) + 1
        total_samples = combined & 0xFFFFFFFFF
        
        duration_sec = total_samples / sample_rate if sample_rate > 0 else 0
        
        result["is_valid"] = True
        result["metadata"] = {
            "sample_rate": sample_rate,
            "channels": channels,
            "bit_depth": bit_depth,
            "total_samples": total_samples,
            "duration_sec": duration_sec
        }
        
        return result
