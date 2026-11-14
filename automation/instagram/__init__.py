"""Automation workflows specific to Instagram."""
from automation.instagram import reel_workflow, login_flow
from automation.instagram.reel_workflow import InstagramReelUploader, DEFAULT_AUDIO_URL, DEFAULT_CAPTION, PACKAGE_NAME
from automation.instagram.login_flow import InstagramAccountLoginService, BatchLoginManager, LoginResult

__all__ = [
    "reel_workflow",
    "login_flow",
    "InstagramReelUploader",
    "InstagramAccountLoginService",
    "BatchLoginManager",
    "LoginResult",
    "DEFAULT_AUDIO_URL",
    "DEFAULT_CAPTION",
    "PACKAGE_NAME",
]
