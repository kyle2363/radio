"""Windows cloudflared child owner. Closing this process closes its kill-on-close Job Object."""
import ctypes
import os
import subprocess
import sys
import threading
import _winapi
import msvcrt


def windows_job_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel.CreateJobObjectW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    kernel.SetInformationJobObject.restype = ctypes.c_int
    kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel.AssignProcessToJobObject.restype = ctypes.c_int
    kernel.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel.ResumeThread.restype = ctypes.c_uint32
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype = ctypes.c_int
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateJobObject.restype = ctypes.c_int
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateProcess.restype = ctypes.c_int
    return kernel


def pump(pipe_handle):
    try:
        fd = msvcrt.open_osfhandle(int(pipe_handle), os.O_RDONLY | os.O_BINARY)
        with os.fdopen(fd, "rb", buffering=0) as stream:
            for raw in iter(stream.readline, b""):
                sys.stdout.write(raw.decode("utf-8", errors="replace"))
                sys.stdout.flush()
    except (OSError, ValueError):
        pass


def run(parent_pid, command):
    if os.name != "nt":
        raise RuntimeError("The tunnel job helper is Windows-only")
    kernel = windows_job_api()
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = bytearray(144)  # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, Windows x64.
    ctypes.cast((ctypes.c_ubyte * len(limits)).from_buffer(limits), ctypes.POINTER(ctypes.c_uint32))[4] = 0x00002000
    if not kernel.SetInformationJobObject(job, 9, (ctypes.c_ubyte * len(limits)).from_buffer(limits), len(limits)):
        raise ctypes.WinError(ctypes.get_last_error())
    parent = kernel.OpenProcess(0x00100000, False, parent_pid)  # SYNCHRONIZE
    if not parent:
        raise ctypes.WinError(ctypes.get_last_error())
    read_pipe = write_pipe = process = thread = null_input = None
    try:
        read_pipe, write_pipe = _winapi.CreatePipe(None, 0)
        os.set_handle_inheritable(read_pipe, False)
        os.set_handle_inheritable(write_pipe, True)
        null_input = kernel.CreateFileW('NUL', 0x80000000, 0x00000003, None, 3, 0x00000080, None)
        if not null_input or int(null_input) == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        os.set_handle_inheritable(int(null_input), True)
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= _winapi.STARTF_USESTDHANDLES
        startup.hStdInput = int(null_input)
        startup.hStdOutput = write_pipe
        startup.hStdError = write_pipe
        app_name = os.path.abspath(command[0])
        command_line = subprocess.list2cmdline(command)
        process, thread, _pid, _tid = _winapi.CreateProcess(
            app_name, command_line, None, None, True,
            0x00000004 | _winapi.CREATE_NO_WINDOW, None, None, startup,
        )
        kernel.CloseHandle(null_input)
        null_input = None
        _winapi.CloseHandle(write_pipe)
        write_pipe = None
        if not kernel.AssignProcessToJobObject(job, int(process)):
            kernel.TerminateProcess(process, 1)
            raise ctypes.WinError(ctypes.get_last_error())
        if kernel.ResumeThread(int(thread)) == 0xFFFFFFFF:
            kernel.TerminateProcess(process, 1)
            raise ctypes.WinError(ctypes.get_last_error())
        _winapi.CloseHandle(thread)
        thread = None
        threading.Thread(target=pump, args=(read_pipe,), daemon=True).start()
        read_pipe = None  # The pump thread owns the raw handle now.
        print("RADIO_JOB_READY", flush=True)
        while True:
            if kernel.WaitForSingleObject(parent, 100) != 0x00000102:
                return 0  # Electron exited; closing job below kills cloudflared and descendants.
            if kernel.WaitForSingleObject(int(process), 0) == 0:
                exit_code = ctypes.c_uint32()
                if kernel.GetExitCodeProcess(int(process), ctypes.byref(exit_code)):
                    return exit_code.value
                return 1
    finally:
        if process and kernel.WaitForSingleObject(int(process), 0) == 0x00000102:
            kernel.TerminateJobObject(job, 1)
        if thread:
            _winapi.CloseHandle(thread)
        if process:
            _winapi.CloseHandle(process)
        if read_pipe:
            _winapi.CloseHandle(read_pipe)
        if write_pipe:
            _winapi.CloseHandle(write_pipe)
        if null_input:
            kernel.CloseHandle(null_input)
        kernel.CloseHandle(parent)
        kernel.CloseHandle(job)  # KILL_ON_JOB_CLOSE is the crash/quit safety net.


def main(argv):
    if len(argv) < 4 or argv[0] != "--parent-pid" or argv[2] != "--":
        raise ValueError("Invalid tunnel helper arguments")
    parent_pid = int(argv[1])
    if parent_pid <= 0:
        raise ValueError("Invalid parent process")
    return run(parent_pid, argv[3:])


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"RADIO_JOB_ERROR {type(exc).__name__}", flush=True)
        raise SystemExit(1)
