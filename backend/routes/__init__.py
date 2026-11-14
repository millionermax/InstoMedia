"""Backend routes package for web endpoints."""
from backend.routes.dashboard import dashboard_bp
from backend.routes.devices import devices_bp
from backend.routes.content import content_bp
from backend.routes.accounts import accounts_bp
from backend.routes.automations import automations_bp
from backend.routes.campaigns import campaigns_bp
from backend.routes.tasks import tasks_bp
from backend.routes.imports_exports import imports_exports_bp

__all__ = [
    "dashboard_bp",
    "devices_bp",
    "content_bp",
    "accounts_bp",
    "automations_bp",
    "campaigns_bp",
    "tasks_bp",
    "imports_exports_bp",
]
