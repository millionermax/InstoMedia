"""
vault.py - Backwards-compatible shim forwarding to backend.storage.vault.
"""
import sys
from backend.storage import vault
from backend.storage.vault import *

# Point sys.modules["vault"] to backend.storage.vault so that attribute accesses and patches
# directly target backend.storage.vault.
sys.modules[__name__] = vault
