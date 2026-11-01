"""Explicit read-only content hashing; cache lookups never open source audio."""
import hashlib
import os
import re
from pathlib import Path
import sqlite3
import threading
import time


def _change_time(path, stat):
    if os.name != "nt":
        return stat.st_ctime_ns
    # Python exposes Windows creation time as st_ctime. Query NTFS change
    # time instead so restoring mtime after an edit cannot reuse the old hash.
    import ctypes
    from ctypes import wintypes
    class BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_longlong) for name in
                    ("CreationTime", "LastAccessTime", "LastWriteTime", "ChangeTime")] + [("FileAttributes", wintypes.DWORD)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path.resolve()), 0x80, 7, None, 3, 0, None)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError("Source attributes unavailable")
    try:
        info = BasicInfo()
        if not kernel.GetFileInformationByHandleEx(handle, 0, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("Source change time unavailable")
        if info.ChangeTime <= 0:
            raise OSError("Source change time unavailable")
        return info.ChangeTime
    finally:
        kernel.CloseHandle(handle)


class ContentIdentityCache:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _fingerprint(path):
        stat = path.stat()
        if not path.is_file() or stat.st_mtime_ns <= 0:
            raise OSError('Unstable source metadata')
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, str(stat.st_dev), str(stat.st_ino), _change_time(path, stat))

    def cached(self, path):
        path = Path(path)
        try:
            fingerprint = self._fingerprint(path)
            with self._lock:
                if not self.path.is_file():
                    return None
                with sqlite3.connect(self.path) as conn:
                    row = conn.execute('SELECT size,mtime,device,inode,ctime,digest FROM identities_v2 WHERE path=?', (fingerprint[0],)).fetchone()
            return row[5] if row and tuple(row[:5]) == fingerprint[1:] and isinstance(row[5], str) and re.fullmatch(r"[a-f0-9]{64}", row[5]) and self._fingerprint(path) == fingerprint else None
        except (OSError, sqlite3.Error):
            return None

    @staticmethod
    def _digest(path, cancelled):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            while True:
                if cancelled():
                    return None
                block = stream.read(256 * 1024)
                if not block:
                    break
                digest.update(block)
                time.sleep(0.001)  # Yield between bounded reads to playback/storage.
            after = os.fstat(stream.fileno())
        attrs = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        return digest.hexdigest() if all(getattr(before, a) == getattr(after, a) for a in attrs) else None

    def measure(self, path, *, cancelled=lambda: False):
        path = Path(path)
        # One cache writer serializes fingerprint checks and prevents an older
        # measurement overwriting a newer result in this process.
        with self._lock:
            if cancelled():
                return None
            existing = self.cached(path)
            if existing:
                return existing
            try:
                before = self._fingerprint(path)
                digest = self._digest(path, cancelled)
                if not digest or cancelled() or self._fingerprint(path) != before:
                    return None
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(self.path) as conn:
                    conn.execute('CREATE TABLE IF NOT EXISTS identities_v2 (path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, device TEXT, inode TEXT, ctime INTEGER, digest TEXT)')
                    # Recheck immediately before publishing; unchanged entries
                    # remain resumable if a subsequent file fails or cancels.
                    if self._fingerprint(path) != before:
                        return None
                    conn.execute('INSERT OR REPLACE INTO identities_v2 VALUES (?,?,?,?,?,?,?)', (*before, digest))
                return digest
            except OSError:
                return None

