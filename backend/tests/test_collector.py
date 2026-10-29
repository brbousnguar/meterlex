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


# ── the Claude Max quota, from Claude Code's status line (#35) ────────────────

STATUS = {
    "model": {"display_name": "Opus 5.5"}, "workspace": {"current_dir": "/Users/me/Server"},
    "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1_000_000 + 7200},
                    "seven_day": {"used_percentage": 60, "resets_at": 1_000_000 + 3 * 86400},
                    "spend_limit": {"used_percentage": 5, "resets_at": 1}},
}


def test_statusline_records_new_readings_once_and_prints_the_pace(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    monkeypatch.setattr(mc.time, "time", lambda: 1_000_000)
    assert mc.cmd_statusline(io.StringIO(json.dumps(STATUS))) == 0
    # 4 of 7 days gone → 57%; 60% used is 3 points ahead
    assert capsys.readouterr().out.strip() == "Opus 5.5 · Server · week 60% · 3 pts ahead · 5h 24%"
    mc.cmd_statusline(io.StringIO(json.dumps(STATUS)))             # same reading again: not kept
    lines = (tmp_path / "quota.jsonl").read_text().splitlines()
    assert sorted(json.loads(l)["window"] for l in lines) == ["five_hour", "seven_day"]


def test_statusline_never_fails_on_bad_input(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    assert mc.cmd_statusline(io.StringIO("not json")) == 0
    assert mc.cmd_statusline(io.StringIO('{"rate_limits": {"seven_day": {"used_percentage": null}}}')) == 0
    assert not (tmp_path / "quota.jsonl").exists()


def test_run_sends_quota_readings_once(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    mc.record_quota(mc.quota_readings(STATUS, 1_000_000))
    sent = []
    monkeypatch.setattr(mc, "_hub", lambda cfg, path, body=None: sent.append(body) or {})
    cfg, state = {"machine": "mini", "hub": "http://h", "key": "k"}, {}
    assert mc.quota_flush(cfg, state) == (2, None)
    assert mc.quota_flush(cfg, state) == (0, None)
    assert [r["window"] for r in sent[0]["quota"]] == ["five_hour", "seven_day"] and sent[0]["turns"] == []

    def down(*a, **k):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(mc, "_hub", down)
    mc.record_quota([{"window": "seven_day", "used_pct": 61.0, "resets_at": 1_000_000 + 3 * 86400, "ts": "x"}])
    before = state["quota_offset"]
    assert mc.quota_flush(cfg, state)[1].startswith("hub unreachable")
    assert state["quota_offset"] == before                        # kept for the next run


def test_an_idle_session_s_stale_lower_figure_is_not_news(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    monkeypatch.setattr(mc.time, "time", lambda: 1_000_000)
    mc.cmd_statusline(io.StringIO(json.dumps(STATUS)))
    stale = json.loads(json.dumps(STATUS))
    stale["rate_limits"]["seven_day"]["used_percentage"] = 24
    mc.cmd_statusline(io.StringIO(json.dumps(stale)))
    assert "week 60%" in capsys.readouterr().out.splitlines()[-1]     # the newest known figure
    weeks = [json.loads(l) for l in (tmp_path / "quota.jsonl").read_text().splitlines()]
    assert [r["used_pct"] for r in weeks if r["window"] == "seven_day"] == [60.0]
    nxt = json.loads(json.dumps(STATUS))
    nxt["rate_limits"]["seven_day"].update(used_percentage=1, resets_at=STATUS["rate_limits"]["seven_day"]["resets_at"] + 7 * 86400)
    assert mc.record_quota(mc.quota_readings(nxt, 1_000_000)) == 1     # a new window is news


# ── a plan change: a session's own figure dropping is fresh news (#46) ────────

def _paint(session_id, pct, plan=None, resets=1_000_000 + 3 * 86400):
    import io
    data = {"session_id": session_id, "model": {"display_name": "Opus 5.5"},
            "rate_limits": {"seven_day": {"used_percentage": pct, "resets_at": resets}}}
    with patch_plan(plan):
        mc.cmd_statusline(io.StringIO(json.dumps(data)))


class patch_plan:
    def __init__(self, plan):
        self.plan = plan

    def __enter__(self):
        self.old = mc.claude_plan
        mc.claude_plan = lambda: self.plan

    def __exit__(self, *exc):
        mc.claude_plan = self.old


def _weeks(tmp_path):
    return [json.loads(l) for l in (tmp_path / "quota.jsonl").read_text().splitlines()
            if json.loads(l)["window"] == "seven_day"]


def test_a_session_whose_own_figure_drops_reports_a_plan_change(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    monkeypatch.setattr(mc.time, "time", lambda: 1_000_000)
    _paint("a", 73, "max_5x")
    _paint("a", 7, "max_5x")                    # the same session, a new answer: the plan grew
    assert "week 7%" in capsys.readouterr().out.splitlines()[-1]
    _paint("idle", 70, "max_5x")                # first seen, an old figure: not news after a fresh one
    _paint("a", 7, "max_5x")                    # a repaint
    _paint("a", 8, "max_5x")
    assert [(r["used_pct"], r.get("fresh", False)) for r in _weeks(tmp_path)] == [(73, False), (7, True), (8, True)]
    assert "week 8%" in capsys.readouterr().out.splitlines()[-1]


def test_a_new_plan_is_news_even_from_a_session_first_seen(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    monkeypatch.setattr(mc.time, "time", lambda: 1_000_000)
    (tmp_path / "quota-last.json").write_text(json.dumps({"seven_day": [73, 1_000_000 + 3 * 86400]}))  # collector 0.5
    _paint("b", 7, "default_claude_max_20x")
    _paint("c", 9, "default_claude_max_20x")    # first seen, higher than a fresh figure: waits for its own move
    assert [(r["used_pct"], r["plan"], r.get("fresh")) for r in _weeks(tmp_path)] == [(7, "default_claude_max_20x", True)]
    _paint("b", 8, None)                        # ~/.claude.json unreadable for a moment
    _paint("d", 70, "default_claude_max_20x")   # an idle session first seen: still not a plan change
    assert [r["used_pct"] for r in _weeks(tmp_path)] == [7, 8]


def test_sessions_are_forgotten_after_a_week_and_a_repaint_does_not_rewrite_state(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    clock = [1_000_000]
    monkeypatch.setattr(mc.time, "time", lambda: clock[0])
    _paint("old", 10)
    clock[0] += 9 * 86400
    _paint("new", 1, resets=1_000_000 + 10 * 86400)
    state = json.loads((tmp_path / "quota-last.json").read_text())
    assert list(state["sessions"]) == ["new"]
    before = (tmp_path / "quota-last.json").stat().st_mtime_ns
    clock[0] += 60
    _paint("new", 1, resets=1_000_000 + 10 * 86400)
    assert (tmp_path / "quota-last.json").stat().st_mtime_ns == before


def test_the_plan_comes_from_claude_code_s_own_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    assert mc.claude_plan() is None
    (tmp_path / ".claude.json").write_text(json.dumps({"oauthAccount": {
        "organizationRateLimitTier": "default_claude_max_20x", "userRateLimitTier": None}}))
    assert mc.claude_plan() == "default_claude_max_20x"
    assert mc.quota_readings(STATUS, 1_000_000, "default_claude_max_20x")[0]["plan"] == "default_claude_max_20x"


# ── the quota without a status line: a headless probe (#67) ───────────────────

# a real `claude -p --output-format stream-json --verbose` event (Claude Code 2.1.308)
RATE_EVENT = {"type": "rate_limit_event", "rate_limit_info": {
    "status": "allowed_warning", "resetsAt": 1_000_000 + 3 * 86400, "rateLimitType": "seven_day", "utilization": 0.88,
    "overageStatus": "rejected", "isUsingOverage": False, "surpassedThreshold": 0.75,
    "unifiedWindows": {"five_hour": {"utilization": 0.29, "resetsAt": 1_000_000 + 7200},
                       "seven_day": {"utilization": 0.88, "resetsAt": 1_000_000 + 3 * 86400}}},
    "uuid": "u", "session_id": "s"}
STREAM = "\n".join([json.dumps({"type": "system", "subtype": "init"}), "not json",
                    json.dumps(RATE_EVENT), json.dumps({"type": "result", "subtype": "success"})])


def test_a_probe_reads_both_windows_from_the_rate_limit_event():
    got = {r["window"]: r for r in mc.probe_readings(STREAM, 1_000_000, "default_claude_max_20x")}
    assert (got["seven_day"]["used_pct"], got["seven_day"]["resets_at"]) == (88.0, 1_000_000 + 3 * 86400)
    assert got["five_hour"]["used_pct"] == 29.0 and got["five_hour"]["plan"] == "default_claude_max_20x"
    older = {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "rateLimitType": "five_hour", "utilization": 0.4, "resetsAt": 1_000_000 + 60}}
    assert [(r["window"], r["used_pct"]) for r in mc.probe_readings(json.dumps(older), 1_000_000)] == [("five_hour", 40.0)]
    overage = {"type": "rate_limit_event", "rate_limit_info": {"rateLimitType": "overage", "utilization": 1}}
    assert mc.probe_readings(json.dumps(overage), 1_000_000) == [] and mc.probe_readings("", 1_000_000) == []


def test_a_probe_reading_is_always_kept_and_fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    _paint("a", 81, "max_20x")                                # a status line, then the desktop app only
    readings = mc.probe_readings(STREAM, 1_000_000 + 60, "max_20x")
    assert mc.record_quota(readings, now=1_000_000 + 60, direct=True) == 2
    assert mc.record_quota(readings, now=1_000_000 + 700, direct=True) == 2     # unchanged, still a time point
    assert [(r["used_pct"], r.get("fresh")) for r in _weeks(tmp_path)] == [(81, None), (88.0, True), (88.0, True)]
    assert json.loads((tmp_path / "quota-last.json").read_text())["read_at"] == 1_000_000 + 700


def _turn(model="claude-opus-5-5", ago=60, now=1_000_000.0):
    ts = mc.datetime.fromtimestamp(now - ago, tz=mc.timezone.utc).replace(tzinfo=None).isoformat()
    return {"source": "claude-code", "model_id": model, "ts": ts}


def test_a_probe_runs_only_after_claude_turns_and_not_twice_in_ten_minutes(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    now, cfg = 1_000_000.0, {"machine": "m"}
    assert mc.probe_due(cfg, {}, [_turn()], now)
    assert not mc.probe_due(cfg, {}, [], now)                                  # no use, no probe
    assert not mc.probe_due(cfg, {}, [_turn("qwen3.6:35b")], now)             # a local model on claude-code
    assert not mc.probe_due(cfg, {}, [_turn(ago=3600)], now)                  # `run --full`: old turns
    assert not mc.probe_due(cfg, {"probed_at": now - 300}, [_turn()], now)
    assert not mc.probe_due({"probe": False}, {}, [_turn()], now)
    (tmp_path / "quota-last.json").write_text(json.dumps({"read_at": now - 120}))  # a status line just read it
    assert not mc.probe_due(cfg, {}, [_turn()], now)
    assert mc.probe_due(cfg, {"probed_at": now - 900}, [_turn()], now + 600)


def test_a_probe_reads_the_tail_of_a_burst_the_last_probe_missed(tmp_path, monkeypatch):
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    now, cfg, state = 1_000_000.0, {"machine": "m"}, {"probed_at": 1_000_000.0 - 300}
    assert not mc.probe_due(cfg, state, [_turn(ago=60)], now)     # the burst's last turns, 5 min after a probe
    assert mc.probe_due(cfg, state, [], now + 360)                 # next run finds nothing new; they are unread
    assert not mc.probe_due(cfg, dict(state), [], now + 900)      # 16 min idle: a probe could open a window
    state["probed_at"] = now + 360
    assert not mc.probe_due(cfg, state, [], now + 1200)            # read after the last spend: nothing to read


def test_run_probes_and_sends_the_readings_in_the_same_pass(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("METERLEX_HOME", str(home))
    cfg = _config(tmp_path, hub="http://hub", key="k")
    calls, sent = [], []

    class Done:
        returncode, stdout = 0, STREAM

    def fake_run(args, **kw):
        calls.append(args)
        return Done()

    turn_now = mc._epoch("2026-09-15T10:00:00Z") + 120                         # claude_line's turn, 2 min old
    monkeypatch.setattr(mc.time, "time", lambda: turn_now)
    monkeypatch.setattr(mc, "claude_exe", lambda: "/bin/claude")
    monkeypatch.setattr(mc.subprocess, "run", fake_run)
    monkeypatch.setattr(mc, "post", lambda cfg, batch: {})
    monkeypatch.setattr(mc, "_hub", lambda cfg, path, body=None: sent.append(body) or {})
    assert mc.cmd_run(cfg) == 0
    assert len(calls) == 1 and "--no-session-persistence" in calls[0] and calls[0][calls[0].index("--tools") + 1] == ""
    assert sorted(r["window"] for r in sent[0]["quota"]) == ["five_hour", "seven_day"]
    assert all(r["fresh"] for r in sent[0]["quota"])
    write_jsonl(Path(cfg["paths"]["claude_code"]) / "p" / "t.jsonl", [claude_line("u2", 1, msg_id="msg_2")])
    assert mc.cmd_run(cfg) == 0 and len(calls) == 1                           # read a moment ago: no second call


SUBSCRIPTION = {"tier": "creator", "character_count": 121_886, "character_limit": 127_544,
                "next_character_count_reset_unix": 1_794_132_400}
STATS = {"time": [1_759_881_600_000, 1_759_968_000_000], "usage": {"All": [9122.0, 0.0]}}


def test_the_elevenlabs_key_comes_from_the_environment_or_an_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    env = tmp_path / "eleven.env"
    env.write_text('# ElevenLabs\nOTHER=x\nexport ELEVENLABS_API_KEY="sk_test"\n')
    assert mc.elevenlabs_key({}) is None
    assert mc.elevenlabs_key({"elevenlabs_env": str(env)}) == "sk_test"
    assert mc.elevenlabs_key({"elevenlabs_env": str(tmp_path / "missing.env")}) is None
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_env")
    assert mc.elevenlabs_key({"elevenlabs_env": str(env)}) == "sk_env"


def test_a_credit_reading_carries_the_count_and_the_days():
    r = mc.credit_reading(SUBSCRIPTION, STATS, 1_761_350_400.0)
    assert (r["provider"], r["used"], r["limit"], r["resets_at"], r["plan"]) == \
        ("elevenlabs", 121_886, 127_544, 1_794_132_400, "creator")
    assert r["days"] == [{"day": "2025-10-08", "used": 9122}, {"day": "2025-10-09", "used": 0}]
    assert mc.credit_reading({"tier": "free"}, STATS, 0) is None


def test_run_sends_the_credits_every_fifteen_minutes(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("METERLEX_HOME", str(tmp_path))
    env = tmp_path / "eleven.env"
    env.write_text("ELEVENLABS_API_KEY=sk_test\n")
    cfg, state, sent, asked = {"machine": "m", "hub": "http://hub", "key": "k", "elevenlabs_env": str(env)}, {}, [], []

    def fake_eleven(key, path):
        asked.append((key, path.split("?")[0]))
        return SUBSCRIPTION if path.startswith("/v1/user/") else STATS

    monkeypatch.setattr(mc, "_elevenlabs", fake_eleven)
    monkeypatch.setattr(mc, "_hub", lambda cfg, path, body=None: sent.append(body) or {})
    assert mc.credits_flush(cfg, state, 2_000_000.0) == (1, None)
    assert sent[0]["credits"][0]["used"] == 121_886 and sent[0]["turns"] == []
    assert ("sk_test", "/v1/user/subscription") in asked
    assert all("sk_test" not in json.dumps(b) for b in sent)                  # the key never reaches the hub
    assert mc.credits_flush(cfg, state, 2_000_000.0 + 600) == (0, None)       # not due yet
    assert mc.credits_flush(cfg, state, 2_000_000.0 + 900) == (1, None)
    assert mc.credits_flush({"machine": "m"}, {}, 2_000_000.0) == (0, None)   # no key, nothing asked


def test_an_elevenlabs_error_is_kept_and_tried_on_the_next_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test")

    def refuse(key, path):
        raise urllib.error.HTTPError(path, 401, "unauthorized", {}, None)

    monkeypatch.setattr(mc, "_elevenlabs", refuse)
    state = {}
    assert mc.credits_flush({"machine": "m"}, state, 2_000_000.0) == (0, None)
    assert state["credits_error"] == "ElevenLabs answered 401" and "credits_at" not in state


OPENROUTER_CREDITS = {"data": {"total_credits": 90, "total_usage": 86.4321}}
OPENAI_COSTS = {"object": "page", "has_more": False, "next_page": None, "data": [
    {"start_time": 1761091200, "end_time": 1761177600, "results": [          # 2025-10-22
        {"amount": {"value": 0.25, "currency": "usd"}, "line_item": "gpt-realtime, input"},
        {"amount": {"value": 1.5, "currency": "usd"}, "line_item": "gpt-realtime, output"}]},
    {"start_time": 1761177600, "end_time": 1761264000, "results": []},     # an empty day still counts
]}


def test_prepaid_balances_read_each_provider_with_its_own_key(tmp_path, monkeypatch):
    for var in ("OPENROUTER_API_KEY", "OPENAI_ADMIN_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "or.env").write_text("export OPENROUTER_API_KEY='sk-or-test'\n")
    (tmp_path / "oa.env").write_text("OPENAI_ADMIN_KEY=sk-admin-test\n")
    cfg = {"machine": "m", "hub": "http://hub", "key": "k",
           "openrouter_env": str(tmp_path / "or.env"), "openai_env": str(tmp_path / "oa.env")}
    asked, sent = [], []

    def fake_get(url, key):
        asked.append((url.split("?")[0], key))
        return OPENROUTER_CREDITS if "openrouter" in url else OPENAI_COSTS

    monkeypatch.setattr(mc, "_get_json", fake_get)
    monkeypatch.setattr(mc, "_hub", lambda cfg, path, body=None: sent.append(body) or {})
    state = {}
    assert mc.balances_flush(cfg, state, 2_000_000_000.0) == (2, None)
    assert ("https://openrouter.ai/api/v1/credits", "sk-or-test") in asked
    assert ("https://api.openai.com/v1/organization/costs", "sk-admin-test") in asked
    by = {r["provider"]: r for r in sent[0]["balances"]}
    assert by["openrouter"]["bought"] == 90_000_000 and by["openrouter"]["used"] == 86_432_100
    assert by["openai"]["days"] == [{"day": "2025-10-22", "spent": 1_750_000}, {"day": "2025-10-23", "spent": 0}]
    assert "sk-or-test" not in json.dumps(sent) and "sk-admin-test" not in json.dumps(sent)
    assert state["balances"]["openrouter"]["last"] == "openrouter $3.57 left of $90.00 bought"
    assert mc.balances_flush(cfg, state, 2_000_000_000.0 + 600) == (0, None)           # not due yet
    assert mc.balances_flush({"machine": "m"}, {}, 2_000_000_000.0) == (0, None)       # no key, nothing asked


def test_a_project_key_on_the_costs_api_says_an_admin_key_is_needed(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_ADMIN_KEY", "sk-proj-test")

    def refuse(url, key):
        raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)

    monkeypatch.setattr(mc, "_get_json", refuse)
    state = {}
    assert mc.balances_flush({"machine": "m"}, state, 2_000_000_000.0) == (0, None)
    assert state["balances"]["openai"]["error"] == "OpenAI answered 403 (the costs API needs an admin key)"
    assert "at" not in state["balances"]["openai"]
