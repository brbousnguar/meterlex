"""The hub's side of ingestion: batches from machines' collectors (#4)."""
from datetime import datetime, timedelta

import pytest
from sqlmodel import select

from models import Machine, ModelPrice, UsageTurn
import ingest
import machines


def _machine(session, name="laptop", labels="full"):
    machines.create(session, name, labels)
    return session.get(Machine, name)


def _turn(**over):
    t = {
        "source": "claude-code", "session_id": "s1", "turn_key": "msg_1", "project": "/work/app",
        "model_id": "claude-sonnet-5", "ts": "2026-09-15T10:00:00", "input_tokens": 10, "output_tokens": 5,
        "cache_read": 100, "cache_write": 20, "reasoning_tokens": 0, "origin": "interactive",
    }
    t.update(over)
    return t


def _rows(session):
    return session.exec(select(UsageTurn)).all()


@pytest.mark.parametrize("source, model, expected", [
    ("claude-code", "claude-opus-5", "claude-code"),
    ("claude-code", "kimi-k2.6", "ollama"),          # Claude Code through Ollama
    ("claude-code", "qwen3.6:35b-mlx", "ollama"),
    ("claude-code", "<synthetic>", "claude-code"),   # Claude Code's own placeholder
    ("codex", "gpt-5.6-sol", "codex"),
])
def test_which_tool_a_turn_counts_toward(source, model, expected):
    assert ingest.classify_source(source, model) == expected


def test_a_reply_sent_twice_is_stored_once_at_its_largest(session):
    m = _machine(session)
    ingest.ingest_turns(session, m, [_turn(output_tokens=0)])
    result = ingest.ingest_turns(session, m, [_turn(output_tokens=42)])
    [row] = _rows(session)
    assert (row.output_tokens, row.total_tokens, result["updated"]) == (42, 10 + 42 + 100 + 20, 1)
    assert ingest.ingest_turns(session, m, [_turn(output_tokens=42)])["unchanged"] == 1


def test_old_per_line_rows_fold_into_their_reply(session):
    for uuid in ("u1", "u2", "u3"):  # the old scanner stored one row per logged part
        session.add(UsageTurn(source="claude-code", session_id="s1", turn_key=uuid, model_id="claude-sonnet-5",
                              ts=datetime(2026, 9, 15, 10), input_tokens=10, output_tokens=5, cache_read=100,
                              cache_write=20, total_tokens=135))
    session.commit()
    m = _machine(session)
    result = ingest.ingest_turns(session, m, [_turn(alt_keys=["u1", "u2", "u3"], output_tokens=7)])
    [row] = _rows(session)
    assert (row.turn_key, row.output_tokens, row.machine, result["folded"]) == ("msg_1", 7, "laptop", 2)


def test_snapshot_rows_take_the_latest_totals(session):
    m = _machine(session)
    snap = _turn(source="copilot", turn_key="session:claude-sonnet-5", snapshot=True)
    ingest.ingest_turns(session, m, [snap])
    ingest.ingest_turns(session, m, [dict(snap, output_tokens=3)])
    [row] = _rows(session)
    assert row.output_tokens == 3


def test_a_basename_machine_never_stores_a_path(session):
    m = _machine(session, "work", "basename")
    ingest.ingest_turns(session, m, [_turn(project="C:\\Users\\me\\RocheBB\\webapps\\rbb-em-dashboard")])
    assert _rows(session)[0].project == "rbb-em-dashboard"


def test_ollama_cloud_models_use_ollama_rates_and_local_ones_are_free(session):
    session.add(ModelPrice(model_id="ollama/kimi-k2.6:cloud", prompt=0.95e-6, completion=4e-6,
                           cache_read=0.16e-6, cache_write=0.95e-6, source="ollama"))
    session.commit()
    m = _machine(session)
    million = dict(input_tokens=1_000_000, output_tokens=0, cache_read=0, cache_write=0)
    ingest.ingest_turns(session, m, [
        _turn(turn_key="a", model_id="kimi-k2.6", **million),
        _turn(turn_key="b", model_id="qwen3.6:35b-mlx", **million),
    ])
    rows = {r.turn_key: r for r in _rows(session)}
    assert (rows["a"].source, rows["a"].price_source) == ("ollama", "ollama")
    assert rows["a"].cost_usd == pytest.approx(0.95)
    assert (rows["b"].source, rows["b"].cost_usd) == ("ollama", 0)


def test_a_bad_turn_is_rejected_not_fatal(session):
    m = _machine(session)
    result = ingest.ingest_turns(session, m, [{"source": "nope"}, {"source": "codex"}, _turn()])
    assert (result["rejected"], result["inserted"]) == (2, 1)
    assert session.get(Machine, "laptop").turns_received == 1


def test_times_with_a_zone_are_stored_in_utc(session):
    m = _machine(session)
    ingest.ingest_turns(session, m, [_turn(ts="2026-09-15T12:00:00+02:00")])
    assert _rows(session)[0].ts == datetime(2026, 9, 15, 10, 0)


def test_stored_non_claude_turns_move_to_ollama(session):
    for key, model in (("a", "kimi-k2.6"), ("b", "claude-opus-5"), ("c", "<synthetic>")):
        session.add(UsageTurn(source="claude-code", session_id="s", turn_key=key, model_id=model,
                              ts=datetime(2026, 9, 1)))
    session.commit()
    assert ingest.reclassify_existing(session) == 1
    assert {r.turn_key: r.source for r in _rows(session)} == {"a": "ollama", "b": "claude-code", "c": "claude-code"}


def test_legacy_rows_of_one_reply_fold_and_other_replies_stay(session):
    t0 = datetime(2026, 8, 1, 10)
    uuids = [f"0000000{i}-aaaa-bbbb-cccc-dddddddddddd" for i in range(4)]
    for i, out in enumerate((0, 0, 30)):  # one reply, logged in three parts
        session.add(UsageTurn(source="claude-code", session_id="s", turn_key=uuids[i], model_id="claude-opus-5",
                              ts=t0 + timedelta(seconds=i), input_tokens=3, output_tokens=out,
                              cache_read=5000, cache_write=200, total_tokens=5203 + out))
    session.add(UsageTurn(source="claude-code", session_id="s", turn_key=uuids[3], model_id="claude-opus-5",
                          ts=t0 + timedelta(seconds=20), input_tokens=3, output_tokens=12,
                          cache_read=5230, cache_write=40, total_tokens=5285))  # the next request
    session.commit()
    report = ingest.dedupe_legacy(session)
    assert (report["replies"], report["rows_removed"], len(_rows(session))) == (1, 2, 4)  # report only
    ingest.dedupe_legacy(session, apply=True)
    rows = sorted(_rows(session), key=lambda r: r.ts)
    assert [r.output_tokens for r in rows] == [30, 12]
