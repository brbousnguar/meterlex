"""The Claude Max quota: readings in, pace and token budget out (#35)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import Machine, QuotaReading, UsageTurn
import ingest
import machines
import quota

PARIS = ZoneInfo("Europe/Paris")
RESET = datetime(2026, 10, 10, 9, 0)          # naive UTC, a Saturday
START = RESET - timedelta(days=7)


def _epoch(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _machine(session, name="mini"):
    machines.create(session, name)
    return session.get(Machine, name)


def _reading(ts: datetime, pct: float, reset: datetime = RESET, window: str = "seven_day") -> dict:
    return {"window": window, "used_pct": pct, "resets_at": _epoch(reset), "ts": ts.isoformat()}


def _turn(session, ts: datetime, tokens: int, source="claude-code", key=None):
    session.add(UsageTurn(source=source, session_id="s", turn_key=key or ts.isoformat(), model_id="claude-opus-5",
                          ts=ts, machine="mini", project="/srv/app", input_tokens=tokens, total_tokens=tokens))
    session.commit()


def test_a_reading_is_stored_once_and_bad_ones_are_rejected(session):
    m = _machine(session)
    batch = [_reading(START + timedelta(hours=1), 4.0), {"window": "x y", "used_pct": 1, "resets_at": 0, "ts": "now"},
             {"window": "seven_day", "used_pct": "lots", "resets_at": _epoch(RESET), "ts": START.isoformat()}]
    assert ingest.ingest_quota(session, m, batch) == {"inserted": 1, "unchanged": 0, "rejected": 2}
    assert ingest.ingest_quota(session, m, batch[:1])["unchanged"] == 1
    [row] = session.exec(select(QuotaReading)).all()
    assert (row.window, row.used_pct, row.resets_at, row.machine) == ("seven_day", 4.0, RESET, "mini")


def test_the_window_rolls_a_week_on_once_its_reset_has_passed():
    r = QuotaReading(window="seven_day", used_pct=80, resets_at=RESET, ts=RESET - timedelta(hours=1))
    assert quota.current_window(r, RESET + timedelta(days=8)) == (RESET + timedelta(days=7), RESET + timedelta(days=14), True)
    assert quota.current_window(None, RESET)[2] is False


def test_pace_budget_and_tokens_left(session):
    m = _machine(session)
    now = START + timedelta(days=3, hours=12)          # half the week gone
    _turn(session, START + timedelta(hours=2), 300)
    _turn(session, START + timedelta(days=3), 300)
    _turn(session, START + timedelta(days=3), 999, source="codex")   # not Claude Max
    _turn(session, START - timedelta(hours=1), 999)                  # last week
    ingest.ingest_quota(session, m, [_reading(START + timedelta(days=1), 2.0),
                                     _reading(START + timedelta(days=3, hours=1), 60.0)])
    v = quota.view(session, now, PARIS)
    assert v["anchored"] and v["used_pct"] == 60.0
    assert v["window"]["elapsed_pct"] == 50.0
    assert v["pace"] == {"delta_pts": 10.0, "status": "ahead", "projected_pct": 120.0}
    assert v["tokens"]["used"] == 600
    assert (v["tokens"]["budget"], v["tokens"]["budget_from"]) == (1000, "this week")   # 600 ÷ 60%
    assert v["tokens"]["left"] == {"tokens": 400, "per_day": round(400 / 3.5)}
    assert [d["tokens"] for d in v["days"][:4]] == [300, 0, 0, 300]
    assert len(v["hourly"]) == 168 and v["hourly"][2] == 300 and v["hourly"][72] == 300 and sum(v["hourly"]) == 600
    assert v["days"][3]["current"] and v["days"][4]["future"]
    assert v["days"][3]["end_pct"] == 60.0 and v["days"][0]["end_pct"] is None
    assert v["past"][0]["tokens"] == 999 and v["past"][0]["final_pct"] is None
    assert v["by_model"] == [{"key": "claude-opus-5", "tokens": 600}]


def test_a_new_week_without_a_reading_borrows_the_budget_of_past_weeks(session):
    m = _machine(session)
    prev_start = START - timedelta(days=7)
    _turn(session, prev_start + timedelta(days=1), 500)
    ingest.ingest_quota(session, m, [_reading(prev_start + timedelta(days=2), 50.0, reset=START)])
    v = quota.view(session, START + timedelta(hours=1), PARIS)
    assert v["window"]["reset"] == "2026-10-10T09:00:00Z"
    assert v["reading"] is None and v["used_pct"] == 0.0
    assert v["past"][0]["final_pct"] == 50.0 and v["past"][0]["budget"] == 1000
    assert (v["tokens"]["budget"], v["tokens"]["budget_from"]) == (1000, "the last 1 week")


def test_the_five_hour_window_shows_only_until_it_resets(session):
    m = _machine(session)
    now = START + timedelta(days=1)
    ingest.ingest_quota(session, m, [_reading(now - timedelta(hours=1), 23.5, reset=now + timedelta(hours=2), window="five_hour")])
    assert quota.view(session, now, PARIS)["five_hour"]["used_pct"] == 23.5
    assert quota.view(session, now + timedelta(hours=3), PARIS)["five_hour"] is None


def test_the_api_takes_quota_beside_turns(client, engine):
    with Session(engine) as session:
        key = machines.create(session, "mini")
    r = client.post("/api/ingest", json={"turns": [], "quota": [_reading(START, 1.0)]},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["quota"]["inserted"] == 1
    assert client.post("/api/ingest", json={"turns": [], "quota": "x"},
                       headers={"Authorization": f"Bearer {key}"}).status_code == 400
    body = client.get("/api/quota").json()
    assert body["anchored"] is True and len(body["hours"]) == 7 and len(body["days"]) == 7


def test_a_stale_lower_figure_from_an_idle_session_does_not_pull_the_week_down(session):
    m = _machine(session)
    ingest.ingest_quota(session, m, [_reading(START + timedelta(days=2), 40.0),
                                     _reading(START + timedelta(days=3), 24.0, ),   # an idle session, days late
                                     _reading(START + timedelta(days=3, hours=1), 45.0)])
    v = quota.view(session, START + timedelta(days=3, hours=2), PARIS)
    assert v["used_pct"] == 45.0
    assert [h["pct"] for h in v["history"]] == [40.0, 45.0]
