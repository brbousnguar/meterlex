"""The collector that runs on each machine (#4): one standard-library file."""
import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("meterlex_collector", ROOT / "collector" / "meterlex_collector.py")
mc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mc)


def new_state():
    return {"files": {}, "snapshots": {}}


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def claude_line(uuid, out, msg_id="msg_1", **extra):
    return {
        "type": "assistant", "uuid": uuid, "sessionId": "s1", "timestamp": "2026-09-15T10:00:00Z",
        "cwd": "/work/app", "entrypoint": "cli",
        "message": {"id": msg_id, "model": "claude-sonnet-5", "usage": {
            "input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 20,
        }},
        **extra,
    }


def test_a_claude_reply_logged_in_parts_is_one_record(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl",
                [claude_line("u1", 0), claude_line("u2", 0), claude_line("u3", 42), {"type": "user"}])
    [t] = list(mc.read_claude_code(tmp_path, new_state(), False))
    assert (t["turn_key"], t["output_tokens"], t["alt_keys"], t["origin"]) == ("msg_1", 42, ["u1", "u2", "u3"], "interactive")
    assert (t["ts"], t["total_tokens"]) == ("2026-09-15T10:00:00", 10 + 42 + 100 + 20)


def test_subagents_and_automated_runs_are_told_apart(tmp_path):
    write_jsonl(tmp_path / "p" / "s1.jsonl", [
        claude_line("u1", 1, "m1", isSidechain=True), claude_line("u2", 1, "m2", entrypoint="sdk-cli"),
    ])
    assert {t["turn_key"]: t["origin"] for t in mc.read_claude_code(tmp_path, new_state(), False)} == {
        "m1": "subagent", "m2": "automated",
    }


def test_reading_resumes_where_it_stopped(tmp_path):
    state, path = new_state(), tmp_path / "p" / "s1.jsonl"
    write_jsonl(path, [claude_line("u1", 5, "m1")])
    assert len(list(mc.read_claude_code(tmp_path, state, False))) == 1
    assert list(mc.read_claude_code(tmp_path, state, False)) == []
    with open(path, "a") as fh:
        fh.write(json.dumps(claude_line("u2", 6, "m2")) + "\n" + '{"type": "assist')  # still being written
    assert [t["turn_key"] for t in mc.read_claude_code(tmp_path, state, False)] == ["m2"]


def test_gemini_cli_counts_each_message_once(tmp_path):
    gem = tmp_path / ".gemini"
    gem.mkdir()
    (gem / "projects.json").write_text(json.dumps({"projects": {"/work/expenses": "expenses"}}))
    msg = {"id": "g1", "type": "gemini", "timestamp": "2026-05-11T09:42:22Z", "model": "gemini-3-flash-preview",
           "tokens": {"input": 9204, "output": 183, "cached": 7448, "thoughts": 321, "tool": 0, "total": 9708}}
    write_jsonl(gem / "tmp" / "expenses" / "chats" / "session-1.jsonl",
                [{"sessionId": "x"}, msg, {"$set": {}}, msg, msg])
    [t] = list(mc.read_gemini_cli(gem, new_state(), False))
    # cached input is its own count; thoughts are billed as output
    assert (t["project"], t["input_tokens"], t["cache_read"], t["output_tokens"], t["reasoning_tokens"]) == (
        "/work/expenses", 1756, 7448, 504, 321)


def test_codex_skips_a_repeated_token_event(tmp_path):
    def token_count(ts, total):
        return {"type": "event_msg", "timestamp": ts, "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": total},
            "last_token_usage": {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4},
        }}}
    write_jsonl(tmp_path / "2026" / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"id": "c1", "cwd": "/work", "originator": "codex-tui", "model_provider": "openai"}},
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol"}},
        token_count("2026-09-15T10:00:00Z", 12), token_count("2026-09-15T10:00:01Z", 12),
        token_count("2026-09-15T10:00:02Z", 24),
    ])
    turns = list(mc.read_codex(tmp_path, new_state(), False))
    assert [t["turn_key"] for t in turns] == ["2026-09-15T10:00:00Z", "2026-09-15T10:00:02Z"]
    assert (turns[0]["model_id"], turns[0]["origin"]) == ("gpt-5.6-sol", "interactive")


def test_copilot_reads_per_model_totals(tmp_path):
    write_jsonl(tmp_path / "session-state" / "abc" / "events.jsonl", [
        {"type": "session.start", "timestamp": "2026-09-01T10:00:00Z", "data": {"context": {"cwd": "/work/rbb"}}},
        {"type": "session.shutdown", "timestamp": "2026-09-01T10:05:00Z", "data": {"modelMetrics": {"claude-sonnet-5": {
            "usage": {"inputTokens": 182080, "outputTokens": 1262, "cacheReadTokens": 132199,
                      "cacheWriteTokens": 49873, "reasoningTokens": 483}}}}},
    ])
    [t] = list(mc.read_copilot(tmp_path, new_state(), False))
    # inputTokens includes both cache parts: 8 + 132,199 + 49,873
    assert (t["turn_key"], t["input_tokens"], t["cache_read"], t["cache_write"], t["project"], t["snapshot"]) == (
        "session:claude-sonnet-5", 8, 132199, 49873, "/work/rbb", True)


# ── OpenClaw agents ──────────────────────────────────────────────────────────

def openclaw_db(root: Path, agent: str, events) -> Path:
    import sqlite3
    db = root / "agents" / agent / "agent" / "openclaw-agent.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trajectory_runtime_events (session_id TEXT, seq INT, run_id TEXT, "
                "event_json TEXT, created_at INT)")
    for created_at, event in events:
        con.execute("INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)",
                    (event.get("sessionId"), event.get("seq"), event.get("runId"),
                     json.dumps(event), created_at))
    con.commit(); con.close()
    return db


