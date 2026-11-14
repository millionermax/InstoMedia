"""
db.py - Backwards-compatible shim forwarding to backend.storage.db.
"""
import sys
from backend.storage import db
from backend.storage.db import *

# Point sys.modules["db"] to backend.storage.db so that patches (e.g. patch("db.DEFAULT_DB_PATH"))
# and attribute accesses directly target backend.storage.db.
sys.modules[__name__] = db
