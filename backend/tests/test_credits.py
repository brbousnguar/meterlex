"""Metered credits: ElevenLabs readings in, the month, pace and projection out (#73)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import CreditDay, CreditKey, CreditReading, Machine
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


def _reading(ts: datetime, used: int, limit: int = 100_000, reset: datetime = RESET, days=None, keys=None) -> dict:
    r = {"provider": "elevenlabs", "used": used, "limit": limit, "resets_at": _epoch(reset),
         "ts": ts.isoformat(), "plan": "creator", "days": days or []}
    if keys is not None:
        r["keys"] = keys
    return r


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


# the month by API key (#99)
SITE = {"name": "site", "used": 50_000, "cap": 60_000}
APP = {"name": "app", "used": 10_000}
IDLE = {"name": "idle", "used": 0, "cap": 5_000}


def test_a_reading_by_key_replaces_the_keys_before_it_and_a_late_older_one_does_not(session):
    m = _machine(session)
    read = START + timedelta(days=10)
    ingest.ingest_credits(session, m, [_reading(read, 60_000, keys=[SITE, APP])])
    ingest.ingest_credits(session, m, [_reading(read + timedelta(minutes=15), 60_100, keys=[{**SITE, "used": 50_100}])])
    rows = session.exec(select(CreditKey)).all()
    assert [(k.name, k.used, k.cap) for k in rows] == [("site", 50_100, 60_000)]   # "app" went with the newer reading
    ingest.ingest_credits(session, m, [_reading(read + timedelta(minutes=5), 60_050, keys=[APP])])
    assert [k.name for k in session.exec(select(CreditKey)).all()] == ["site"]
    ingest.ingest_credits(session, m, [_reading(read + timedelta(minutes=30), 60_200)])  # an old collector: keys stay
    assert len(session.exec(select(CreditKey)).all()) == 1
    bad = [_reading(read + timedelta(hours=1), 1, keys=k) for k in
           ({"site": 1}, [{"name": "", "used": 1}], [{"name": "a\nb", "used": 1}], [{"name": "x", "used": -1}],
            [{"name": "x", "used": 1}, {"name": "x", "used": 2}], [{"name": "x", "used": 1, "cap": "lots"}])]
    assert ingest.ingest_credits(session, m, bad)["rejected"] == 6


def test_each_key_is_laid_against_its_own_cap(session):
    m = _machine(session)
    read = START + timedelta(days=10)
    ingest.ingest_credits(session, m, [_reading(read, 60_000, keys=[SITE, APP, IDLE])])
    v = credits.view(session, read + timedelta(minutes=5), PARIS)
    assert [k["name"] for k in v["keys"]] == ["site", "app", "idle"]
    site, app, idle = v["keys"]
    assert site["used_pct"] == 83.3 and site["share_pct"] == 83.3 and site["status"] == "ahead"
    # 5,000 a day: 155,000 by the reset, and the cap's last 10,000 gone two days after the reading
    assert site["projected_used"] == 155_000 and site["projected_pct"] == 258.3
    assert site["runs_out_at"] == "2026-10-20T10:06:00Z"
    assert app["cap"] is None and app["used_pct"] is None and app["status"] is None
    assert app["projected_used"] == 31_000 and app["runs_out_at"] is None
    assert idle["used_pct"] == 0.0 and idle["status"] == "behind" and idle["runs_out_at"] is None


def test_a_key_at_its_cap_is_used_up_and_keys_end_with_their_month(session):
    m = _machine(session)
    read = START + timedelta(days=10)
    ingest.ingest_credits(session, m, [_reading(read, 60_000, keys=[{**SITE, "used": 60_000}])])
    assert credits.view(session, read, PARIS)["keys"][0]["runs_out_at"] == "2026-10-18T10:06:00Z"
    assert credits.view(session, RESET + timedelta(minutes=10), PARIS)["keys"] == []
