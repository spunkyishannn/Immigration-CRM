import os
import socket
import subprocess
import shutil
import threading
import time
import urllib.error
import urllib.request
import atexit

import uvicorn

from app.main import app


HOST = "127.0.0.1"
PORT = 8765
APP_URL = f"http://{HOST}:{PORT}"
APP_DATA_DIR = os.path.join(os.getenv("LOCALAPPDATA", os.path.expanduser("~")), "ImmigrationCRM")
BROWSER_PROFILE_DIR = os.path.join(APP_DATA_DIR, "browser-profile")


def _hidden_subprocess_kwargs():
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _wait_for_server(timeout_seconds: float = 20.0) -> bool:
    started = time.time()
    while time.time() - started <= timeout_seconds:
        try:
            with urllib.request.urlopen(f"{APP_URL}/api/health", timeout=0.8) as res:
                if res.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError):
            time.sleep(0.2)
    return False


def _get_pid_on_port(port: int) -> int | None:
    # Windows-friendly netstat parsing for LISTENING pid.
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            stderr=subprocess.DEVNULL,
            **_hidden_subprocess_kwargs(),
        )
    except Exception:
        return None
    needle = f":{port}"
    for line in out.splitlines():
        line = line.strip()
        if not line or "LISTENING" not in line or needle not in line:
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local_addr = parts[1]
        state = parts[3]
        pid = parts[4]
        if local_addr.endswith(needle) and state.upper() == "LISTENING":
            try:
                return int(pid)
            except ValueError:
                return None
    return None


def _kill_pid(pid: int):
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_hidden_subprocess_kwargs(),
        )
    except Exception:
        pass


def _pid_process_name(pid: int) -> str:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            text=True,
            stderr=subprocess.DEVNULL,
            **_hidden_subprocess_kwargs(),
        ).strip()
    except Exception:
        return ""
    if not out or "No tasks are running" in out:
        return ""
    # CSV: "Image Name","PID","Session Name","Session#","Mem Usage"
    parts = [p.strip().strip('"') for p in out.split(",")]
    return parts[0].lower() if parts else ""


def _has_browser_session(browser_exe_name: str, profile_dir: str) -> bool:
    # Detect detached browser instance started with our profile dir.
    try:
        out = subprocess.check_output(
            ["wmic", "process", "where", f"name='{browser_exe_name}'", "get", "CommandLine", "/FORMAT:LIST"],
            text=True,
            stderr=subprocess.DEVNULL,
            **_hidden_subprocess_kwargs(),
        )
    except Exception:
        out = ""
    needle = f"--user-data-dir={profile_dir}".lower()
    if needle in out.lower():
        return True
    # WMIC may be unavailable on some systems; fallback to PowerShell CIM query.
    try:
        ps = (
            "$n='" + browser_exe_name + "'; "
            "$p='" + profile_dir.replace("'", "''") + "'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.Name -eq $n -and $_.CommandLine -like ('*--user-data-dir=' + $p + '*') } | "
            "Select-Object -First 1 -ExpandProperty ProcessId"
        )
        out2 = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            stderr=subprocess.DEVNULL,
            **_hidden_subprocess_kwargs(),
        ).strip()
        return bool(out2)
    except Exception:
        return False


def _pick_app_browser() -> str | None:
    candidates = [
        shutil.which("msedge"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        shutil.which("chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for c in candidates:
        if not c:
            continue
        if ("\\" in c and os.path.exists(c)) or shutil.which(c):
            return c
    return None


def _trigger_backup_on_close():
    try:
        req = urllib.request.Request(
            f"{APP_URL}/api/backup/create",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        # Backup failure should not block app shutdown.
        pass


class ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.server: uvicorn.Server | None = None
        self.error: Exception | None = None

    def run(self):
        try:
            config = uvicorn.Config(
                app=app,
                host=HOST,
                port=PORT,
                log_level="warning",
                log_config=None,
                access_log=False,
            )
            self.server = uvicorn.Server(config)
            # Uvicorn signal handlers can fail outside main thread in packaged apps.
            self.server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
            self.server.run()
        except Exception as exc:
            self.error = exc

    def stop(self):
        if self.server is not None:
            self.server.should_exit = True


def main():
    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)

    # Always try to reclaim app port from stale app-owned processes.
    if _is_port_open(HOST, PORT):
        stale_pid = _get_pid_on_port(PORT)
        if stale_pid:
            pname = _pid_process_name(stale_pid)
            if pname in {"immigrationcrm.exe", "python.exe", "pythonw.exe"}:
                _kill_pid(stale_pid)
                time.sleep(0.8)
        if _is_port_open(HOST, PORT):
            raise RuntimeError(f"Port {PORT} is already in use by another process.")

    server = ServerThread()
    server.start()
    if not _wait_for_server(timeout_seconds=60):
        if server.error is not None:
            raise RuntimeError(f"Desktop server failed: {server.error}") from server.error
        raise RuntimeError("Desktop server did not start in time.")

    browser_path = _pick_app_browser()
    if not browser_path:
        raise RuntimeError("No supported browser runtime found (Edge/Chrome).")

    proc = None
    def _cleanup():
        if server is not None:
            server.stop()
            server.join(timeout=5)

    atexit.register(_cleanup)
    try:
        # App mode = no tabs/address bar; native-like standalone window.
        browser_exe_name = os.path.basename(browser_path)
        proc = subprocess.Popen(
            [
                browser_path,
                f"--app={APP_URL}",
                "--new-window",
                f"--user-data-dir={BROWSER_PROFILE_DIR}",
                "--no-first-run",
                "--disable-session-crashed-bubble",
                "--start-maximized",
            ]
        )

        if proc is not None:
            # Some browser launches detach quickly and hand off to another process.
            # Keep desktop runtime alive while an app-mode session with our profile exists.
            grace_deadline = time.time() + 15
            while True:
                if proc.poll() is None:
                    time.sleep(0.8)
                    continue
                if _has_browser_session(browser_exe_name, BROWSER_PROFILE_DIR):
                    time.sleep(1.0)
                    continue
                # Grace period for delayed process handoff on slower machines.
                if time.time() < grace_deadline:
                    time.sleep(1.0)
                    continue
                break
    finally:
        _trigger_backup_on_close()
        _cleanup()
        # Final safety: ensure the app port is released for next launch.
        stale_pid = _get_pid_on_port(PORT)
        if stale_pid:
            _kill_pid(stale_pid)


if __name__ == "__main__":
    main()
