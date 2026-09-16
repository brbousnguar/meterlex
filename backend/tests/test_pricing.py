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

    assert result == {"synced": True, "count": 1, "error": None}
    assert session.get(ModelPrice, "stale-model-no-longer-in-central") is None
    row = session.get(ModelPrice, "claude-sonnet-4-6")
    assert row is not None and row.prompt == 3e-6


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