def model_completed(seq, model="claude-sonnet-5", provider="anthropic",
                    session_key="agent:ecu:telegram:direct:42", **usage):
    u = {"input": 10, "output": 5, "cacheRead": 100, "cacheWrite": 20, "total": 135}
    u.update(usage)
    return {
        "type": "model.completed", "modelId": model, "provider": provider, "seq": seq,
        "runId": "run-1", "sessionId": "sess-1", "sessionKey": session_key,
        "ts": "2026-09-16T08:00:00Z", "data": {"usage": u},
    }


def test_openclaw_counts_each_model_call_under_its_agent(tmp_path):
    openclaw_db(tmp_path, "ecu", [(1, model_completed(1)), (2, {"type": "prompt.submitted", "seq": 2})])
    [t] = list(mc.read_openclaw(tmp_path, new_state(), False))
    assert t["source"] == "openclaw"
    assert t["project"] == "ecu"              # the agent is the "where" of an agent run
    assert t["model_id"] == "claude-sonnet-5"
    assert (t["turn_key"], t["total_tokens"], t["cache_read"]) == ("run-1:1", 135, 100)
    assert t["origin"] == "interactive"


def test_openclaw_resumes_after_the_last_event_it_read(tmp_path):
    openclaw_db(tmp_path, "nova", [(10, model_completed(1))])
    state = new_state()
    assert len(list(mc.read_openclaw(tmp_path, state, False))) == 1
    assert list(mc.read_openclaw(tmp_path, state, False)) == []      # nothing new
    db = tmp_path / "agents" / "nova" / "agent" / "openclaw-agent.sqlite"
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)",
                ("sess-1", 2, "run-1", json.dumps(model_completed(2)), 20))
    con.commit(); con.close()
    [t] = list(mc.read_openclaw(tmp_path, state, False))
    assert t["turn_key"] == "run-1:2"


@pytest.mark.parametrize("session_key,origin", [
    ("agent:forge:cron:abc", "automated"),
    ("agent:nova:main", "automated"),               # the agent's own unattended session
    ("agent:nova:agent:camille:main", "subagent"),
    ("agent:ecu:signal:direct:1", "interactive"),
    ("agent:ecu:telegram:direct:42", "interactive"),
])
def test_openclaw_origin_from_the_session_key(tmp_path, session_key, origin):
    openclaw_db(tmp_path, "a", [(1, model_completed(1, session_key=session_key))])
    [t] = list(mc.read_openclaw(tmp_path, new_state(), False))
    assert t["origin"] == origin


@pytest.mark.parametrize("provider,model,expected", [
    ("ollama", "qwen3.6:35b-mlx", "ollama/qwen3.6:35b-mlx"),
    ("openrouter", "anthropic/claude-sonnet-4.6", "openrouter/anthropic/claude-sonnet-4.6"),
    ("anthropic", "claude-sonnet-5", "claude-sonnet-5"),
    ("google", "gemini-3-flash-preview", "gemini-3-flash-preview"),
])
def test_openclaw_names_a_resold_model_by_its_host(tmp_path, provider, model, expected):
    """The rate card keys resold models by who sells them."""
    openclaw_db(tmp_path, "a", [(1, model_completed(1, model=model, provider=provider))])
    [t] = list(mc.read_openclaw(tmp_path, new_state(), False))
    assert t["model_id"] == expected


@pytest.mark.parametrize("path, policy, expected", [
    ("/Users/me/Server/webapps/vitalex", "full", "/Users/me/Server/webapps/vitalex"),
    ("C:\\Users\\me\\RocheBB\\webapps\\rbb-em-dashboard", "basename", "rbb-em-dashboard"),
    ("/Users/me/Work/RocheBB/", "basename", "RocheBB"),
    ("", "basename", ""),
])
def test_project_labels(path, policy, expected):
    assert mc.label_project(path, policy, "salt") == expected


def test_hashed_labels_are_stable_and_opaque():
    label = mc.label_project("C:\\work\\secret-client", "hash", "s")
    assert label == mc.label_project("C:\\work\\secret-client", "hash", "s")
    assert label.startswith("p-") and "secret" not in label


def _config(tmp_path, **extra):
    none = str(tmp_path / "absent")
    cc = tmp_path / "cc"
    write_jsonl(cc / "p" / "s.jsonl", [claude_line("u1", 1)])
    paths = {name: none for name in mc.DEFAULT_PATHS}
    paths["claude_code"] = str(cc)
    return {"machine": "m", "labels": "full", "paths": paths, **extra}


def test_a_dry_run_sends_and_saves_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(mc, "post", lambda *a: pytest.fail("a dry run posted"))
    assert mc.cmd_run(_config(tmp_path), dry_run=True) == 0
    assert not (tmp_path / "home" / "state.json").exists()


def test_unsent_turns_wait_in_the_spool(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("METERLEX_HOME", str(home))
    cfg = _config(tmp_path, hub="http://hub", key="k")

    def down(cfg, batch):
        raise urllib.error.URLError("hub down")

    monkeypatch.setattr(mc, "post", down)
    assert mc.cmd_run(cfg) == 1
    assert len((home / "spool.jsonl").read_text().splitlines()) == 1
    sent = []
    monkeypatch.setattr(mc, "post", lambda cfg, batch: sent.extend(batch) or {})
    assert mc.cmd_run(cfg) == 0  # nothing new; the spooled turn goes
    assert len(sent) == 1 and (home / "spool.jsonl").read_text() == ""
