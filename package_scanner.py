"""
package_scanner.py - Backwards-compatible shim forwarding to automation.android.package_scanner.
"""
import sys
from automation.android import package_scanner
from automation.android.package_scanner import *

# Point sys.modules["package_scanner"] to target module
sys.modules[__name__] = package_scanner
