from types import SimpleNamespace

import pytest
from sqlalchemy import select

from ibig_agent.config import Settings
from ibig_agent.db import AIUsage, JournalEntry, init_db, make_engine
from ibig_agent.llm import BudgetExceeded, ClaudeClient, LLMRefusal


def response(text='{"ok": true}', stop="end_turn", model="claude-haiku-4-5"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, model=model,
        usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=0,
                              cache_creation_input_tokens=0, cache_read_input_tokens=0))


class FakeMessages:
    def __init__(self, resp):
        self.resp, self.kwargs = resp, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.resp


def make(tmp_path, resp, budget=10.0):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'llm.db'}",
                        monthly_ai_budget_usd=budget, _env_file=None)
    sessions = init_db(make_engine(settings.database_url))
    fake = SimpleNamespace(messages=FakeMessages(resp),
                           beta=SimpleNamespace(messages=FakeMessages(resp)))
    return ClaudeClient(settings, sessions, client=fake), fake, sessions


def test_triage_uses_light_model_and_records_cost(tmp_path):
    client, fake, sessions = make(tmp_path, response())
    assert client.structured("t", "triage", "sys", "user", {"type": "object"}) == {"ok": True}
    assert fake.messages.kwargs["model"] == "claude-haiku-4-5"
    assert "effort" not in fake.messages.kwargs["output_config"]
    with sessions() as s:
        assert s.scalar(select(AIUsage)).cost_usd == pytest.approx(1.0)


def test_writing_uses_advanced_model_with_fallback(tmp_path):
    client, fake, _ = make(tmp_path, response("Bonjour", model="claude-opus-5-5"))
    assert client.write("w", "sys", "user") == "Bonjour"
    kw = fake.beta.messages.kwargs
    assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
    assert kw["output_config"]["effort"] == "medium"


def test_refusal_raises(tmp_path):
    client, _, _ = make(tmp_path, response(stop="refusal", model="claude-opus-5-5"))
    with pytest.raises(LLMRefusal):
        client.write("w", "sys", "user")


def test_budget_alert_then_block(tmp_path):
    client, _, sessions = make(tmp_path, response(), budget=2.4)
    client.structured("t", "triage", "s", "u", {})  # 1.00 USD
    client.structured("t", "triage", "s", "u", {})  # 2.00 USD -> > 80 %
    client.structured("t", "triage", "s", "u", {})  # alerte émise avant cet appel
    with sessions() as s:
        alerts = s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "budget.alert")).all()
    assert len(alerts) == 1
    with pytest.raises(BudgetExceeded):
        client.structured("t", "triage", "s", "u", {})
