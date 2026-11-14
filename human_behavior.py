"""
human_behavior.py - Backwards-compatible shim forwarding to automation.interaction.human_behavior.
"""
import sys
from automation.interaction import human_behavior
from automation.interaction.human_behavior import *

# Point sys.modules["human_behavior"] to target module
sys.modules[__name__] = human_behavior
