from models import ModelPrice, Setting
import pricing


def test_seed_and_compute_all_token_classes(session):
    pricing.seed_prices(session)
    cost, source = pricing.compute_cost(
        session,
        "anthropic",
        "claude-sonnet-4-6",
        {"input": 1_000, "output": 100, "cacheRead": 500, "cacheWrite": 200},
    )
    expected = 1_000 * 3e-6 + 100 * 15e-6 + 500 * 0.3e-6 + 200 * 3.75e-6
    assert cost == round(expected, 8)
    assert source == "seed"


def test_manual_price_is_not_overwritten(session):
    session.add(ModelPrice(model_id="gpt-5", prompt=0.123, source="manual"))
    session.commit()
    pricing.seed_prices(session)
    assert session.get(ModelPrice, "gpt-5").prompt == 0.123


def test_unknown_and_local_models_are_free(session):
    pricing.seed_prices(session)
    assert pricing.compute_cost(session, "ollama", "local/qwen", {"input": 99}) == (0.0, "free")
    assert pricing.compute_cost(session, "unknown", "not-on-rate-card", {"input": 99}) == (0.0, "free")


def test_fx_rate_falls_back_for_invalid_setting(session):
    session.add(Setting(key="fx_rate", value="not-a-number"))
    session.commit()
    assert pricing.get_fx_rate(session) == 0.92
