from conftest import ROOT
from sqlalchemy import func, select

from ibig_agent.config import Settings
from ibig_agent.db import PendingAction, ProcessedMessage, Prospect, Ticket
from ibig_agent.demo import DemoMailbox, DemoSite, build_demo, seed


def test_demo_uses_only_simulated_channels_and_seeds_every_path(tmp_path):
    demo = build_demo(tmp_path / "demo", Settings(config_dir=ROOT / "config",
                                                  knowledge_dir=ROOT / "knowledge",
                                                  _env_file=None))
    rt = demo.runtime
    assert all(isinstance(c, DemoMailbox) for c in rt.connectors.values())
    assert all(isinstance(c, DemoSite) for c in rt.web_connectors.values())
    assert rt.whatsapp_clients == {} and type(rt.llm).__name__ == "DemoLLM"
    seed(demo)
    with rt.sessions() as s:
        decisions = dict(s.execute(select(ProcessedMessage.decision, func.count())
                                   .group_by(ProcessedMessage.decision)).all())
        assert s.scalar(select(Prospect)).temperature == "chaud"
        assert s.scalar(select(func.count()).select_from(Ticket)) == 1
        pending = s.scalar(select(func.count()).select_from(PendingAction).where(
            PendingAction.status == "pending"))
    assert {"faq", "support_auto", "prospect", "signale", "direction", "spam",
            "transfere"} <= set(decisions)
    assert pending >= 4
    # Le dépôt réel n'est pas modifié par la démonstration.
    assert "statut: a_completer" in (ROOT / "knowledge/poles/soft.md").read_text("utf-8")
