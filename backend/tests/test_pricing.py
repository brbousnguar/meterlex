import asyncio

from models import ModelPrice, Setting
import pricing


def _seed(session, model_id, **rates):
    session.add(ModelPrice(model_id=model_id, source="seed", **rates))
    session.commit()


def test_compute_cost_for_all_token_classes(session):
    _seed(session, "claude-sonnet-4-6",
          prompt=3e-6, completion=15e-6, cache_read=0.3e-6, cache_write=3.75e-6)
    cost, source = pricing.compute_cost(
        session,
        "anthropic",
        "claude-sonnet-4-6",
        {"input": 1_000, "output": 100, "cacheRead": 500, "cacheWrite": 200},
    )
    expected = 1_000 * 3e-6 + 100 * 15e-6 + 500 * 0.3e-6 + 200 * 3.75e-6
    assert cost == round(expected, 8)
    assert source == "seed"


def test_unknown_and_local_models_are_free(session):
    assert pricing.compute_cost(session, "ollama", "local/qwen", {"input": 99}) == (0.0, "free")
    assert pricing.compute_cost(session, "unknown", "not-on-rate-card", {"input": 99}) == (0.0, "free")


def test_fx_rate_falls_back_for_invalid_setting(session):
    session.add(Setting(key="fx_rate", value="not-a-number"))
    session.commit()
    assert pricing.get_fx_rate(session) == 0.92


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload, *_a, **_kw):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _url):
        return _FakeResponse(self._payload)


def test_mirror_pull_replaces_local_table(session, monkeypatch):
    session.add(ModelPrice(model_id="stale-model-no-longer-in-central", source="seed"))
    session.commit()

    payload = [{
        "model_id": "claude-sonnet-4-6", "display_name": "Claude Sonnet 4.6",
        "provider": "anthropic", "prompt": 3e-6, "completion": 15e-6,
        "cache_read": 0.3e-6, "cache_write": 3.75e-6, "reasoning": 0.0,
        "source": "seed", "overrides": [],
    }]
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(payload))

    result = asyncio.run(pricing.mirror_pull_prices(session))

    assert result == {"synced": True, "changed": True, "count": 1, "error": None}
    assert session.get(ModelPrice, "stale-model-no-longer-in-central") is None
    row = session.get(ModelPrice, "claude-sonnet-4-6")
    assert row is not None and row.prompt == 3e-6


def test_mirror_pull_reports_an_unchanged_card(session, monkeypatch):
    payload = [{"model_id": "claude-sonnet-4-6", "prompt": 3e-6, "completion": 15e-6}]
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(payload))

    assert asyncio.run(pricing.mirror_pull_prices(session))["changed"] is True
    assert asyncio.run(pricing.mirror_pull_prices(session))["changed"] is False

    payload[0]["prompt"] = 4e-6
    assert asyncio.run(pricing.mirror_pull_prices(session))["changed"] is True
    assert session.get(ModelPrice, "claude-sonnet-4-6").prompt == 4e-6


def test_mirror_pull_keeps_existing_rows_on_empty_response(session, monkeypatch):
    session.add(ModelPrice(model_id="claude-sonnet-4-6", prompt=3e-6, source="seed"))
    session.commit()

    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient([]))

    result = asyncio.run(pricing.mirror_pull_prices(session))

    assert result["synced"] is False
    assert session.get(ModelPrice, "claude-sonnet-4-6") is not None


def test_an_ollama_hosted_model_is_priced_by_its_host_whatever_ran_it(session):
    """OpenClaw runs Ollama models too: the id names the host, so the Ollama
    rule applies without the row having to claim source='ollama'."""
    from models import ModelPrice
    import pricing

    session.add(ModelPrice(model_id="ollama/glm-5.2:cloud", prompt=1e-6, completion=2e-6))
    session.commit()
    row = pricing.resolve_price(session, "ollama/glm-5.2:cloud", source="openclaw")
    assert row is not None and row.model_id == "ollama/glm-5.2:cloud"
    # A model Ollama served locally has no cloud row, so it stays free.
    assert pricing.resolve_price(session, "ollama/qwen3.6:35b-mlx", source="openclaw") is None


def _sol_payload():
    """GPT-5.6 Sol as Ratelex sends it: launched at $5 / $30, cut to $4 / $20 on 21 Aug."""
    return [{
        "model_id": "gpt-5.6-sol", "prompt": 4e-6, "completion": 20e-6, "cache_read": 0.4e-6,
        "reasoning": 20e-6, "source": "openrouter",
        "history": [
            {"on": "2026-07-09", "kind": "launch", "prompt": 5e-6, "completion": 30e-6, "cache_read": 0.5e-6},
            {"on": "2026-08-21", "kind": "change", "prompt": 4e-6, "completion": 20e-6, "cache_read": 0.4e-6},
        ],
    }]


def test_a_turn_is_priced_at_the_rate_of_its_day(session, monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(_sol_payload()))
    asyncio.run(pricing.mirror_pull_prices(session))
    usage = {"input": 1_000, "output": 100, "cacheRead": 10_000, "reasoningTokens": 50}

    def cost(at):
        return pricing.compute_cost(session, "openai", "gpt-5.6-sol", usage, at=at)[0]

    launch = round(1_000 * 5e-6 + 100 * 30e-6 + 10_000 * 0.5e-6 + 50 * 30e-6, 8)
    today = round(1_000 * 4e-6 + 100 * 20e-6 + 10_000 * 0.4e-6 + 50 * 20e-6, 8)
    assert cost(datetime(2026, 8, 1, 12)) == launch
    assert cost(datetime(2026, 8, 20, 23, 59)) == launch
    assert cost(datetime(2026, 8, 21, 0, 1)) == today       # the cut applies from its day
    assert cost(datetime(2026, 11, 11)) == today
    assert cost(datetime(2026, 6, 1)) == launch               # before launch: the launch price
    assert cost(None) == today                                # no date: today's card


def test_a_model_with_one_point_keeps_todays_rate(session, monkeypatch):
    """A single launch point means no announced change, so the row rules, even if it moved since."""
    from datetime import datetime
    payload = [{"model_id": "gpt-5", "prompt": 1.25e-6, "completion": 10e-6,
                "history": [{"on": "2025-08-07", "kind": "launch", "prompt": 1e-6, "completion": 8e-6}]}]
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(payload))
    asyncio.run(pricing.mirror_pull_prices(session))
    assert pricing.compute_cost(session, "openai", "gpt-5", {"input": 1_000}, at=datetime(2025, 9, 1))[0] == 0.00125


def test_a_history_change_alone_reprices(session, monkeypatch):
    payload = _sol_payload()
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(payload))
    assert asyncio.run(pricing.mirror_pull_prices(session))["changed"] is True
    assert asyncio.run(pricing.mirror_pull_prices(session))["changed"] is False
    payload[0]["history"][0]["prompt"] = 6e-6
    assert asyncio.run(pricing.mirror_pull_prices(session))["changed"] is True
