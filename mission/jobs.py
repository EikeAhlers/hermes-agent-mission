"""Start, watch and stop real Hermes runs from the deck.

A job is a detached `hermes -z "<prompt>"` process. We keep the pid, the log and
the prompt in a small JSON registry so the UI can show live output, stop a run
and jump into the session it created.

Everything stays on this machine: no network, no credentials, no shell
interpolation (arguments are passed as a list, never through a shell).
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import store

_LOCK = threading.Lock()
_STILL_ACTIVE = 259
_QUERY_LIMITED = 0x1000
_SECRET = re.compile(r"(sk-[A-Za-z0-9]{12,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._-]{20,})")


def _dir() -> Path:
    path = store.home() / "mission-jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _registry() -> Path:
    return _dir() / "jobs.json"


def _read() -> list[dict]:
    try:
        return json.loads(_registry().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _write(jobs: list[dict]) -> None:
    _registry().write_text(json.dumps(jobs[-60:], indent=1), encoding="utf-8")


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(_QUERY_LIMITED, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def hermes_bin() -> str | None:
    for name in ("hermes.exe", "hermes.cmd", "hermes"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _command(binary: str, rest: list[str]) -> list[str]:
    # .cmd/.bat are shell scripts: they need cmd.exe, a detached process cannot run them directly
    if binary.lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", binary, *rest]
    return [binary, *rest]


def start(prompt: str, session_id: str | None = None, profile: str | None = None,
          cwd: str | None = None) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        return {"error": "empty prompt"}
    binary = hermes_bin()
    if not binary:
        return {"error": "hermes executable not found on PATH"}

    job_id = uuid.uuid4().hex[:12]
    log = _dir() / f"{job_id}.log"
    rest = ["-z", prompt, "--yolo"]
    if session_id:
        rest += ["--resume", session_id]
    if profile:
        rest += ["--profile", profile]
    args = _command(binary, rest)

    creation = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
    handle = open(log, "wb")
    try:
        proc = subprocess.Popen(
            args,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd or str(Path.home()),
            creationflags=creation,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
    except OSError as exc:
        handle.close()
        return {"error": f"could not start: {exc}"}
    finally:
        try:
            handle.close()
        except OSError:
            pass

    job = {
        "id": job_id,
        "pid": proc.pid,
        "prompt": _SECRET.sub("[REDACTED]", prompt[:2000]),
        "session": session_id or "",
        "profile": profile or "",
        "started": time.time(),
        "ended": 0,
        "returncode": None,
        "log": str(log),
    }
    with _LOCK:
        jobs = _read()
        jobs.append(job)
        _write(jobs)
    _watch(job_id)
    return {"ok": True, "job": job}


def _watch(job_id: str) -> None:
    def run():
        while True:
            with _LOCK:
                jobs = _read()
                job = next((j for j in jobs if j["id"] == job_id), None)
            if job is None or not alive(job.get("pid")):
                break
            time.sleep(2.0)
        with _LOCK:
            jobs = _read()
            job = next((j for j in jobs if j["id"] == job_id), None)
            if job is None:
                return
            job["ended"] = time.time()
            job["alive"] = False
            if not job.get("session"):
                job["session"] = _recent_session(job["started"])
            _write(jobs)

    threading.Thread(target=run, name=f"mission-watch-{job_id}", daemon=True).start()


def _recent_session(since: float, window: float = 1800.0) -> str:
    try:
        return store.newest_since(since, window)
    except Exception:
        return ""


def stop(job_id: str) -> dict:
    with _LOCK:
        jobs = _read()
        job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return {"error": "unknown job"}
    pid = int(job.get("pid") or 0)
    if not alive(pid):
        return {"ok": True, "already_dead": True}
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=20)
    else:
        os.kill(pid, 15)
    with _LOCK:
        jobs = _read()
        for entry in jobs:
            if entry["id"] == job_id:
                entry["ended"] = entry.get("ended") or time.time()
                entry["stopped"] = True
        _write(jobs)
    return {"ok": True, "pid": pid}


def tail(job_id: str, chars: int = 4000) -> str:
    with _LOCK:
        job = next((j for j in _read() if j["id"] == job_id), None)
    if not job:
        return ""
    try:
        text = Path(job["log"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _SECRET.sub("[REDACTED]", text[-chars:])


def listing(limit: int = 12) -> list[dict]:
    with _LOCK:
        jobs = _read()
    out = []
    changed = False
    for job in reversed(jobs[-limit:]):
        live = alive(job.get("pid"))
        if live and job.get("ended"):
            live = False
        if not live and not job.get("session"):
            found = _recent_session(job["started"])
            if found:
                job["session"] = found
                changed = True
        out.append({
            **job,
            "alive": live,
            "elapsed": round((job.get("ended") or time.time()) - job["started"], 1),
            "tail": tail(job["id"], 1500),
        })
    if changed:
        with _LOCK:
            current = {j["id"]: j for j in _read()}
            for entry in out:
                if entry["session"] and entry["id"] in current:
                    current[entry["id"]]["session"] = entry["session"]
            _write(list(current.values()))
    return out
