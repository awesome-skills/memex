#!/usr/bin/env python3
"""Tests for recall.py and read_session.py."""

import json
import os
import sqlite3
import sys
import tempfile

# Import from same directory
sys.path.insert(0, os.path.dirname(__file__))
from recall import (
    extract_text,
    parse_iso_timestamp,
    parse_claude_session,
    parse_codex_session,
    create_schema,
    migrate_schema,
    index_sessions,
    search,
    format_timestamp,
)
from read_session import (
    extract_text as rs_extract_text,
    detect_format,
    iter_messages,
)

passed = 0
failed = 0


def test(name, got, expected):
    global passed, failed
    if got == expected:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    got:      {got!r}")


# — extract_text ———————————————————————————————————————————————————————————

def test_extract_text():
    print("extract_text")

    test("plain string", extract_text("hello"), "hello")
    test("empty string", extract_text(""), "")
    test("none-ish", extract_text(None), "")
    test("number", extract_text(42), "")

    test("text block", extract_text([{"type": "text", "text": "hi"}]), "hi")
    test("input_text block", extract_text([{"type": "input_text", "text": "hi"}]), "hi")
    test("output_text block", extract_text([{"type": "output_text", "text": "hi"}]), "hi")

    test("skips tool_use", extract_text([
        {"type": "tool_use", "text": "skip"},
        {"type": "text", "text": "keep"},
    ]), "keep")

    test("skips tool_result", extract_text([{"type": "tool_result", "text": "skip"}]), "")
    test("skips thinking", extract_text([{"type": "thinking", "text": "skip"}]), "")
    test("skips image", extract_text([{"type": "image", "text": "skip"}]), "")

    test("multiple blocks joined", extract_text([
        {"type": "text", "text": "a"},
        {"type": "text", "text": "b"},
    ]), "a\nb")

    test("filters empty text", extract_text([
        {"type": "text", "text": ""},
        {"type": "text", "text": "real"},
    ]), "real")

    test("skips non-dict blocks", extract_text(["not a dict", {"type": "text", "text": "ok"}]), "ok")


# — parse_iso_timestamp ————————————————————————————————————————————————————

def test_parse_iso_timestamp():
    print("parse_iso_timestamp")

    # Verify it parses and returns milliseconds (exact value depends on local tz)
    z_result = parse_iso_timestamp("2026-03-03T00:26:57.352Z")
    test("Z format is int", isinstance(z_result, int), True)
    test("Z format in reasonable range", 1770000000000 < z_result < 1780000000000, True)
    test("numeric passthrough", parse_iso_timestamp(1234567890), 1234567890)
    test("float passthrough", parse_iso_timestamp(1234567890.5), 1234567890)
    test("none", parse_iso_timestamp(None), None)
    test("empty string", parse_iso_timestamp(""), None)
    test("garbage", parse_iso_timestamp("not a date"), None)


# — format_timestamp ———————————————————————————————————————————————————————

def test_format_timestamp():
    print("format_timestamp")

    test("zero", format_timestamp(0), "unknown")
    test("none", format_timestamp(None), "unknown")
    # 2026-01-01T00:00:00Z = 1767225600000 ms
    result = format_timestamp(1767225600000)
    test("valid date has correct format", len(result), 10)  # YYYY-MM-DD
    test("valid date starts with 2025 or 2026", result[:3], "202")


# — parse_claude_session ———————————————————————————————————————————————————

def test_parse_claude_session():
    print("parse_claude_session")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        entries = [
            {"type": "user", "cwd": "/home/test/project", "slug": "test-session",
             "timestamp": "2026-03-01T12:00:00Z",
             "message": {"role": "user", "content": "hello world"}},
            {"type": "assistant", "timestamp": "2026-03-01T12:00:01Z",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]}},
            {"type": "tool_call", "timestamp": "2026-03-01T12:00:02Z"},
        ]
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.flush()

        meta, msgs = parse_claude_session(f.name)
        test("source", meta["source"], "claude")
        test("project", meta["project"], "/home/test/project")
        test("slug", meta["slug"], "test-session")
        test("message count", len(msgs), 2)
        test("user msg", msgs[0], ("user", "hello world"))
        test("assistant msg", msgs[1], ("assistant", "hi there"))

        os.unlink(f.name)


# — parse_codex_session ————————————————————————————————————————————————————

def test_parse_codex_session():
    print("parse_codex_session")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False,
        prefix="rollout-2026-03-01T12-00-00-"
    ) as f:
        entries = [
            {"id": "abc123", "timestamp": "2026-03-01T12:00:00Z", "instructions": "..."},
            {"record_type": "state"},
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "<environment_context>\nCurrent working directory: /home/test/codex\n</environment_context>"}
            ]},
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "fix the bug"}
            ], "timestamp": "2026-03-01T12:00:01Z"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "on it"}
            ], "timestamp": "2026-03-01T12:00:02Z"},
            {"record_type": "state"},
        ]
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.flush()

        meta, msgs = parse_codex_session(f.name)
        test("source", meta["source"], "codex")
        test("session_id from entry", meta["session_id"], "abc123")
        test("project from env context", meta["project"], "/home/test/codex")
        test("skips state + env context", len(msgs), 2)
        test("user msg", msgs[0], ("user", "fix the bug"))
        test("assistant msg", msgs[1], ("assistant", "on it"))

        os.unlink(f.name)


