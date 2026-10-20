from datetime import datetime, timedelta

from sqlmodel import Session

from models import UsageTurn

T0 = datetime(2026, 10, 20, 9, 0, 0)        # naive UTC, as rows are stored


def _turn(n, minutes, model="claude-opus-5-5", sid="s1", source="claude-code", machine="brahim-mini", tok=100):
    return UsageTurn(
        source=source, session_id=sid, turn_key=f"t{n}", model_id=model, machine=machine,
        ts=T0 + timedelta(minutes=minutes), input_tokens=tok // 2, output_tokens=tok // 4,
        cache_read=tok // 8, cache_write=tok // 8, total_tokens=tok,
        cost_eur=0.5, cost_usd=0.55,
    )


def _seed(engine, *turns):
    with Session(engine) as session:
        session.add_all(turns)
        session.commit()


def test_unknown_session_is_found_false_with_zeros(client):
    body = client.get("/api/sessions/nope/usage").json()
    assert body["found"] is False
    assert body["turns"] == 0 and body["total_tokens"] == 0 and body["cost_eur"] == 0
    assert body["by_model"] == {} and body["active_seconds"] == 0
    assert body["first_ts"] is None and body["source"] is None


def test_whole_session_sums_and_splits_by_model(client, engine):
    _seed(engine, _turn(1, 0), _turn(2, 1), _turn(3, 2, model="claude-fable-5-1", tok=40),
          _turn(4, 3, sid="other"))
    body = client.get("/api/sessions/s1/usage").json()
    assert body["found"] is True
    assert body["source"] == "claude-code" and body["machine"] == "brahim-mini"
    assert body["turns"] == 3
    assert body["total_tokens"] == 240
    assert body["input_tokens"] == 120 and body["cache_read"] == 12 + 12 + 5
    assert body["cost_eur"] == 1.5 and body["cost_usd"] == 1.65
    assert body["by_model"]["claude-opus-5-5"] == {"turns": 2, "total_tokens": 200, "cost_eur": 1.0, "cost_usd": 1.1}
    assert body["by_model"]["claude-fable-5-1"]["turns"] == 1
    assert body["first_ts"].startswith("2026-10-20T09:00") and body["last_ts"].startswith("2026-10-20T09:02")


def test_window_is_from_inclusive_to_exclusive(client, engine):
    _seed(engine, *(_turn(n, n * 10) for n in range(5)))          # 09:00 … 09:40
    body = client.get("/api/sessions/s1/usage",
                      params={"from": "2026-10-20T09:10:00Z", "to": "2026-10-20T09:30:00Z"}).json()
    assert body["turns"] == 2                                      # 09:10 and 09:20
    assert body["first_ts"].startswith("2026-10-20T09:10")


def test_window_accepts_an_offset_and_keeps_found_when_empty(client, engine):
    _seed(engine, _turn(1, 0))
    # 11:00+02:00 is 09:00Z, so the one turn falls in [10:30+02:00, 11:30+02:00)
    hit = client.get("/api/sessions/s1/usage",
                     params={"from": "2026-10-20T10:30:00+02:00", "to": "2026-10-20T11:30:00+02:00"}).json()
    assert hit["turns"] == 1
    empty = client.get("/api/sessions/s1/usage", params={"from": "2026-10-21T00:00:00Z"}).json()
    assert empty["found"] is True and empty["turns"] == 0 and empty["machine"] == "brahim-mini"


def test_active_seconds_cap_each_gap_at_five_minutes(client, engine):
    # gaps: 2 min, 60 min (counts 5), 1 min  → 120 + 300 + 60
    _seed(engine, _turn(1, 0), _turn(2, 2), _turn(3, 62), _turn(4, 63))
    assert client.get("/api/sessions/s1/usage").json()["active_seconds"] == 480


def test_one_id_under_two_sources_is_summed_and_named_by_the_most_common(client, engine):
    _seed(engine, _turn(1, 0), _turn(2, 1), _turn(3, 2, source="ollama", model="qwen3.6:35b", machine="brahims-macbook-pro"))
    body = client.get("/api/sessions/s1/usage").json()
    assert body["turns"] == 3
    assert body["source"] == "claude-code" and body["machine"] == "brahim-mini"
    assert set(body["by_model"]) == {"claude-opus-5-5", "qwen3.6:35b"}


def test_bad_date_is_a_400(client):
    assert client.get("/api/sessions/s1/usage", params={"from": "yesterday"}).status_code == 400
