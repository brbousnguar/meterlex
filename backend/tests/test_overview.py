"""The overview endpoint and the local-time period bounds behind it."""
from datetime import datetime

from sqlmodel import Session

from models import Machine, Setting, UsageTurn
import main


def _turn(**kw):
    base = dict(
        source="claude-code", session_id="s1", turn_key="t1",
        model_id="claude-opus-5", ts=datetime(2026, 9, 15, 10),
        machine="brahim-mini", input_tokens=10, output_tokens=5,
        cache_read=100, cache_write=20, reasoning_tokens=0,
        total_tokens=135, cost_eur=1.5, cost_usd=1.63,
    )
    base.update(kw)
    return UsageTurn(**base)


def _seed(engine):
    with Session(engine) as session:
        session.add(Setting(key="sub_claude_code_eur", value="21.60"))
        # Tuesday of the week under test, and the Monday of the week before.
        session.add(_turn(turn_key="t1", project="server"))
        session.add(_turn(turn_key="t2", ts=datetime(2026, 9, 15, 22), machine="sqli-5cd6030lcj",
                          source="codex", model_id="gpt-5.4-codex", total_tokens=300, cost_eur=0.5))
        session.add(_turn(turn_key="t3", ts=datetime(2026, 9, 8, 9), total_tokens=1000, cost_eur=4.0))
        session.add(Machine(name="brahim-mini", key_hash="x", last_seen_at=datetime(2026, 9, 15, 15)))
        session.commit()


def test_week_starts_monday_in_local_time(client, engine):
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    assert body["from_local"].startswith("2026-09-14T00:00")   # Monday, Paris
    assert body["to_local"].startswith("2026-09-21T00:00")
    assert body["tz"] == "Europe/Paris"
    # Stored UTC is two hours behind Paris in September.
    assert body["from"].startswith("2026-09-13T22:00")


def test_overview_totals_split_tokens_and_compare_with_last_week(client, engine):
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    totals = body["totals"]
    assert totals["turns"] == 2
    assert totals["tokens"] == 435
    assert totals["cache_read"] == 200
    assert totals["machines"] == 2
    assert body["previous"]["tokens"] == 1000
    # A week's share of the monthly fee, pro-rated by days.
    assert 4.5 < totals["sub_eur"] < 5.5


def test_overview_lists_every_harness_machine_and_fills_empty_days(client, engine):
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    assert len(body["by_source"]) == 6                          # silent harnesses still carry a fee
    machines = {m["machine"]: m for m in body["by_machine"]}
    assert machines["brahim-mini"]["tokens"] == 135
    assert machines["brahim-mini"]["last_seen_at"].startswith("2026-09-15")
    assert [b["bucket"] for b in body["series"]][:3] == ["2026-09-14", "2026-09-15", "2026-09-16"]
    assert len(body["series"]) == 7
    monday = next(b for b in body["series"] if b["bucket"] == "2026-09-14")
    assert monday["tokens"] == 0                                # a quiet day stays in the chart
    tuesday = next(b for b in body["series"] if b["bucket"] == "2026-09-15")
    assert tuesday["by_machine"] == {"brahim-mini": 135}   # the 22:00 UTC turn is Wednesday here


def test_a_late_evening_turn_counts_on_its_local_day(client, engine):
    """22:00 UTC on the 15th is 00:00 Paris on the 16th."""
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    wednesday = next(b for b in body["series"] if b["bucket"] == "2026-09-16")
    assert wednesday["tokens"] == 300


def test_models_carry_the_harness_that_ran_them(client, engine):
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    models = {m["model_id"]: m["source"] for m in body["by_model"]}
    assert models["gpt-5.4-codex"] == "codex"


def test_each_machine_carries_its_folders_and_harnesses(client, engine):
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    machines = {m["machine"]: m for m in body["by_machine"]}
    assert machines["brahim-mini"]["projects"] == [
        {"project": "server", "tokens": 135, "turns": 1, "cost_eur": 1.5}]
    assert machines["brahim-mini"]["sources"] == [
        {"source": "claude-code", "tokens": 135, "turns": 1, "cost_eur": 1.5}]
    assert machines["sqli-5cd6030lcj"]["sources"] == [
        {"source": "codex", "tokens": 300, "turns": 1, "cost_eur": 0.5}]


def test_series_buckets_carry_money_as_well_as_tokens(client, engine):
    """The daily bars switch between tokens and euros without a second call."""
    _seed(engine)
    body = client.get("/api/overview", params={"period": "weekly", "ref": "2026-09-16"}).json()
    tuesday = next(b for b in body["series"] if b["bucket"] == "2026-09-15")
    assert tuesday["by_machine"] == {"brahim-mini": 135}
    assert tuesday["cost_by_machine"] == {"brahim-mini": 1.5}
    assert tuesday["cost_by_source"] == {"claude-code": 1.5}


def test_overview_rejects_an_unknown_period(client):
    assert client.get("/api/overview", params={"period": "fortnightly"}).status_code == 400


def test_previous_bounds_walk_back_a_month_and_a_year():
    start, _ = main._period_bounds("monthly", "2026-01")
    prev_start, prev_end = main._previous_bounds("monthly", start)
    assert main._from_utc(prev_start).strftime("%Y-%m") == "2025-12"
    assert prev_end == start
    start, _ = main._period_bounds("yearly", "2026")
    prev_start, _ = main._previous_bounds("yearly", start)
    assert main._from_utc(prev_start).year == 2025
