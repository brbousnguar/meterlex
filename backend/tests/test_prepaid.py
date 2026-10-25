"""Prepaid accounts: OpenRouter and OpenAI readings in, the balance, spend by day and how long it lasts out (#77)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import select

from models import BalanceReading, CreditDay, Machine
import ingest
import machines
import prepaid

PARIS = ZoneInfo("Europe/Paris")
T0 = datetime(2026, 10, 20, 9, 0)            # naive UTC
M = 1_000_000


def _machine(session, name="mini"):
    machines.create(session, name)
    return session.get(Machine, name)


def _or(ts: datetime, used: float, bought: float = 90.0) -> dict:
    return {"provider": "openrouter", "bought": round(bought * M), "used": round(used * M), "ts": ts.isoformat()}


def test_openrouter_spend_goes_to_the_day_of_the_reading(session):
    m = _machine(session)
    assert ingest.ingest_balances(session, m, [_or(T0, 80.0)])["inserted"] == 1
    assert session.get(CreditDay, ("openrouter", "2026-10-20")) is None          # nothing to compare the first with
    ingest.ingest_balances(session, m, [_or(T0 + timedelta(hours=2), 81.5), _or(T0 + timedelta(hours=20), 83.0)])
    assert session.get(CreditDay, ("openrouter", "2026-10-20")).used == 1_500_000
    assert session.get(CreditDay, ("openrouter", "2026-10-21")).used == 1_500_000
    again = ingest.ingest_balances(session, m, [_or(T0 + timedelta(hours=20), 83.0)])
    assert again["unchanged"] == 1
    late = ingest.ingest_balances(session, m, [_or(T0 + timedelta(hours=10), 82.0)])   # older than the latest
    assert late["inserted"] == 1 and session.get(CreditDay, ("openrouter", "2026-10-20")).used == 1_500_000
    bad = [{**_or(T0, 1), "provider": "anthropic"}, {**_or(T0, 1), "used": -5}, {"provider": "openai", "ts": "x"},
           {"provider": "openai", "ts": T0.isoformat(), "days": [{"day": "20/10", "spent": 1}]}]
    assert ingest.ingest_balances(session, m, bad)["rejected"] == 4


def test_openrouter_balance_rate_and_the_day_it_runs_out(session):
    m = _machine(session)
    ingest.ingest_balances(session, m, [_or(T0 + timedelta(days=i), 80.0 + i) for i in range(5)])  # $1 a day
    now = T0 + timedelta(days=4, minutes=5)
    v = prepaid.view(session, now, PARIS, "openrouter")
    assert v["kind"] == "prepaid" and v["label"] == "OpenRouter"
    assert v["bought"] == 90.0 and v["used"] == 84.0 and v["left"] == 6.0
    assert v["per_day"] == 1.0 and v["lasts_days"] == 6.0
    assert v["runs_out_at"].startswith("2026-10-30")
    assert v["spent"]["today"] == 1.0 and v["spent"]["week"] == 4.0 and v["spent"]["since"] == "2026-10-20"
    assert v["spent"]["week_complete"] is False                          # the record starts on the 20th
    assert len(v["days"]) == 30 and v["days"][-1] == {"day": "2026-10-24", "spent": 1.0, "current": True, "known": True}
    assert v["days"][-6]["known"] is False
    assert v["reading"]["stale"] is False
    assert prepaid.view(session, now + timedelta(hours=2), PARIS, "openrouter")["reading"]["stale"] is True


def test_no_rate_from_a_few_hours(session):
    m = _machine(session)
    ingest.ingest_balances(session, m, [_or(T0, 80.0), _or(T0 + timedelta(hours=2), 81.0)])
    v = prepaid.view(session, T0 + timedelta(hours=2), PARIS, "openrouter")
    assert v["left"] == 9.0 and v["per_day"] is None and v["lasts_days"] is None


def _oa(ts: datetime, days: dict) -> dict:
    return {"provider": "openai", "ts": ts.isoformat(),
            "days": [{"day": d, "spent": round(usd * M)} for d, usd in days.items()]}


def test_openai_left_is_the_saved_balance_less_what_was_spent_since(session):
    m = _machine(session)
    days = {f"2026-10-{d:02d}": 0.5 for d in range(5, 21)}     # 16 days at $0.50
    ingest.ingest_balances(session, m, [_oa(T0, days)])
    v = prepaid.view(session, T0, PARIS, "openai")
    assert v["left"] is None and v["balance_set"] is None and v["per_day"] == 0.5
    assert v["spent"]["month"] == 8.0 and v["spent"]["since"] == "2026-10-05"

    prepaid.save_anchor(session, 20.0, T0)                       # the 20th's $0.50 is already out of the $20
    days.update({"2026-10-20": 1.25, "2026-10-21": 2.0})
    ingest.ingest_balances(session, m, [_oa(T0 + timedelta(days=1), days)])
    v = prepaid.view(session, T0 + timedelta(days=1), PARIS, "openai")
    assert v["balance_set"] == {"usd": 20.0, "at": "2026-10-20T09:00:00Z", "topped_up": None}
    assert v["spent_since_balance"] == 2.75 and v["left"] == 17.25
    assert session.get(CreditDay, ("openai", "2026-10-20")).used == 1_250_000      # a later read of a day wins

    assert v["topped_up"] is None
    prepaid.save_anchor(session, 20.0, T0, date(2026, 10, 15))   # again, now with the last top-up's day
    v = prepaid.view(session, T0 + timedelta(days=1), PARIS, "openai")
    assert v["balance_set"]["topped_up"] == "2026-10-15" and v["left"] == 18.0   # the 20th's $1.25 is out of it now
    # 15th–19th at $0.50, the 20th's $1.25 and the 21st's $2: $5.75 spent since, so $23.75 held after it
    assert v["topped_up"] == {"day": "2026-10-15", "held": 23.75, "spent": 5.75}
    prepaid.save_anchor(session, 20.0, T0, date(2026, 9, 1))    # before the costs on record: no whole
    assert prepaid.view(session, T0, PARIS, "openai")["topped_up"] is None

    prepaid.save_anchor(session, None, T0)
    assert prepaid.view(session, T0, PARIS, "openai")["balance_set"] is None


def test_the_api_takes_both_providers_and_the_openai_balance(client):
    assert client.get("/api/credits?provider=openrouter").json()["reading"] is None
    assert client.get("/api/credits?provider=elevenlabs").json()["provider"] == "elevenlabs"
    assert client.get("/api/credits?provider=nope").status_code == 404
    r = client.put("/api/credits/openai/balance", json={"usd": "12.5"})
    assert r.status_code == 200 and r.json()["balance_set"]["usd"] == 12.5
    assert client.put("/api/credits/openai/balance", json={"usd": "lots"}).status_code == 400
    assert client.put("/api/credits/openai/balance", json={"usd": -1}).status_code == 400
    r = client.put("/api/credits/openai/balance", json={"usd": 5, "topped_up": "2026-10-07"})
    assert r.status_code == 200 and r.json()["balance_set"]["topped_up"] == "2026-10-07"
    assert client.put("/api/credits/openai/balance", json={"usd": 5, "topped_up": "7 Oct"}).status_code == 400
    assert client.put("/api/credits/openai/balance", json={"usd": 5, "topped_up": "2999-01-01"}).status_code == 400
    assert client.put("/api/credits/openai/balance", json={"usd": None}).json()["balance_set"] is None
    assert client.get("/api/settings").status_code == 200                  # the balance keys stay out of settings


def test_ingest_takes_balances(client, engine):
    from sqlmodel import Session
    with Session(engine) as s:
        key = machines.create(s, "mini")
    body = {"collector": "0.9.0", "turns": [], "balances": [_or(T0, 80.0)]}
    r = client.post("/api/ingest", json=body, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["balances"]["inserted"] == 1
    assert client.post("/api/ingest", json={"turns": [], "balances": {}},
                       headers={"Authorization": f"Bearer {key}"}).status_code == 400
    with Session(engine) as s:
        assert len(s.exec(select(BalanceReading)).all()) == 1
