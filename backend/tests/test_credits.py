"""Metered credits: ElevenLabs readings in, the month, pace and projection out (#73)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import CreditDay, CreditReading, Machine
import credits
import ingest
import machines

PARIS = ZoneInfo("Europe/Paris")
RESET = datetime(2026, 11, 8, 10, 6)          # naive UTC
START = datetime(2026, 10, 8, 10, 6)


def _epoch(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _machine(session, name="mini"):
    machines.create(session, name)
    return session.get(Machine, name)


def _reading(ts: datetime, used: int, limit: int = 100_000, reset: datetime = RESET, days=None) -> dict:
    return {"provider": "elevenlabs", "used": used, "limit": limit, "resets_at": _epoch(reset),
            "ts": ts.isoformat(), "plan": "creator", "days": days or []}


def test_a_reading_is_stored_once_and_its_days_take_the_latest_figure(session):
    m = _machine(session)
    ts = START + timedelta(days=3)
    first = _reading(ts, 900, days=[{"day": "2026-10-09", "used": 400}, {"day": "2026-10-10", "used": 500}])
    assert ingest.ingest_credits(session, m, [first]) == {"inserted": 1, "unchanged": 0, "rejected": 0, "days": 2}
    again = _reading(ts, 900, days=[{"day": "2026-10-10", "used": 650}])
    assert ingest.ingest_credits(session, m, [again])["unchanged"] == 1
    assert session.get(CreditDay, ("elevenlabs", "2026-10-10")).used == 650
    bad = [{**first, "provider": "other"}, {**first, "used": -1}, {**first, "days": [{"day": "10/10", "used": 1}]},
           {k: v for k, v in first.items() if k != "limit"}]
    assert ingest.ingest_credits(session, m, bad)["rejected"] == 4
    assert len(session.exec(select(CreditReading)).all()) == 1


def test_the_month_runs_from_the_reset_before_to_the_reset_ahead():
    assert credits.month_before(datetime(2026, 11, 8, 10, 6)) == datetime(2026, 10, 8, 10, 6)
    assert credits.month_before(datetime(2026, 3, 31)) == datetime(2026, 2, 28)
    assert credits.month_before(datetime(2026, 1, 15)) == datetime(2025, 12, 15)
    assert credits.month_after(datetime(2026, 1, 31)) == datetime(2026, 2, 28)


def test_pace_projection_and_the_day_the_credits_run_out(session):
    m = _machine(session)
    read = START + timedelta(days=10)
    ingest.ingest_credits(session, m, [_reading(read, 60_000, days=[{"day": "2026-10-17", "used": 7_000}])])
    v = credits.view(session, read + timedelta(minutes=5), PARIS)
    assert v["used"] == 60_000 and v["left"] == 40_000 and v["used_pct"] == 60.0 and v["plan_label"] == "Creator"
    assert v["window"]["start"] == "2026-10-08T10:06:00Z" and v["window"]["reset"] == "2026-11-08T10:06:00Z"
    p = v["pace"]
    assert p["status"] == "ahead" and p["per_day"] == 6_000
    assert p["projected_used"] == 186_000
    # 40,000 left at 6,000 a day: six days and sixteen hours after the reading
    assert p["runs_out_at"] == "2026-10-25T02:06:00Z"
    assert v["days"][0]["day"] == "2026-10-08" and v["days"][-1]["day"] == "2026-11-08" and len(v["days"]) == 32
    assert next(d for d in v["days"] if d["day"] == "2026-10-17")["used"] == 7_000
    assert v["recent_per_day"] == 1_000 and v["reading"]["stale"] is False


def test_a_slow_month_lands_under_the_limit_and_a_young_one_makes_no_projection(session):
    m = _machine(session)
    ingest.ingest_credits(session, m, [_reading(START + timedelta(hours=6), 5_000)])
    young = credits.view(session, START + timedelta(hours=6), PARIS)["pace"]
    assert young["projected_used"] is None and young["runs_out_at"] is None
    ingest.ingest_credits(session, m, [_reading(START + timedelta(days=20), 20_000)])
    p = credits.view(session, START + timedelta(days=20), PARIS)["pace"]
    assert p["status"] == "behind" and p["runs_out_at"] is None and p["projected_pct"] == 31.0


def test_a_reading_from_before_the_reset_gives_no_figure_for_the_new_month(session):
    m = _machine(session)
    ingest.ingest_credits(session, m, [_reading(RESET - timedelta(minutes=30), 99_000)])
    v = credits.view(session, RESET + timedelta(minutes=10), PARIS)
    assert v["window"]["reset_passed"] is True and v["window"]["start"] == "2026-11-08T10:06:00Z"
    assert v["used"] is None and v["pace"] is None and v["reading"]["stale"] is False


def test_the_api_takes_credits_beside_turns(client, engine):
    with Session(engine) as session:
        key = machines.create(session, "mini")
    now = datetime.utcnow().replace(microsecond=0)
    body = {"turns": [], "credits": [_reading(now, 1_000, reset=now + timedelta(days=12))]}
    r = client.post("/api/ingest", json=body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["credits"]["inserted"] == 1
    assert client.post("/api/ingest", json={"turns": [], "credits": {}},
                       headers={"Authorization": f"Bearer {key}"}).status_code == 400
    v = client.get("/api/credits").json()
    assert v["label"] == "ElevenLabs" and v["used"] == 1_000 and v["limit"] == 100_000
    assert client.get("/api/credits?provider=nope").status_code == 404


def test_no_reading_yet(client):
    assert client.get("/api/credits").json()["reading"] is None
