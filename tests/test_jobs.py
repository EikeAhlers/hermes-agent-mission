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


def test_transcript_shape():
    from mission import store

    data = store.transcript("local-c9d3354eaa16384122a9f68b4230b1f6d094f467a1d881794c06d6f4db7c3107")
    assert set(data) >= {"session", "title", "messages"}
    assert isinstance(data["messages"], list)
    for m in data["messages"]:
        assert {"id", "role", "text"} <= set(m)


def test_transcript_unknown_session_is_empty():
    from mission import store

    assert store.transcript("gibt-es-nicht")["messages"] == []
