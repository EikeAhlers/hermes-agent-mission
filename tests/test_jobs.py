import os
from pathlib import Path

from mission import jobs


def test_command_wraps_cmd_files():
    assert jobs._command("C:/x/hermes.exe", ["-z", "hi"]) == ["C:/x/hermes.exe", "-z", "hi"]
    assert jobs._command("C:/x/hermes.cmd", ["-z", "hi"]) == ["cmd.exe", "/c", "C:/x/hermes.cmd", "-z", "hi"]


def test_empty_prompt_rejected():
    assert "error" in jobs.start("   ")


def test_unknown_job_stop():
    assert "error" in jobs.stop("doesnotexist")


def test_alive_false_for_unused_pid():
    assert jobs.alive(None) is False
    assert jobs.alive(999999) is False


def test_listing_keeps_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_dir", lambda: tmp_path)
    log = tmp_path / "abc.log"
    log.write_text("hallo welt\n", encoding="utf-8")
    jobs._write([{"id": "abc", "pid": 0, "prompt": "p", "session": "", "started": 1.0,
                  "ended": 2.0, "log": str(log)}])
    rows = jobs.listing()
    assert rows and rows[0]["tail"].strip() == "hallo welt"
    assert rows[0]["alive"] is False


def test_secrets_redacted():
    text = "sk-abcdefghijklmnopqrstuv"
    assert "[REDACTED]" in jobs._SECRET.sub("[REDACTED]", text)
