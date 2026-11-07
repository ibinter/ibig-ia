from datetime import timedelta

from sqlalchemy import select

from ibig_agent.db import JournalEntry, PendingAction, ProcessedMessage, Prospect, Ticket, utcnow
from ibig_agent.governance import ActionRequest
from ibig_agent.privacy import erase_contact, export_contact, purge

EMAIL = "parent@x.ci"


def seed(rt, connector, llm):
    connector.add("Tarifs", "Bonjour, quel est le prix ?", sender=EMAIL, mid="<p1@x>")
    llm.triages["Tarifs"] = {"categorie": "prospect", "resume": "Demande le prix"}
    rt.messagerie.poll()
    rt.support.handle_chat("Question sans réponse", "SOFT", contact=EMAIL)


def test_export_gathers_everything(rt, connector, llm):
    seed(rt, connector, llm)
    data = export_contact(rt.sessions, "  Parent@X.ci ")
    assert data.prospect["email"] == EMAIL
    assert data.mails[0]["summary"] == "Demande le prix"
    assert len(data.tickets) == 1 and data.validations  # brouillon de réponse en attente


def test_erase_removes_data_and_cancels_pending_messages(rt, connector, llm):
    seed(rt, connector, llm)
    erase_contact(rt.sessions, EMAIL, by="DPO")
    assert export_contact(rt.sessions, EMAIL).empty
    with rt.sessions() as s:
        assert s.scalar(select(Prospect)) is None and s.scalar(select(Ticket)) is None
        assert s.scalar(select(ProcessedMessage)) is None
        (pa,) = s.scalars(select(PendingAction).where(
            PendingAction.action_type == "mail.reply")).all()
        assert pa.status == "rejected" and pa.payload == {"efface": True}
        texts = [f"{e.summary} {e.details}" for e in s.scalars(select(JournalEntry)).all()]
    assert not any(EMAIL in t or "<p1@x>" in t for t in texts)
    assert any("Données d'un contact effacées" in t for t in texts)


def test_purge_respects_retention(rt, connector):
    old = utcnow() - timedelta(days=400)
    with rt.sessions() as s:
        s.add_all([
            JournalEntry(agent="x", action_type="t", level=1, channel="mail", status="executed",
                         created_at=old),
            JournalEntry(agent="x", action_type="t", level=1, channel="mail", status="executed"),
            ProcessedMessage(mailbox="m", message_id="old", processed_at=old),
            ProcessedMessage(mailbox="m", message_id="new"),
            Ticket(channel="sara", question="q", status="resolu", created_at=old),
            Ticket(channel="sara", question="q", status="ouvert", created_at=old),
            Prospect(email="a@x", status="perdu", updated_at=old),
            Prospect(email="b@x", status="en_discussion", updated_at=old),
        ])
        s.commit()
    out = rt.governor.submit(ActionRequest(agent="messagerie", action_type="mail.reply",
                                           channel="mail", title="en attente"))
    with rt.sessions() as s:
        s.get(PendingAction, out.pending_id).created_at = old
        s.commit()
    r = purge(rt.sessions, 12)
    assert (r.journal, r.mails, r.tickets, r.prospects, r.validations) == (1, 1, 1, 1, 0)
    with rt.sessions() as s:
        assert s.get(PendingAction, out.pending_id) is not None  # jamais une validation ouverte
        assert {p.email for p in s.scalars(select(Prospect))} == {"b@x"}
