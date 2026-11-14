"""
profile_capture.py - Backwards-compatible shim forwarding to automation.android.profile_capture.
"""
import sys
from automation.android import profile_capture
from automation.android.profile_capture import *

# Point sys.modules["profile_capture"] to target module
sys.modules[__name__] = profile_capture
