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


# ── Hermes Agent ─────────────────────────────────────────────────────────────

HERMES_COUNTS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")


def hermes_db(home: Path, sessions, usage=None, with_usage_table=True) -> Path:
    """A Hermes state.db with the columns the collector reads. `sessions` rows
    are dicts; counts default to 0."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, parent_session_id TEXT, "
                "started_at REAL NOT NULL, model TEXT, billing_provider TEXT, cwd TEXT, git_branch TEXT, "
                + ", ".join(f"{c} INTEGER DEFAULT 0" for c in HERMES_COUNTS) + ")")
    for s in sessions:
        con.execute(f"INSERT INTO sessions ({', '.join(s)}) VALUES ({', '.join('?' * len(s))})", tuple(s.values()))
    if with_usage_table:
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, billing_provider TEXT, "
                    "task TEXT NOT NULL DEFAULT '', "
                    + ", ".join(f"{c} INTEGER NOT NULL DEFAULT 0" for c in HERMES_COUNTS) + ")")
        for u in usage or []:
            con.execute(f"INSERT INTO session_model_usage ({', '.join(u)}) VALUES ({', '.join('?' * len(u))})",
                        tuple(u.values()))
    con.commit(); con.close()
    return home / "state.db"


SID = "20261004_114920_243d4c"


def test_hermes_counts_a_session_per_model_and_keeps_side_work_apart(tmp_path):
    hermes_db(tmp_path, [
        {"id": SID, "source": "cli", "started_at": 1759578560.5, "model": "claude-sonnet-5",
         "billing_provider": "anthropic", "cwd": "/nowhere/app", "input_tokens": 130, "output_tokens": 30,
         "cache_read_tokens": 1000},
    ], [
        {"session_id": SID, "model": "claude-sonnet-5", "billing_provider": "anthropic",
         "input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 1000},
        {"session_id": SID, "model": "qwen/qwen3.7-flash", "billing_provider": "openrouter",
         "input_tokens": 30, "output_tokens": 10},
        {"session_id": SID, "model": "gemini-3-flash", "billing_provider": "google", "task": "title_generation",
         "input_tokens": 50, "output_tokens": 5},
    ])
    found = {t["turn_key"]: t for t in mc.read_hermes(tmp_path, new_state(), False)}
    assert set(found) == {"session:claude-sonnet-5", "session:openrouter/qwen/qwen3.7-flash",
                          "session:gemini-3-flash:title_generation"}
    main = found["session:claude-sonnet-5"]
    assert (main["source"], main["session_id"], main["snapshot"], main["origin"]) == ("hermes", SID, True, "interactive")
    assert (main["input_tokens"], main["output_tokens"], main["cache_read"], main["total_tokens"]) == (100, 20, 1000, 1120)
    assert main["ts"] == "2025-10-04T11:49:20.500000"
    assert main["project"] == "/nowhere/app"
    # the session row already holds the two main-loop rows: nothing left over
    assert found["session:openrouter/qwen/qwen3.7-flash"]["input_tokens"] == 30


def test_hermes_usage_the_per_model_table_missed_goes_to_the_session_model(tmp_path):
    """A gateway writes absolute totals to the session row only."""
    hermes_db(tmp_path, [
        {"id": SID, "source": "telegram", "started_at": 1759578560, "model": "glm-5.2",
         "billing_provider": "ollama", "input_tokens": 500, "output_tokens": 50},
    ], [
        {"session_id": SID, "model": "glm-5.2", "billing_provider": "ollama", "input_tokens": 200, "output_tokens": 20},
    ])
    [t] = list(mc.read_hermes(tmp_path, new_state(), False))
    assert (t["turn_key"], t["input_tokens"], t["output_tokens"]) == ("session:ollama/glm-5.2", 500, 50)


def test_hermes_reads_a_database_older_than_its_per_model_table(tmp_path):
    hermes_db(tmp_path, [{"id": SID, "source": "cron", "started_at": 1759578560, "model": "nvidia/nemotron-3.5",
                          "billing_provider": "nvidia", "input_tokens": 7, "output_tokens": 3}],
              with_usage_table=False)
    [t] = list(mc.read_hermes(tmp_path, new_state(), False))
    assert (t["turn_key"], t["model_id"], t["total_tokens"], t["origin"]) == (
        "session:nvidia/nemotron-3.5", "nvidia/nemotron-3.5", 10, "automated")


def test_hermes_reads_every_profile_and_sends_a_session_again_only_when_it_grew(tmp_path):
    row = {"source": "cli", "started_at": 1759578560, "model": "m", "input_tokens": 5, "output_tokens": 1}
    hermes_db(tmp_path, [{"id": "a", **row}], with_usage_table=False)
    hermes_db(tmp_path / "profiles" / "work", [{"id": "b", "parent_session_id": "a", **row}], with_usage_table=False)
    state = new_state()
    found = {t["session_id"]: t for t in mc.read_hermes(tmp_path, state, False)}
    assert set(found) == {"a", "b"} and found["b"]["origin"] == "subagent"
    assert list(mc.read_hermes(tmp_path, state, False)) == []
    import sqlite3
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("UPDATE sessions SET output_tokens = 9 WHERE id = 'a'")
    con.commit(); con.close()
    [t] = list(mc.read_hermes(tmp_path, state, False))
    assert (t["session_id"], t["output_tokens"]) == ("a", 9)


def atif(steps, provider="nvidia", session=SID, agent="Hermes Agent"):
    """A NeMo Relay trajectory: the Hermes session id lives in each API request id."""
    return {
        "schema_version": "ATIF-v1.7", "session_id": "01a106bf-5f51-7042-9bd4-69b00bc91a33",
        "agent": {"name": agent, "version": "0.8.3", "model_name": "nvidia/nemotron-3.5-lightning-30b-a3b"},
        "extra": {"observed_events": [
            {"kind": "scope", "name": "hermes.session", "metadata": {"hermes.execution_surface": "cli"}},
            {"kind": "scope", "name": "openai.chat_completions",
             "metadata": {"api_request_id": f"{session}:8d4b:caf9:api:1", "hermes.provider": provider}},
        ]},
        "steps": steps,
    }


def agent_step(prompt, completion, cached=0, model="nvidia/nemotron-3.5-lightning-30b-a3b",
               ts="2026-10-04T11:49:31.876102602+00:00"):
    m = {"prompt_tokens": prompt, "completion_tokens": completion, "extra": {"total_tokens": prompt + completion}}
    if cached:
        m["cached_tokens"] = cached
    return {"source": "agent", "model_name": model, "timestamp": ts, "metrics": m}


def write_trajectory(root: Path, data, name="trajectory-01a1.json") -> Path:
    fp = root / "artifacts" / "runs" / "r1" / "atif" / name
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(data))
    return fp


def test_a_relay_trajectory_counts_its_hermes_session(tmp_path):
    write_trajectory(tmp_path, atif([
        {"source": "user", "timestamp": "2026-10-04T11:49:20Z"},
        agent_step(3462, 70), agent_step(3537, 39, cached=2176),
    ]))
    [t] = list(mc.read_hermes_relay(tmp_path, new_state(), False))
    assert (t["source"], t["session_id"], t["turn_key"]) == ("hermes", SID, "session:nvidia/nemotron-3.5-lightning-30b-a3b")
    # prompt_tokens counts the cached part
    assert (t["input_tokens"], t["cache_read"], t["output_tokens"]) == (3462 + 3537 - 2176, 2176, 109)
    assert (t["ts"], t["snapshot"], t["origin"]) == ("2026-10-04T11:49:20", True, "interactive")


def test_a_relay_trajectory_names_a_resold_model_by_its_host(tmp_path):
    write_trajectory(tmp_path, atif([agent_step(10, 1, model="anthropic/claude-sonnet-5")], provider="openrouter"))
    [t] = list(mc.read_hermes_relay(tmp_path, new_state(), False))
    assert t["model_id"] == "openrouter/anthropic/claude-sonnet-5"


def test_relay_reads_a_trajectory_once_and_skips_other_agents(tmp_path):
    write_trajectory(tmp_path, atif([agent_step(10, 1)]))
    write_trajectory(tmp_path, atif([agent_step(10, 1)], agent="Other Agent"), name="trajectory-other.json")
    state = new_state()
    assert len(list(mc.read_hermes_relay(tmp_path, state, False))) == 1
    assert list(mc.read_hermes_relay(tmp_path, state, False)) == []


def test_a_session_in_both_records_is_stored_once(tmp_path):
    """Same source, session and key: the hub keeps one row."""
    hermes_db(tmp_path / "hermes", [
        {"id": SID, "source": "cli", "started_at": 1759578560, "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
         "billing_provider": "nvidia", "input_tokens": 4823, "output_tokens": 109, "cache_read_tokens": 2176},
    ], with_usage_table=False)
    write_trajectory(tmp_path / "relay", atif([agent_step(3462, 70), agent_step(3537, 39, cached=2176)]))
    [a] = list(mc.read_hermes(tmp_path / "hermes", new_state(), False))
    [b] = list(mc.read_hermes_relay(tmp_path / "relay", new_state(), False))
    assert (a["source"], a["session_id"], a["turn_key"]) == (b["source"], b["session_id"], b["turn_key"])
    assert (a["input_tokens"], a["cache_read"], a["output_tokens"]) == (b["input_tokens"], b["cache_read"], b["output_tokens"])


def test_relay_has_no_default_folder(tmp_path):
    cfg = {"paths": {}}
    assert mc.source_path(cfg, "hermes_relay") is None
    assert mc.source_path(cfg, "hermes") == Path.home() / ".hermes"
    cfg["paths"]["hermes_relay"] = str(tmp_path)
    assert mc.source_path(cfg, "hermes_relay") == tmp_path


def test_setup_points_a_source_at_a_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path / "home"))
    run = lambda *paths: mc.main(["setup", "--hub", "http://hub/", "--key", "k",
                                  *[a for p in paths for a in ("--path", p)]])
    assert run("hermes_relay=/srv/runs") == 0
    assert mc.load_config()["paths"] == {"hermes_relay": "/srv/runs"}
    assert run("hermes_relay=") == 0                       # back to its default
    assert mc.load_config()["paths"] == {}
    assert run("nonsense=/x") == 2


def test_install_on_linux_writes_a_systemd_user_timer(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(mc, "HOME", tmp_path)
    monkeypatch.setattr(mc.sys, "platform", "linux")
    monkeypatch.setattr(mc.shutil, "which", lambda name: "/usr/bin/" + name)
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "yes\n", ""

    monkeypatch.setattr(mc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    assert mc.cmd_install(300) == 0
    units = tmp_path / ".config" / "systemd" / "user"
    assert "OnUnitActiveSec=300" in (units / "meterlex-collector.timer").read_text()
    assert " run\n" in (units / "meterlex-collector.service").read_text()
    assert ["systemctl", "--user", "enable", "--now", "meterlex-collector.timer"] in calls


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