# — schema + migration —————————————————————————————————————————————————————

def test_schema():
    print("schema + migration")

    conn = sqlite3.connect(":memory:")
    create_schema(conn)

    # Verify source column exists
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
    test("sessions has source col", "source" in cols, True)

    # Test migration on old schema (no source column)
    conn2 = sqlite3.connect(":memory:")
    conn2.executescript("""
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, project TEXT, slug TEXT, timestamp INTEGER, mtime REAL);
        CREATE VIRTUAL TABLE messages USING fts5(session_id UNINDEXED, role, text, tokenize='porter unicode61');
    """)
    migrate_schema(conn2)
    cols2 = [r[1] for r in conn2.execute("PRAGMA table_info(sessions)")]
    test("migration adds source col", "source" in cols2, True)

    conn.close()
    conn2.close()


# — index + search (integration) ———————————————————————————————————————————

def test_index_and_search():
    print("index + search (integration)")

    conn = sqlite3.connect(":memory:")
    create_schema(conn)

    # Create a temp dir with a Claude session
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a session file
        session_path = os.path.join(tmpdir, "test-session.jsonl")
        with open(session_path, "w") as f:
            f.write(json.dumps({
                "type": "user", "cwd": "/test", "slug": "my-slug",
                "timestamp": "2026-03-01T12:00:00Z",
                "message": {"role": "user", "content": "search for bananas"}
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant", "timestamp": "2026-03-01T12:00:01Z",
                "message": {"role": "assistant", "content": "found some bananas"}
            }) + "\n")

        # Manually index this file
        meta, msgs = parse_claude_session(session_path)
        mtime = os.path.getmtime(session_path)
        conn.execute(
            "INSERT INTO sessions (session_id, source, project, slug, timestamp, mtime) VALUES (?, ?, ?, ?, ?, ?)",
            (meta["session_id"], meta["source"], meta["project"], meta["slug"], meta["timestamp"], mtime),
        )
        for role, text in msgs:
            conn.execute("INSERT INTO messages (session_id, role, text) VALUES (?, ?, ?)",
                         (meta["session_id"], role, text))
        conn.commit()

        results = search(conn, "bananas")
        test("finds match", len(results), 1)
        test("session_id", results[0][0], "test-session")
        test("source is claude", results[0][1], "claude")

        results2 = search(conn, "xyznothing")
        test("no match", len(results2), 0)

        results3 = search(conn, "bananas", source="codex")
        test("source filter excludes", len(results3), 0)

    conn.close()


# — detect_format (read_session) ———————————————————————————————————————————

def test_detect_format():
    print("detect_format")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"parentUuid": None, "message": {"role": "user", "content": "hi"}}) + "\n")
        f.flush()
        test("claude format", detect_format(f.name), "claude")
        os.unlink(f.name)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"id": "abc", "instructions": "...", "timestamp": "2026-01-01T00:00:00Z"}) + "\n")
        f.flush()
        test("codex format (instructions)", detect_format(f.name), "codex")
        os.unlink(f.name)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps({"record_type": "state"}) + "\n")
        f.flush()
        test("codex format (state)", detect_format(f.name), "codex")
        os.unlink(f.name)


# — iter_messages (read_session) ————————————————————————————————————————————

def test_iter_messages():
    print("iter_messages")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        entries = [
            {"parentUuid": None, "type": "user",
             "message": {"role": "user", "content": "hello"}},
            {"parentUuid": "x", "type": "assistant",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "world"}]}},
        ]
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.flush()

        msgs = list(iter_messages(f.name))
        test("claude messages count", len(msgs), 2)
        test("claude user msg", msgs[0], ("user", "hello"))
        test("claude assistant msg", msgs[1], ("assistant", "world"))
        os.unlink(f.name)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        entries = [
            {"id": "abc", "instructions": "...", "timestamp": "2026-01-01T00:00:00Z"},
            {"record_type": "state"},
            {"role": "user", "content": [{"type": "input_text", "text": "<user_instructions>skip</user_instructions>"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "real message"}]},
            {"record_type": "state"},
        ]
        for e in entries:
            f.write(json.dumps(e) + "\n")
        f.flush()

        msgs = list(iter_messages(f.name))
        test("codex skips state + instructions", len(msgs), 1)
        test("codex keeps real message", msgs[0], ("user", "real message"))
        os.unlink(f.name)


# — read_session extract_text consistency —————————————————————————————————

def test_extract_text_consistency():
    print("extract_text consistency (recall vs read_session)")

    cases = [
        "plain string",
        [{"type": "text", "text": "a"}, {"type": "input_text", "text": "b"}],
        [{"type": "tool_use", "text": "skip"}],
        [],
        None,
    ]
    for case in cases:
        test(f"consistent for {type(case).__name__}", extract_text(case), rs_extract_text(case))


# — run all ————————————————————————————————————————————————————————————————

if __name__ == "__main__":
    test_extract_text()
    test_parse_iso_timestamp()
    test_format_timestamp()
    test_parse_claude_session()
    test_parse_codex_session()
    test_schema()
    test_index_and_search()
    test_detect_format()
    test_iter_messages()
    test_extract_text_consistency()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
