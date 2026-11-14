"""
excel_manager.py - Backwards-compatible shim forwarding to backend.services.excel_manager.
"""
import sys
from backend.services import excel_manager
from backend.services.excel_manager import *

# Point sys.modules["excel_manager"] to backend.services.excel_manager so that patches
# and attribute accesses directly target backend.services.excel_manager.
sys.modules[__name__] = excel_manager
