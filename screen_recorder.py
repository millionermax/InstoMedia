"""
screen_recorder.py - Backwards-compatible shim forwarding to automation.observability.screen_recorder.
"""
import sys
from automation.observability import screen_recorder
from automation.observability.screen_recorder import *

# Point sys.modules["screen_recorder"] to target module
sys.modules[__name__] = screen_recorder
