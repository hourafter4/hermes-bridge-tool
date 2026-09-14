"""Optional macOS Keychain storage without passing secrets to subprocesses."""

import ctypes
import os
from pathlib import Path
import sys
import tempfile


SERVICE = "hermes-bridge-tool"


class KeychainUnavailable(ValueError):
    """Keychain could not satisfy the request; never silently fall back to a file."""


class _Keychain:
    def __init__(self):
        if sys.platform != "darwin":
            raise KeychainUnavailable("Keychain storage requires macOS. Select file storage explicitly on this platform.")
        try:
            self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
            self.sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
            pointer = ctypes.c_void_p
            self.cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
            self.cf.CFStringCreateWithCString.restype = pointer
            self.cf.CFDataCreate.argtypes = [pointer, pointer, ctypes.c_long]
            self.cf.CFDataCreate.restype = pointer
            self.cf.CFDictionaryCreateMutable.argtypes = [pointer, ctypes.c_long, pointer, pointer]
            self.cf.CFDictionaryCreateMutable.restype = pointer
            self.cf.CFDictionarySetValue.argtypes = [pointer, pointer, pointer]
            self.cf.CFDictionarySetValue.restype = None
            self.cf.CFDataGetLength.argtypes = [pointer]
            self.cf.CFDataGetLength.restype = ctypes.c_long
            self.cf.CFDataGetBytePtr.argtypes = [pointer]
            self.cf.CFDataGetBytePtr.restype = pointer
            self.cf.CFRelease.argtypes = [pointer]
            self.cf.CFRelease.restype = None
            self.sec.SecItemCopyMatching.argtypes = [pointer, ctypes.POINTER(pointer)]
            self.sec.SecItemAdd.argtypes = [pointer, ctypes.POINTER(pointer)]
            self.sec.SecItemUpdate.argtypes = [pointer, pointer]
            self.sec.SecItemDelete.argtypes = [pointer]
        except (OSError, AttributeError):
            raise KeychainUnavailable("Cannot load macOS Keychain services.") from None

    def symbol(self, name):
        return ctypes.c_void_p.in_dll(self.sec if name.startswith("kSec") else self.cf, name).value

    def operate(self, action, account, secret=None):
        allocated = []

        def string(value):
            item = self.cf.CFStringCreateWithCString(None, value.encode(), 0x08000100)
            if not item:
                raise KeychainUnavailable("Cannot allocate a Keychain request.")
            allocated.append(item)
            return item

        def dictionary(pairs):
            # NULL callbacks: references remain alive in allocated until request ends.
            item = self.cf.CFDictionaryCreateMutable(None, 0, None, None)
            if not item:
                raise KeychainUnavailable("Cannot allocate a Keychain request.")
            allocated.append(item)
            for name, value in pairs:
                self.cf.CFDictionarySetValue(item, self.symbol(name), value)
            return item

        try:
            query = dictionary([
                ("kSecClass", self.symbol("kSecClassGenericPassword")),
                ("kSecAttrService", string(SERVICE)),
                ("kSecAttrAccount", string(account)),
            ])
            if action == "load":
                self.cf.CFDictionarySetValue(query, self.symbol("kSecReturnData"), self.symbol("kCFBooleanTrue"))
                result = ctypes.c_void_p()
                status = self.sec.SecItemCopyMatching(query, ctypes.byref(result))
                if status == 0 and result.value:
                    allocated.append(result.value)
                    try:
                        return ctypes.string_at(self.cf.CFDataGetBytePtr(result), self.cf.CFDataGetLength(result)).decode("utf-8")
                    except UnicodeError:
                        raise KeychainUnavailable("The Keychain item is not a UTF-8 credential.") from None
                if status == 0:
                    raise KeychainUnavailable("Keychain returned no credential data.")
            elif action == "delete":
                status = self.sec.SecItemDelete(query)
                if status in (0, -25300):
                    return None
            else:
                raw = secret.encode("utf-8")
                data = self.cf.CFDataCreate(None, ctypes.c_char_p(raw), len(raw))
                if not data:
                    raise KeychainUnavailable("Cannot allocate a Keychain request.")
                allocated.append(data)
                updates = dictionary([("kSecValueData", data)])
                status = self.sec.SecItemUpdate(query, updates)
                if status == -25300:
                    self.cf.CFDictionarySetValue(query, self.symbol("kSecValueData"), data)
                    status = self.sec.SecItemAdd(query, None)
            if status != 0:
                raise KeychainUnavailable(f"Keychain request failed (OSStatus {status}). Unlock or authorize Keychain access, or select file storage explicitly.")
        finally:
            for item in reversed(allocated):
                self.cf.CFRelease(item)


def _validate(account, backend, file_path):
    if not isinstance(account, str) or not account or len(account) > 512 or any(ord(c) < 32 for c in account):
        raise ValueError("Credential account must be a nonempty name without control characters.")
    if backend not in {"keychain", "file"}:
        raise ValueError("Credential storage must be keychain or file.")
    if backend == "file" and file_path is None:
        raise ValueError("File credential storage requires an explicit path.")


def store_secret(account: str, secret: str, *, backend: str = "keychain", file_path=None):
    _validate(account, backend, file_path)
    if not isinstance(secret, str) or not secret or "\x00" in secret:
        raise ValueError("Credential must be a nonempty string without NUL characters.")
    if backend == "keychain":
        return _Keychain().operate("store", account, secret)
    path = Path(file_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            os.chmod(temporary, 0o600)
            handle.write(secret)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def load_secret(account: str, *, backend: str = "keychain", file_path=None) -> str:
    _validate(account, backend, file_path)
    if backend == "keychain":
        return _Keychain().operate("load", account)
    try:
        return Path(file_path).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError("Cannot read the configured credential file.") from None


def delete_secret(account: str, *, backend: str = "keychain", file_path=None):
    _validate(account, backend, file_path)
    if backend == "keychain":
        return _Keychain().operate("delete", account)
    Path(file_path).expanduser().unlink(missing_ok=True)
