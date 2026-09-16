from datetime import datetime

from sqlmodel import Session

from models import Setting, UsageTurn
import machines


def _seed(engine):
    with Session(engine) as session:
        session.add(Setting(key="fx_rate", value="0.92"))
        session.add(Setting(key="sub_claude_code_eur", value="10"))
        session.add(
            UsageTurn(
                source="claude-code",
                session_id="s1",
                turn_key="t1",
                model_id="claude-sonnet-4-6",
                ts=datetime(2026, 7, 15),
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cost_eur=12.5,
            )
        )
        session.commit()


def test_health_is_empty_but_healthy(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "total_turns": 0,
        "last_ingested_at": None,
        "by_source": {},
        "by_machine": {},
    }


def test_monthly_summary_includes_usage_and_zero_filled_tools(client, engine):
    _seed(engine)
    response = client.get("/api/summary", params={"period": "monthly", "ref": "2026-07"})
    assert response.status_code == 200
    body = response.json()
    claude = next(tool for tool in body["tools"] if tool["source"] == "claude-code")
    assert claude["turns"] == 1
    assert claude["cost_eur"] == 12.5
    assert claude["total_tokens"] == 120
    assert len(body["tools"]) == 6


def test_summary_rejects_invalid_period_and_month(client):
    assert client.get("/api/summary", params={"period": "hourly"}).status_code == 400
    assert client.get("/api/summary", params={"period": "monthly", "ref": "bad"}).status_code in {400, 422}


def test_ingest_needs_a_machine_key(client):
    assert client.post("/api/ingest", json={"turns": []}).status_code == 401
    assert client.post("/api/ingest", json={"turns": []}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_ingest_stores_a_batch_under_the_key_s_machine(client, engine):
    with Session(engine) as session:
        key = machines.create(session, "sqli-laptop", "basename")
    turn = {"source": "claude-code", "session_id": "s", "turn_key": "m", "model_id": "claude-sonnet-5",
            "ts": "2026-09-15T10:00:00", "project": "C:/Users/me/RocheBB/rbb-em-dashboard",
            "input_tokens": 1, "output_tokens": 2}
    response = client.post("/api/ingest", json={"collector": "0.1.0", "turns": [turn]},
                           headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert (response.json()["machine"], response.json()["inserted"]) == ("sqli-laptop", 1)
    [machine] = client.get("/api/machines").json()
    assert (machine["name"], machine["turns"], machine["collector_version"]) == ("sqli-laptop", 1, "0.1.0")
    spend = client.get("/api/spend", params={"frm": "2026-09-01", "to": "2026-10-01"}).json()
    assert spend["by_machine"][0]["machine"] == "sqli-laptop"
    assert spend["by_project"][0]["project"] == "rbb-em-dashboard"
