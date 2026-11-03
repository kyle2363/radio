"""Persistent P-256 infrastructure identity, protected by Windows user DPAPI.

No keys or files are touched at import. Tests inject a protector and disposable path.
An unreadable identity is never silently replaced: repair requires trusted re-pairing.
"""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from uuid import UUID, uuid4


def b64(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class WindowsProtector:
    def _transform(self, value, decrypt):
        if os.name != "nt":
            raise RuntimeError("Windows protected storage unavailable")
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        source, target = Blob(len(value), buffer), Blob()
        function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise RuntimeError("Windows protected storage unavailable")
        try:
            return ctypes.string_at(target.data, target.length)
        finally:
            kernel.LocalFree(target.data)

    def protect(self, value):
        return self._transform(value, False)

    def unprotect(self, value):
        return self._transform(value, True)


class ServerIdentity:
    def __init__(self, path, *, protector=None):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        self.path, self.protector = Path(path), protector or WindowsProtector()
        self._lock = threading.Lock()
        try:
            if self.path.exists():
                if self.path.stat().st_size > 16384:
                    raise ValueError("Invalid protected identity")
                stored = json.loads(self.protector.unprotect(self.path.read_bytes()))
                self.server_id = str(UUID(stored["server_id"]))
                self.generation = stored["connection_generation"]
                if type(self.generation) is not int or self.generation < 0:
                    raise ValueError("Invalid identity generation")
                self._key = serialization.load_der_private_key(base64.b64decode(stored["private_key"], validate=True), None)
                if not isinstance(self._key, ec.EllipticCurvePrivateKey) or not isinstance(self._key.curve, ec.SECP256R1):
                    raise ValueError("Invalid identity key")
            else:
                self.server_id, self.generation = str(uuid4()), 0
                self._key = ec.generate_private_key(ec.SECP256R1())
                self._save()
            public = self._key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            self.public_key = b64(public)
            self.public_key_pin = hashlib.sha256(public).hexdigest()
        except Exception as exc:
            raise RuntimeError("Protected server identity unavailable; trusted repair required") from exc

    def _save(self):
        from cryptography.hazmat.primitives import serialization
        private = self._key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        value = json.dumps({"server_id": self.server_id, "connection_generation": self.generation,
                            "private_key": base64.b64encode(private).decode("ascii")}).encode("utf8")
        protected = self.protector.protect(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(protected)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def next_generation(self):
        with self._lock:
            self.generation += 1
            self._save()
            return self.generation

    def descriptor(self, endpoint, generation, revision):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        now = int(time.time())
        payload = json.dumps({"protocol_version": 1, "server_id": self.server_id, "endpoint": endpoint,
                              "connection_generation": generation, "publication_revision": revision,
                              "available": True, "issued_at": now, "expires_at": now + 300},
                             separators=(",", ":"), sort_keys=True).encode("utf8")
        signature = self._key.sign(payload, ec.ECDSA(hashes.SHA256()))
        return {"payload": b64(payload), "signature": b64(signature), "public_key": self.public_key}
