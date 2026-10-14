"""Encrypt API keys at rest with Windows DPAPI (bound to the current user)."""
import base64
import ctypes
import ctypes.wintypes as wt
import logging

log = logging.getLogger(__name__)

_PREFIX_DPAPI = "dpapi:"
_PREFIX_B64 = "b64:"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _crypt(func, data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32")
    kernel32 = ctypes.WinDLL("kernel32")
    blob_in, _keep = _blob(data)
    blob_out = _Blob()
    ok = getattr(crypt32, func)(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"{func} failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def encrypt(secret: str) -> str:
    if not secret:
        return ""
    try:
        return _PREFIX_DPAPI + base64.b64encode(_crypt("CryptProtectData", secret.encode("utf-8"))).decode()
    except (OSError, AttributeError) as e:
        log.warning("DPAPI unavailable, storing key obfuscated only: %s", e)
        return _PREFIX_B64 + base64.b64encode(secret.encode("utf-8")).decode()


def decrypt(stored: str) -> str:
    if not stored:
        return ""
    try:
        if stored.startswith(_PREFIX_DPAPI):
            return _crypt("CryptUnprotectData", base64.b64decode(stored[len(_PREFIX_DPAPI):])).decode("utf-8")
        if stored.startswith(_PREFIX_B64):
            return base64.b64decode(stored[len(_PREFIX_B64):]).decode("utf-8")
    except (OSError, ValueError, UnicodeDecodeError) as e:
        log.error("Could not decrypt stored API key: %s", e)
        return ""
    return stored


def decode_legacy(stored: str) -> str:
    """v1 stored keys as bare base64 (and sometimes as plain text)."""
    if not stored:
        return ""
    try:
        decoded = base64.b64decode(stored, validate=True).decode("utf-8")
        if decoded.isprintable():
            return decoded
    except (ValueError, UnicodeDecodeError):
        pass
    return stored
