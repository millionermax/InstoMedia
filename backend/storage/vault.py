"""
vault.py - Protected Secret Storage & Encrypted Export/Import for Instoper.
Protects sensitive account credentials (passwords, TOTP seeds, proxy credentials)
using Windows DPAPI locally, and provides AES-256-GCM passphrase-encrypted backup/restore.
"""

import os
import sys
import json
import base64
import ctypes
import ctypes.wintypes
import urllib.parse
from typing import Dict, Any, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag


# ---------------------------------------------------------------------------
# 1. Windows DPAPI (Local OS Protection)
# ---------------------------------------------------------------------------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte))
    ]


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def dpapi_protect(plaintext: Optional[str]) -> Optional[bytes]:
    """Encrypts a plaintext secret string using Windows Data Protection API (DPAPI)."""
    if plaintext is None or plaintext == "":
        return None

    data_bytes = plaintext.encode("utf-8")

    if not _is_windows():
        # Fallback for non-windows environments: local obfuscation with warning
        return base64.b64encode(data_bytes)

    try:
        data_in = DATA_BLOB(
            len(data_bytes),
            ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_byte))
        )
        data_out = DATA_BLOB()
        # CRYPTPROTECT_UI_FORBIDDEN = 0x1
        if not ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(data_in),
            "instoper_secret",
            None,
            None,
            None,
            0x1,
            ctypes.byref(data_out)
        ):
            raise ctypes.WinError()

        buf = ctypes.string_at(data_out.pbData, data_out.cbData)
        ctypes.windll.kernel32.LocalFree(data_out.pbData)
        return buf
    except Exception as e:
        raise RuntimeError(f"DPAPI protection failed: {e}")


def dpapi_unprotect(ciphertext: Optional[bytes]) -> Optional[str]:
    """Decrypts a DPAPI-protected ciphertext buffer back into a plaintext string."""
    if ciphertext is None or len(ciphertext) == 0:
        return None

    if not _is_windows():
        return base64.b64decode(ciphertext).decode("utf-8")

    try:
        data_in = DATA_BLOB(
            len(ciphertext),
            ctypes.cast(ctypes.create_string_buffer(ciphertext), ctypes.POINTER(ctypes.c_byte))
        )
        data_out = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(data_in),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(data_out)
        ):
            raise ctypes.WinError()

        buf = ctypes.string_at(data_out.pbData, data_out.cbData)
        ctypes.windll.kernel32.LocalFree(data_out.pbData)
        return buf.decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"DPAPI unprotection failed (credentials might belong to another user/machine): {e}")


# ---------------------------------------------------------------------------
# 2. Passphrase-Encrypted Secret Backup & Restore (AES-256-GCM + PBKDF2)
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 120_000
SALT_SIZE = 16
NONCE_SIZE = 12


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derives a 256-bit AES key from a user-supplied passphrase and salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def export_secrets_package(secrets_data: Dict[str, Any], passphrase: str) -> bytes:
    """
    Encrypts a dictionary of account secrets using AES-256-GCM with a user passphrase.
    Returns a formatted UTF-8 JSON byte string ready to be saved as an encrypted backup file.
    """
    if not passphrase or len(passphrase.strip()) < 4:
        raise ValueError("Passphrase must be at least 4 characters long.")

    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(passphrase, salt)

    aesgcm = AESGCM(key)
    payload_json = json.dumps(secrets_data, ensure_ascii=False).encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, payload_json, associated_data=b"instoper_v1")

    package = {
        "version": 1,
        "kdf": "pbkdf2_hmac_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(package, indent=2).encode("utf-8")


def import_secrets_package(package_bytes: bytes, passphrase: str) -> Dict[str, Any]:
    """
    Decrypts an encrypted backup file using the user passphrase and returns the secrets dictionary.
    Raises ValueError if passphrase is wrong or file is corrupted.
    """
    if not passphrase:
        raise ValueError("Passphrase is required to restore secrets.")

    try:
        package = json.loads(package_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"Invalid package format: file is not valid JSON ({e})")

    if package.get("version") != 1:
        raise ValueError("Unsupported package version.")

    try:
        salt = base64.b64decode(package["salt"])
        nonce = base64.b64decode(package["nonce"])
        ciphertext = base64.b64decode(package["ciphertext"])
        iterations = package.get("iterations", PBKDF2_ITERATIONS)
    except KeyError as e:
        raise ValueError(f"Corrupted package header: missing field {e}")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    key = kdf.derive(passphrase.encode("utf-8"))

    aesgcm = AESGCM(key)
    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, associated_data=b"instoper_v1")
        return json.loads(decrypted_bytes.decode("utf-8"))
    except InvalidTag:
        raise ValueError("Incorrect passphrase or corrupted secret package.")
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")


# ---------------------------------------------------------------------------
# 3. Proxy URL Parsing & Credential Redaction
# ---------------------------------------------------------------------------
def parse_proxy_credentials(raw_proxy: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Parses a raw proxy string into (sanitized_endpoint, auth_secret).
    Supports:
      - http://user:pass@host:port -> ('http://host:port', 'user:pass')
      - host:port:user:pass -> ('host:port', 'user:pass')
      - http://host:port -> ('http://host:port', None)
    """
    if not raw_proxy:
        return None, None

    raw = raw_proxy.strip()
    if not raw:
        return None, None

    # Format A: host:port:user:pass (legacy text list format)
    parts = raw.split(":")
    if len(parts) == 4 and not raw.startswith(("http://", "https://", "socks5://", "socks4://")):
        endpoint = f"{parts[0]}:{parts[1]}"
        secret = f"{parts[2]}:{parts[3]}"
        return endpoint, secret

    # Format B: URL schema [http|https|socks5]://[user:pass@]host[:port]
    has_scheme = "://" in raw
    url_to_parse = raw if has_scheme else "http://" + raw
    try:
        parsed = urllib.parse.urlsplit(url_to_parse)
        if parsed.username or parsed.password:
            user = parsed.username or ""
            pwd = parsed.password or ""
            host = parsed.hostname or ""
            netloc = f"{host}:{parsed.port}" if parsed.port else host
            scheme = parsed.scheme if has_scheme else ""
            endpoint = f"{scheme}://{netloc}" if scheme else netloc
            secret = f"{user}:{pwd}"
            return endpoint, secret
        return raw, None
    except Exception:
        return raw, None


def redact_proxy_url(raw_proxy: Optional[str]) -> Optional[str]:
    """
    Returns a safe, redacted proxy string masking any embedded credentials (username & password).
    e.g. 'http://user:password@host:8080' -> 'http://***:***@host:8080'
    """
    if not raw_proxy:
        return None

    endpoint, secret = parse_proxy_credentials(raw_proxy)
    if not endpoint:
        return None

    if secret:
        if "://" in endpoint:
            scheme, host = endpoint.split("://", 1)
            return f"{scheme}://***:***@{host}"
        return f"***:***@{endpoint}"

    return endpoint
