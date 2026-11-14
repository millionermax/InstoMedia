"""
login_flow.py - Backwards-compatible shim forwarding to automation.instagram.login_flow.
"""
import sys
from automation.instagram import login_flow
from automation.instagram.login_flow import *

# Point sys.modules["login_flow"] to target module
sys.modules[__name__] = login_flow
