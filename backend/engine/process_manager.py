"""Process manager — launch, monitor, and shutdown external subprocesses.

Supports auto-start on server boot, health checking (HTTP + TCP), graceful
shutdown via CTRL_BREAK_EVENT, and force-kill fallback on Windows.
"""

import atexit
import logging
import os
import shlex
import signal
import socket
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from urllib.parse import urlparse

import httpx

import config

logger = logging.getLogger(__name__)

# ── Health check strategies ──

def _check_http(url, timeout=3):
    """Return True if the HTTP endpoint responds (any status code)."""
    try:
        r = httpx.get(url, timeout=timeout)
        return True
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
        return False


def _check_tcp(host, port, timeout=3):
    """Return True if a TCP connection succeeds on the given port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


# ── Managed process definition ──

class ManagedProcess:
    """Describes a single external process that the manager can start/stop."""

    def __init__(self, name, display_name, exe_path, args, work_dir,
                 health_url=None, health_port=None, auto_start=False):
        self.name = name
        self.display_name = display_name
        self.exe_path = exe_path
        self.args = list(args)
        self.work_dir = work_dir
        self.health_url = health_url
        self.health_port = health_port
        self.auto_start = auto_start

        # Runtime state
        self.process = None
        self.state = "stopped"          # stopped | starting | running | failed
        self.pid = None
        self.started_at = None
        self.log_buffer = deque(maxlen=200)
        self.log_file = None
        self.log_path = None
        self._lock = threading.Lock()

    @property
    def is_running(self):
        return self.state == "running"

    @property
    def uptime(self):
        if self.started_at and self.is_running:
            return time.time() - self.started_at
        return 0.0

    def status_dict(self):
        return {
            "name": self.name,
            "display_name": self.display_name,
            "state": self.state,
            "pid": self.pid,
            "uptime": round(self.uptime, 1),
            "auto_start": self.auto_start,
            "exe": self.exe_path,
        }

    def _log(self, message):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        logger.debug("pm[%s] %s", self.name, line)
        self.log_buffer.append(line)
        if self.log_file and not self.log_file.closed:
            try:
                self.log_file.write(line + "\n")
                self.log_file.flush()
            except OSError:
                pass


# ── Process Manager ──

class ProcessManager:
    """Singleton that manages the lifecycle of external subprocesses."""

    def __init__(self):
        self._processes = {}
        self._lock = threading.Lock()
        self._monitor_thread = None
        self._stop_monitor = threading.Event()
        self._started = False

        # Ensure log dir exists
        os.makedirs(config.PROC_MGR_LOG_DIR, exist_ok=True)

        # Register default processes from config
        self._register_defaults()

        # Register cleanup on normal interpreter exit
        atexit.register(self.stop_all)

    # ── Registration ──

    def register(self, proc):
        """Register a ManagedProcess instance."""
        with self._lock:
            self._processes[proc.name] = proc

    def get(self, name):
        return self._processes.get(name)

    def list_all(self):
        return list(self._processes.keys())

    def _register_defaults(self):
        """Populate managed processes from config.py."""

        def _exe_path(exe, work_dir):
            """Resolve exe path (relative or absolute) against work dir."""
            if not exe:
                return ""
            if os.path.isabs(exe):
                return exe
            return os.path.join(work_dir, exe) if work_dir else exe

        chat_exe = _exe_path(config.LLAMA_CHAT_EXE, config.LLAMA_CHAT_WORK_DIR)
        if config.LLAMA_CHAT_EXE and os.path.isfile(chat_exe):
            self.register(ManagedProcess(
                name="llama_chat",
                display_name="llama.cpp (chat)",
                exe_path=chat_exe,
                args=config.LLAMA_CHAT_ARGS,
                work_dir=config.LLAMA_CHAT_WORK_DIR,
                health_url=config.LLAMA_CHAT_HEALTH_URL,
                auto_start=config.LLAMA_CHAT_AUTO_START,
            ))
        else:
            logger.info("pm: llama_chat not configured (%s not found)", chat_exe)

        embed_exe = _exe_path(config.LLAMA_EMBED_EXE, config.LLAMA_EMBED_WORK_DIR)
        if config.LLAMA_EMBED_EXE and os.path.isfile(embed_exe):
            self.register(ManagedProcess(
                name="llama_embed",
                display_name="llama.cpp (embeddings)",
                exe_path=embed_exe,
                args=config.LLAMA_EMBED_ARGS,
                work_dir=config.LLAMA_EMBED_WORK_DIR,
                health_url=config.LLAMA_EMBED_HEALTH_URL,
                auto_start=config.LLAMA_EMBED_AUTO_START,
            ))
        else:
            logger.info("pm: llama_embed not configured (%s not found)", embed_exe)

        if config.ACESTEP_EXE and os.path.isfile(config.ACESTEP_EXE):
            self.register(ManagedProcess(
                name="acestep",
                display_name="acestep.cpp",
                exe_path=config.ACESTEP_EXE,
                args=config.ACESTEP_ARGS,
                work_dir=config.ACESTEP_WORK_DIR,
                health_port=config.ACESTEP_PORT,
                auto_start=config.ACESTEP_AUTO_START,
            ))
        else:
            logger.info("pm: acestep not configured (ACESTEP_EXE not found)")

    # ── Startup ──

    def start_all_auto(self):
        """Start every process whose ``auto_start`` flag is True.

        Skips processes that are already healthy (port in use or HTTP
        endpoint responding), so the user can pre-start servers manually.
        Returns ``{name: bool}`` indicating which ones were launched.
        """
        results = {}
        for name in self.list_all():
            proc = self.get(name)
            if proc and proc.auto_start:
                results[name] = self.start(name)
        return results

    def start(self, name):
        """Start a single process by name.

        Returns True if the process was launched (or was already running),
        False on failure.
        """
        proc = self.get(name)
        if not proc:
            logger.warning("pm: unknown process '%s'", name)
            return False

        with proc._lock:
            if proc.is_running:
                logger.debug("pm: %s already running", name)
                return True

            if proc.state == "starting":
                logger.debug("pm: %s already starting", name)
                return False

            # Check if something is already listening on this endpoint
            if self._is_healthy(proc):
                logger.info("pm: %s already healthy (external instance), marking running", name)
                proc.state = "running"
                proc.pid = None
                proc.started_at = time.time()
                self._maybe_start_monitor()
                return True

            proc.state = "starting"
            proc.process = None
            proc.pid = None

        # Launch
        return self._launch(proc)

    def _launch(self, proc):
        """Spawn the subprocess and wait for it to become healthy."""
        try:
            log_path = os.path.join(
                config.PROC_MGR_LOG_DIR,
                f"pm_{proc.name}.log"
            )
            proc.log_path = log_path
            log_file = open(log_path, "a", encoding="utf-8")
            proc.log_file = log_file

            header = f"{'='*60}\nStarted at {datetime.now().isoformat()}\n{'='*60}"
            log_file.write(header + "\n")
            log_file.flush()

            # Build Popen arguments
            full_args = [proc.exe_path] + proc.args
            proc._log(f"Launching: {' '.join(shlex.quote(a) for a in full_args)}")
            proc._log(f"  Work dir: {proc.work_dir}")

            is_batch = proc.exe_path.lower().endswith(".cmd") or proc.exe_path.lower().endswith(".bat")

            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if not is_batch else 0

            proc.process = subprocess.Popen(
                full_args if not is_batch else [proc.exe_path] + proc.args,
                cwd=proc.work_dir if proc.work_dir else None,
                creationflags=creation_flags,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if not is_batch else subprocess.PIPE,
                shell=is_batch,
            )
            proc.pid = proc.process.pid
            proc.started_at = time.time()
            proc._log(f"Spawned PID {proc.pid}")

            # Start stdout reader thread
            self._start_reader_thread(proc)

            # Wait for health check
            ready = self._wait_healthy(proc, timeout=30)
            if ready:
                with proc._lock:
                    proc.state = "running"
                proc._log("Health check passed — process is ready")
                self._maybe_start_monitor()
            else:
                with proc._lock:
                    proc.state = "failed"
                proc._log("Health check timed out — process may not be ready")
                return False

            return True

        except FileNotFoundError:
            logger.error("pm: %s — executable not found: %s", proc.name, proc.exe_path)
            proc._log(f"ERROR: executable not found: {proc.exe_path}")
            with proc._lock:
                proc.state = "failed"
            return False
        except OSError as e:
            # Common subprocess errors: permission denied, address in use, etc.
            port = self._extract_port(proc)
            port_info = f" on port {port}" if port else ""
            detail = f"{e} (check if the port{port_info} is already in use by another process)"
            logger.error("pm: %s — OS error: %s", proc.name, detail)
            proc._log(f"ERROR: OS error{port_info} — {e}")
            with proc._lock:
                proc.state = "failed"
            return False
        except Exception as e:
            logger.error("pm: %s — unexpected launch error: %s", proc.name, e)
            proc._log(f"ERROR: unexpected error — {e}")
            # Try to get process output if it exited quickly
            if proc.process:
                try:
                    rc = proc.process.poll()
                    if rc is not None:
                        out = proc.process.stdout.read()
                        stderr_text = out.decode("utf-8", errors="replace")[:2000]
                        proc._log(f"  Process exited with rc={rc}: {stderr_text}")
                except Exception:
                    pass
            with proc._lock:
                proc.state = "failed"
            return False

    def _start_reader_thread(self, proc):
        """Read stdout/stderr in a background thread and write to log."""
        def _reader(p):
            try:
                for line in iter(p.process.stdout.readline, b""):
                    text = line.decode("utf-8", errors="replace").rstrip()
                    p._log(text)
            except (OSError, ValueError):
                pass

        t = threading.Thread(target=_reader, args=(proc,), daemon=True,
                             name=f"pm-reader-{proc.name}")
        t.start()

    # ── Health checks ──

    @staticmethod
    def _extract_port(proc):
        """Extract TCP port from health_url or health_port."""
        if proc.health_port:
            return proc.health_port
        if proc.health_url:
            parsed = urlparse(proc.health_url)
            if parsed.port:
                return parsed.port
        return None

    def _is_healthy(self, proc):
        """Check if the process endpoint is already serving.

        Tries HTTP first (any response = running), then falls back to a TCP
        port check to detect zombie processes holding the port.
        """
        if proc.health_url:
            if _check_http(proc.health_url, timeout=2):
                return True
        port = self._extract_port(proc)
        if port:
            return _check_tcp("127.0.0.1", port, timeout=2)
        return False

    def _wait_healthy(self, proc, timeout=30):
        """Poll health check up to `timeout` seconds."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._is_healthy(proc):
                return True
            time.sleep(1)
        return False

    def _maybe_start_monitor(self):
        """Start the background health monitor if not already running."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True,
            name="pm-monitor"
        )
        self._monitor_thread.start()

    def _monitor_loop(self):
        """Periodically check that managed processes are still alive."""
        while not self._stop_monitor.is_set():
            for name in self.list_all():
                proc = self.get(name)
                if not proc or proc.state != "running":
                    continue

                # Check if subprocess died
                if proc.process and proc.process.poll() is not None:
                    rc = proc.process.poll()
                    proc._log(f"Process exited unexpectedly (rc={rc})")
                    with proc._lock:
                        proc.state = "failed"
                        proc.process = None
                        if proc.log_file and not proc.log_file.closed:
                            proc.log_file.close()

                # Check health endpoint
                elif not self._is_healthy(proc):
                    proc._log("Health check failed — endpoint not responding")
                    with proc._lock:
                        proc.state = "failed"

            self._stop_monitor.wait(15)

    # ── Shutdown ──

    def stop(self, name):
        """Gracefully stop a single process.

        Sends CTRL_BREAK_EVENT (Windows), waits up to 5s, then force-kills
        with taskkill /F /T.
        """
        proc = self.get(name)
        if not proc:
            return False

        with proc._lock:
            if proc.state in ("stopped", "failed"):
                return True
            proc.state = "stopping"

        proc._log("Shutting down...")

        # Graceful: CTRL_BREAK_EVENT
        if proc.process and proc.process.poll() is None:
            try:
                proc._log("Sending CTRL_BREAK_EVENT")
                proc.process.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, AttributeError, ValueError) as e:
                proc._log(f"CTRL_BREAK failed: {e}")

            # Wait up to 5s
            for _ in range(10):
                if proc.process.poll() is not None:
                    break
                time.sleep(0.5)

        # Force kill if still alive
        if proc.process and proc.process.poll() is None:
            pid = proc.process.pid
            proc._log(f"Force killing PID {pid} (taskkill /F /T)")
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True, timeout=5
                )
            except subprocess.TimeoutExpired:
                proc._log("taskkill timed out")
            except Exception as e:
                proc._log(f"Force kill failed: {e}")

        # Final poll
        if proc.process:
            try:
                proc.process.poll()
            except Exception:
                pass

        with proc._lock:
            proc.state = "stopped"
            proc.process = None
            proc.pid = None
            proc.started_at = None
            if proc.log_file and not proc.log_file.closed:
                proc.log_file.close()
                proc.log_file = None

        proc._log("Stopped")
        return True

    def stop_all(self):
        """Stop every managed process (reverse order for dependencies)."""
        names = list(self.list_all())
        for name in reversed(names):
            try:
                self.stop(name)
            except Exception as e:
                logger.error("pm: error stopping %s: %s", name, e)

        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    # ── Status ──

    def get_status(self, name):
        proc = self.get(name)
        if not proc:
            return {"name": name, "state": "unknown"}
        return proc.status_dict()

    def get_all_statuses(self):
        return [self.get_status(name) for name in self.list_all()]

    def get_logs(self, name, tail=100):
        """Return the last `tail` log lines for a process."""
        proc = self.get(name)
        if not proc:
            return []
        lines = list(proc.log_buffer)
        return lines[-tail:]


# ── Singleton ──

_manager = None
_lock = threading.Lock()


def get_process_manager():
    global _manager
    if _manager is None:
        with _lock:
            if _manager is None:
                _manager = ProcessManager()
    return _manager
