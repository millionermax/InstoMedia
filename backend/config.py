"""
backend/config.py - Core configuration and constants for Instoper / Reel Studio.
"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

ALLOWED_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}
CONTENT_VIDEO_EXTENSIONS = {"mp4", "mov", "mkv", "avi"}
CONTENT_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
CONTENT_ALLOWED_EXTENSIONS = CONTENT_VIDEO_EXTENSIONS | CONTENT_IMAGE_EXTENSIONS

CONTENT_MIME_MAP = {
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "mkv": "video/x-matroska",
    "avi": "video/x-msvideo",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}

BUILTIN_AUTOMATION_ID = "reel_upload_with_music"
AUTOMATION_CATALOG = [
    {
        "id": BUILTIN_AUTOMATION_ID,
        "name": "Reel Upload with Music",
        "status": "ready",
        "description": "Runs the 11-step Instagram Reel workflow on one onboarded account using selected audio, video, caption, and privacy settings.",
        "steps": 11,
    }
]
