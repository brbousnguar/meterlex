"""The Claude Max quota: readings in, pace and token budget out (#35)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import Machine, QuotaReading, Setting, UsageTurn
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
    assert quota.view(session, now, PARIS)["five_hour"]["open"]["used_pct"] == 23.5
    later = quota.view(session, now + timedelta(hours=3), PARIS)["five_hour"]
    assert later["open"] is None and later["windows"][0]["final_pct"] == 23.5


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


# ── a plan change: same reset, a rescaled percentage (#46) ────────────────────

def _switch_week(session):
    """73% on Max 5x, then Max 20x: 5% when first read, a tick to 6%, then 10%."""
    m = _machine(session)
    prev_start = START - timedelta(days=7)
    _turn(session, prev_start + timedelta(days=1), 500)
    _turn(session, START + timedelta(hours=1), 730)
    _turn(session, START + timedelta(days=4), 270)
    _turn(session, START + timedelta(days=4, hours=4), 40)
    _turn(session, START + timedelta(days=4, hours=5), 40)
    five, twenty = "default_claude_max_5x", "default_claude_max_20x"
    ingest.ingest_quota(session, m, [
        dict(_reading(prev_start + timedelta(days=2), 100.0, reset=START), plan=five),
        dict(_reading(START + timedelta(days=3), 73.0), plan=five),
        dict(_reading(START + timedelta(days=4, hours=1), 5.0), plan=twenty, fresh=True),
        _reading(START + timedelta(days=4, hours=2), 70.0),               # an idle session, old figure
        dict(_reading(START + timedelta(days=4, hours=3), 6.0), plan=twenty, fresh=True),
        dict(_reading(START + timedelta(days=4, hours=6), 10.0), plan=twenty, fresh=True),
        dict(_reading(START + timedelta(days=4, hours=7), 8.0), plan=twenty, fresh=True),   # fallen behind
        dict(_reading(START + timedelta(days=3, hours=2), 100.0, reset=START + timedelta(days=3, hours=4),
                      window="five_hour"), plan=five),      # the 5-hour cap, after the last weekly 5x reading
    ])
    return five, twenty


def test_a_fresh_lower_figure_on_a_new_plan_sets_the_week_and_its_budget(session):
    _, twenty = _switch_week(session)
    v = quota.view(session, START + timedelta(days=5), PARIS)
    assert v["used_pct"] == 10.0 and v["plan"] == twenty and v["plan_label"] == "Max 20x"
    assert [h["pct"] for h in v["history"]] == [73.0, 5.0, 6.0, 10.0]
    assert v["plan_change"]["from_label"] == "Max 5x" and v["plan_change"]["to_label"] == "Max 20x"
    assert (v["plan_change"]["last_at"], v["plan_change"]["at"]) == ("2026-10-06T11:00:00Z", "2026-10-07T10:00:00Z")
    # 80 tokens from the tick at 6% to 10%, not the week's 1080 ÷ 10%
    assert (v["tokens"]["budget"], v["tokens"]["budget_from"]) == (2000, "the 4 points since the switch")
    assert v["tokens"]["left"] == {"tokens": 1800, "per_day": round(1800 / 2)}
    assert v["tokens"]["estimate"] == [
        {"from": "2026-10-03T09:00:00Z", "to": "2026-10-06T11:00:00Z", "pct": 0.0, "tokens": 0,
         "budget": 1000, "plan_label": "Max 5x"},
        {"from": "2026-10-07T12:00:00Z", "to": None, "pct": 6.0, "tokens": 1000,
         "budget": 2000, "plan_label": "Max 20x"},
    ]
    assert v["past"][0]["final_pct"] == 100.0 and v["past"][0]["plan_label"] == "Max 5x"
    assert v["past"][0]["same_plan"] is False and v["avg_final_pct"] is None


def test_a_week_with_a_switch_lends_the_new_plans_budget(session):
    _switch_week(session)
    v = quota.view(session, RESET + timedelta(hours=1), PARIS)
    assert v["past"][0]["final_pct"] == 10.0 and v["past"][0]["budget"] == 2000 and v["past"][0]["same_plan"]
    assert (v["tokens"]["budget"], v["tokens"]["budget_from"]) == (2000, "the last 1 week")
    assert v["tokens"]["estimate"] == [] and v["tokens"]["left"]["tokens"] == 2000


def test_a_switch_lends_no_budget_until_three_points_after_its_first_tick(session):
    m = _machine(session)
    _turn(session, START + timedelta(hours=1), 730)
    ingest.ingest_quota(session, m, [
        dict(_reading(START + timedelta(days=3), 73.0), plan="default_claude_max_5x"),
        dict(_reading(START + timedelta(days=4), 7.0), plan="default_claude_max_20x", fresh=True),
        dict(_reading(START + timedelta(days=4, hours=1), 9.0), plan="default_claude_max_20x", fresh=True),
    ])
    v = quota.view(session, START + timedelta(days=5), PARIS)
    assert v["tokens"]["budget"] is None and v["tokens"]["left"] is None
    assert [e["plan_label"] for e in v["tokens"]["estimate"]] == ["Max 5x"]


def test_past_weeks_on_another_plan_lend_no_budget(session):
    m = _machine(session)
    prev_start = START - timedelta(days=7)
    _turn(session, prev_start + timedelta(days=1), 500)
    ingest.ingest_quota(session, m, [
        dict(_reading(prev_start + timedelta(days=2), 50.0, reset=START), plan="default_claude_max_5x"),
        dict(_reading(START + timedelta(hours=1), 1.0), plan="default_claude_max_20x", fresh=True),
    ])
    v = quota.view(session, START + timedelta(hours=2), PARIS)
    assert v["tokens"]["budget"] is None and v["plan_change"] is None


def test_readings_keep_their_plan_and_freshness_and_old_ones_can_be_named(session):
    m = _machine(session)
    batch = [dict(_reading(START, 2.0), plan="default_claude_max_20x", fresh=True),
             _reading(START - timedelta(days=1), 90.0, reset=START),
             dict(_reading(START + timedelta(hours=1), 3.0), plan="max 20x; drop table")]
    assert ingest.ingest_quota(session, m, batch) == {"inserted": 2, "unchanged": 0, "rejected": 1}
    rows = {r.used_pct: r for r in session.exec(select(QuotaReading)).all()}
    assert (rows[2.0].plan, rows[2.0].fresh) == ("default_claude_max_20x", True)
    assert (rows[90.0].plan, rows[90.0].fresh) == (None, False)
    assert quota.stamp_plan(session, "default_claude_max_5x", START) == 1          # a dry run
    assert session.get(QuotaReading, rows[90.0].id).plan is None
    assert quota.stamp_plan(session, "default_claude_max_5x", START, apply=True) == 1
    assert session.get(QuotaReading, rows[90.0].id).plan == "default_claude_max_5x"
    assert quota.plan_label("default_claude_max_5x") == "Max 5x" and quota.plan_label(None) is None


# ── the marks of a switch: the projection, past weeks, the next plan (#54) ────

def test_after_a_switch_the_projection_runs_at_the_new_plans_rate(session):
    _switch_week(session)
    # less than a day on Max 20x since its first tick (6% at day 4 + 3h): nothing to say yet
    assert quota.view(session, START + timedelta(days=5), PARIS)["pace"]["projected_pct"] is None
    # 4 points in the 25 hours since the tick, 44 hours to the reset: 10 + 4 × 44 / 25
    v = quota.view(session, START + timedelta(days=5, hours=4), PARIS)
    assert v["pace"]["projected_pct"] == 17.0
    assert v["pace"]["status"] == "behind"          # 10% with 74% of the week gone, still


def test_a_past_week_with_a_switch_names_both_plans(session):
    _switch_week(session)
    v = quota.view(session, RESET + timedelta(hours=1), PARIS)
    assert (v["past"][0]["plan_label"], v["past"][0]["switched_from_label"]) == ("Max 20x", "Max 5x")
    assert v["past"][1]["switched_from_label"] is None


def test_the_next_plan_shows_until_a_reading_names_it(session):
    m = _machine(session)
    session.add(Setting(key="plan_next", value="default_claude_max_5x"))
    session.add(Setting(key="plan_next_on", value="2026-10-08"))
    session.commit()
    ingest.ingest_quota(session, m, [dict(_reading(START + timedelta(hours=1), 1.0), plan="default_claude_max_20x", fresh=True)])
    v = quota.view(session, START + timedelta(hours=2), PARIS)
    assert v["plan_next"] == {"plan": "default_claude_max_5x", "plan_label": "Max 5x", "on": "2026-10-08",
                              "at": "2026-10-07T22:00:00Z"}       # the day starts at Paris midnight
    ingest.ingest_quota(session, m, [dict(_reading(START + timedelta(days=5), 30.0), plan="default_claude_max_5x", fresh=True)])
    assert quota.view(session, START + timedelta(days=5, hours=1), PARIS)["plan_next"] is None


def test_the_next_plan_round_trips_through_the_settings_api(client):
    r = client.patch("/api/settings", json={"plan_next": "default_claude_max_5x", "plan_next_on": "2026-11-14"})
    assert r.status_code == 200
    assert (r.json()["plan_next"], r.json()["plan_next_on"]) == ("default_claude_max_5x", "2026-11-14")
    assert client.patch("/api/settings", json={"plan_next_on": "14/11/2026"}).status_code == 400
    assert client.patch("/api/settings", json={"plan_next": "max 5x; drop table"}).status_code == 400
    r = client.patch("/api/settings", json={"plan_next": "", "plan_next_on": ""})
    assert (r.json()["plan_next"], r.json()["plan_next_on"]) == ("", "")


# ── browsing past weeks (#57) ───────────────────────────────────────────────

def test_a_past_window_is_read_to_its_own_end(session):
    _switch_week(session)
    v = quota.view(session, RESET + timedelta(days=2), PARIS, back=1)
    w = v["window"]
    assert (w["back"], w["start"], w["reset"], w["end"]) == (1, quota._iso(START), quota._iso(RESET), quota._iso(RESET))
    assert (w["elapsed_pct"], w["days_left"]) == (100.0, 0.0)
    assert (v["used_pct"], v["plan_label"], v["plan_change"]["from_label"]) == (10.0, "Max 20x", "Max 5x")
    assert v["pace"] is None and v["five_hour"] is None and v["plan_next"] is None
    assert v["tokens"]["used"] == 1080 and v["by_model"] == [{"key": "claude-opus-5", "tokens": 1080}]
    assert (v["tokens"]["budget"], v["tokens"]["budget_from"]) == (2000, "the 4 points since the switch")
    assert v["tokens"]["left"] == {"tokens": 1800, "per_day": None}      # what the reset found unused
    assert not any(d["future"] or d["current"] for d in v["days"]) and v["days"][4]["end_pct"] == 10.0
    assert sum(v["hourly"]) == 1080
    # the list of past weeks stays the live window's; the week in view is in it by its index
    assert [p["back"] for p in v["past"]] == list(range(1, 9)) and v["past"][0]["start"] == w["start"]
    # a week before the readings began names no plan, rather than the plan of today
    before = quota.view(session, RESET + timedelta(days=2), PARIS, back=3)
    assert (before["plan"], before["plan_label"], before["used_pct"], before["tokens"]["budget"]) == (None, None, None, None)


def test_the_live_window_ends_now_and_the_index_is_clamped(session):
    m = _machine(session)
    ingest.ingest_quota(session, m, [_reading(START + timedelta(days=1), 2.0)])
    now = START + timedelta(days=2)
    v = quota.view(session, now, PARIS)
    assert (v["window"]["back"], v["window"]["end"]) == (0, quota._iso(now))
    assert quota.view(session, now, PARIS, back=99)["window"]["back"] == quota.PAST_WINDOWS


def test_the_api_opens_a_past_week_within_the_list(client, engine):
    with Session(engine) as session:
        ingest.ingest_quota(session, _machine(session), [_reading(START + timedelta(days=1), 40.0)])
    assert client.get("/api/quota?back=1").json()["window"]["back"] == 1
    assert client.get("/api/quota?back=9").status_code == 422
    assert client.get("/api/quota?back=-1").status_code == 422


# ── the 5-hour window (#59) ─────────────────────────────────────────────────

FIVE_RESET = START + timedelta(days=1, hours=5)      # the open window: began a day in, resets five hours later
FIVE_START = FIVE_RESET - timedelta(hours=5)


def _five_hour_day(session):
    """A window that ended at 100% an hour before the open one began, then the
    open one: 10% after half an hour, 45% at 2h30 (a stale 30% after it is a
    session that fell behind)."""
    m = _machine(session)
    prev_reset = FIVE_START - timedelta(hours=1)
    _turn(session, prev_reset - timedelta(hours=4), 900)
    _turn(session, FIVE_START + timedelta(hours=1), 300)
    _turn(session, FIVE_START + timedelta(hours=2), 200)
    ingest.ingest_quota(session, m, [
        _reading(prev_reset - timedelta(hours=4, minutes=30), 40.0, reset=prev_reset, window="five_hour"),
        _reading(prev_reset - timedelta(hours=2), 100.0, reset=prev_reset, window="five_hour"),
        _reading(FIVE_START + timedelta(minutes=30), 10.0, reset=FIVE_RESET, window="five_hour"),
        _reading(FIVE_START + timedelta(hours=2, minutes=30), 45.0, reset=FIVE_RESET, window="five_hour"),
        _reading(FIVE_START + timedelta(hours=2, minutes=40), 30.0, reset=FIVE_RESET, window="five_hour"),
        _reading(START + timedelta(days=1), 20.0),            # the week's own reading
    ])


def test_the_open_five_hour_window_is_read_with_its_pace_tokens_and_the_windows_before(session):
    _five_hour_day(session)
    now = FIVE_START + timedelta(hours=3)                   # 60% of the window gone
    f = quota.view(session, now, PARIS)["five_hour"]
    o = f["open"]
    assert (o["used_pct"], o["start"], o["reset"], o["elapsed_pct"], o["minutes_left"]) \
        == (45.0, quota._iso(FIVE_START), quota._iso(FIVE_RESET), 60.0, 120)
    assert o["at"] == quota._iso(FIVE_START + timedelta(hours=2, minutes=30)) and o["capped_at"] is None
    # 45 points in 3 of 5 hours: 15 behind an even pace, 75% by the reset, never 100%
    assert o["pace"] == {"delta_pts": -15.0, "status": "behind", "projected_pct": 75.0, "runs_out_at": None}
    assert [h["pct"] for h in o["history"]] == [10.0, 45.0]
    # 500 tokens by the last reading ÷ 45 points; what the figure leaves, nothing spent since
    assert o["tokens"] == {"used": 500, "budget": 1111, "left": 611}
    assert len(o["slots"]) == 30 and o["slots"][6] == 300 and o["slots"][12] == 200 and sum(o["slots"]) == 500
    # the windows, newest first: the open one, then the one that hit 100%
    assert [(w["final_pct"], w["current"], w["tokens"]) for w in f["windows"]] == [(45.0, True, 500), (100.0, False, 900)]
    assert f["windows"][1]["capped_at"] == quota._iso(FIVE_START - timedelta(hours=3))


def test_a_five_hour_window_ahead_of_pace_says_when_it_runs_out(session):
    m = _machine(session)
    ingest.ingest_quota(session, m, [_reading(FIVE_START + timedelta(hours=2), 80.0, reset=FIVE_RESET, window="five_hour")])
    now = FIVE_START + timedelta(hours=2, minutes=30)          # 80% in half the window
    o = quota.view(session, now, PARIS)["five_hour"]["open"]
    assert o["pace"]["status"] == "ahead" and o["pace"]["projected_pct"] == 160.0
    assert o["pace"]["runs_out_at"] == quota._iso(FIVE_START + timedelta(hours=3, minutes=7, seconds=30))
    assert o["tokens"] == {"used": 0, "budget": None, "left": None}


def test_between_five_hour_windows_nothing_is_open_and_the_last_ones_are_listed(session):
    _five_hour_day(session)
    f = quota.view(session, FIVE_RESET + timedelta(minutes=10), PARIS)["five_hour"]
    assert f["open"] is None and [w["final_pct"] for w in f["windows"]] == [45.0, 100.0]
    assert not any(w["current"] for w in f["windows"])
    # a past week has no 5-hour window, and a hub without any reading none at all
    assert quota.view(session, FIVE_RESET + timedelta(days=8), PARIS, back=1)["five_hour"] is None
    assert quota.view(session, FIVE_START - timedelta(days=2), PARIS)["five_hour"] is None
