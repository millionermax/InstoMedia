"""
adb.py - Backwards-compatible shim forwarding to automation.instagram.reel_workflow.
"""
import sys
from automation.instagram import reel_workflow
from automation.instagram.reel_workflow import *

# Point sys.modules["adb"] to target module
sys.modules[__name__] = reel_workflow

if __name__ == "__main__":
    reel_workflow.main()
