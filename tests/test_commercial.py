from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JournalEntry, PendingAction, ProcessedMessage, Prospect
from ibig_agent.governance import ActionRequest

NOW = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)  # lundi


def prospect(rt, email="dir@ecole.ci", **kw):
    values = {"name": "M. Koné", "pole": "SOFT", "source": "contact@ibigsoft.com",
              "need": "ERP scolaire", "status": "qualifie", **kw}
    with rt.sessions() as s:
        p = Prospect(email=email, **values)
        s.add(p)
        s.commit()
        return p.id


def get(rt, pid):
    with rt.sessions() as s:
        return s.get(Prospect, pid)


def inbound(rt, email, at, mid=None):
    with rt.sessions() as s:
        s.add(ProcessedMessage(mailbox="contact@ibigsoft.com", message_id=mid or f"<{at}>",
                               sender=email, subject="Devis", category="prospect",
                               summary="Demande un devis", received_at=at, processed_at=at))
        s.commit()


def outbound(rt, connector, email, at):
    out = rt.governor.submit(ActionRequest(
        agent="messagerie", action_type="mail.reply", channel="mail", title="Réponse",
        payload={"mailbox": connector.mailbox.adresse, "to": email, "subject": "Devis",
                 "body": "Réponse"}))
    rt.governor.approve(out.pending_id, by="Awa")
    with rt.sessions() as s:
        s.get(PendingAction, out.pending_id).decided_at = at
        s.commit()


def followups(rt, status="pending"):
    with rt.sessions() as s:
        return s.scalars(select(PendingAction).where(
            PendingAction.action_type == "commercial.followup",
            PendingAction.status == status)).all()


# ------------------------------------------------------------------ qualification
def test_qualification_uses_summaries_not_raw_mail(rt, llm):
    pid = prospect(rt, status="nouveau")
    inbound(rt, "dir@ecole.ci", NOW)
    result = rt.commercial.qualify_new()
    assert result.qualifies == 1 and result.chauds == 0
    p = get(rt, pid)
    assert (p.score, p.temperature, p.status, p.next_step) == (40, "tiede", "qualifie", "Appeler")
    assert "Demande un devis" in llm.last_user
    assert rt.commercial.qualify_new().qualifies == 0  # déjà qualifié


def test_hot_prospect_alerts_pole_sales_contact(rt, llm, connector):
    llm.qualification = {"score": 85, "temperature": "chaud", "solution": "IBIG School",
                         "prochaine_etape": "Proposer une démo", "raisons": "budget validé"}
    prospect(rt, status="nouveau")
    assert rt.commercial.qualify_new().chauds == 1
    (mail,) = connector.sent
    assert mail["to"] == "awa@ibig.test"  # pas de commercial renseigné : le valideur
    assert "Prospect chaud" in mail["subject"] and "Proposer une démo" in mail["body"]


def test_notification_to_sales_needs_no_validation(rt, connector):
    out = rt.governor.submit(ActionRequest(
        agent="commercial", action_type="notify.internal", channel="interne", title="t",
        payload={"mailbox": connector.mailbox.adresse, "to": "a@ibig.test", "subject": "s",
                 "body": "b"}))
    assert out.status == "executed"


def test_score_is_clamped(rt, llm):
    llm.qualification = {**llm.qualification, "score": 250}
    pid = prospect(rt, status="nouveau")
    rt.commercial.qualify_new()
    assert get(rt, pid).score == 100


# ------------------------------------------------------------------ séquence de relance
def test_followup_sequence(rt, connector):
    pid = prospect(rt)
    outbound(rt, connector, "dir@ecole.ci", NOW)
    assert rt.commercial.run(NOW + timedelta(days=2)).relances == 0      # trop tôt
    assert rt.commercial.run(NOW + timedelta(days=3)).relances == 1      # J+3
    assert rt.commercial.run(NOW + timedelta(days=3)).relances == 0      # déjà en attente
    (draft,) = followups(rt)
    assert draft.level == 2 and draft.payload["to"] == "dir@ecole.ci"
    assert "L'équipe IBIG SOFT" in draft.payload["body"]                 # signature
    assert connector.sent[-1]["body"] == "Réponse"                        # rien envoyé seul
    rt.governor.approve(draft.id, by="Awa")                              # relance 1 envoyée
    with rt.sessions() as s:
        s.get(PendingAction, draft.id).decided_at = NOW + timedelta(days=3)
        s.commit()
    assert rt.commercial.run(NOW + timedelta(days=6)).relances == 0      # 3 j après : non
    assert rt.commercial.run(NOW + timedelta(days=7)).relances == 1      # 4 j après : J+7
    assert get(rt, pid).followups_sent == 2 and get(rt, pid).status == "relance"


def test_prospect_reply_stops_sequence(rt, connector):
    pid = prospect(rt)
    outbound(rt, connector, "dir@ecole.ci", NOW)
    rt.commercial.run(NOW + timedelta(days=3))
    inbound(rt, "dir@ecole.ci", NOW + timedelta(days=4))
    result = rt.commercial.run(NOW + timedelta(days=10))
    assert result.relances == 0
    p = get(rt, pid)
    assert p.status == "en_discussion" and p.followups_sent == 0


def test_no_answer_after_three_followups_closes(rt, connector):
    pid = prospect(rt, followups_sent=3, status="relance")
    outbound(rt, connector, "dir@ecole.ci", NOW)
    assert rt.commercial.run(NOW + timedelta(days=6)).sans_suite == 0
    assert rt.commercial.run(NOW + timedelta(days=7)).sans_suite == 1
    assert get(rt, pid).status == "sans_suite"


def test_stop_from_dashboard_blocks_followups(rt, connector):
    pid = prospect(rt)
    outbound(rt, connector, "dir@ecole.ci", NOW)
    rt.commercial.set_status(pid, "stop", by="Awa")
    assert rt.commercial.run(NOW + timedelta(days=30)).relances == 0


def test_followup_waits_for_our_reply_first(rt):
    prospect(rt)
    inbound(rt, "dir@ecole.ci", NOW)  # le prospect a écrit, on n'a pas encore répondu
    assert rt.commercial.run(NOW + timedelta(days=10)).relances == 0


def test_invented_price_in_followup_is_flagged(rt, llm, connector):
    llm.draft = "Bonjour, profitez de notre offre à 3 000 FCFA."
    prospect(rt)
    outbound(rt, connector, "dir@ecole.ci", NOW)
    rt.commercial.run(NOW + timedelta(days=3))
    (draft,) = followups(rt)
    assert any("3 000 FCFA" in a for a in draft.payload["alertes"])


# ------------------------------------------------------------------ essais et démos
def test_trial_end_and_demo_reminders(rt, connector):
    pid = prospect(rt, trial_ends_at=NOW + timedelta(days=2), demo_at=NOW + timedelta(hours=20))
    result = rt.commercial.run(NOW)
    assert result.rappels_essai == 1 and result.rappels_demo == 1
    (draft,) = followups(rt)
    assert draft.payload["type"] == "fin_essai"
    subjects = [m["subject"] for m in connector.sent]
    assert any("Fin d'essai" in s for s in subjects) and any("Démonstration" in s for s in subjects)
    again = rt.commercial.run(NOW + timedelta(hours=1))
    assert again.rappels_essai == 0 and again.rappels_demo == 0
    assert get(rt, pid).trial_reminded_at is not None


def test_csv_import(rt, tmp_path):
    f = tmp_path / "prospects.csv"
    f.write_text("email;nom;pole;solution;besoin;fin_essai;demo\n"
                 "a@ecole.ci;École A;SOFT;IBIG School;Gestion;2026-10-01;02/10/2026 10:00\n"
                 "mauvais;X;SOFT;;;;\n"
                 "b@x.ci;B;INCONNU;;;;\n"
                 "c@x.ci;C;EDUFORM;;;31/02/2026;\n", encoding="utf-8")
    result = rt.commercial.import_csv(f)
    assert result.crees == 1 and len(result.erreurs) == 3
    with rt.sessions() as s:
        p = s.scalar(select(Prospect).where(Prospect.email == "a@ecole.ci"))
    assert p.trial_ends_at.day == 1 and p.demo_at.hour == 10 and p.source == "import"
    assert rt.commercial.import_csv(f).mis_a_jour == 1


def test_imported_prospect_follows_up_from_pole_mailbox(rt, connector, tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("email,nom,pole,fin_essai\nz@x.ci,Z,SOFT,2026-09-30\n", encoding="utf-8")
    rt.commercial.import_csv(f)
    rt.commercial.run(NOW)
    (draft,) = followups(rt)
    assert draft.payload["mailbox"] == connector.mailbox.adresse


# ------------------------------------------------------------------ tableau de bord
def test_prospect_status_from_dashboard(rt):
    store = UserStore(rt.sessions, rt.org)
    store.create("awa@ibig.test", "Awa", "valideur", "mot-de-passe-1")
    mine, other = prospect(rt), prospect(rt, email="e@x.ci", pole="EDUFORM")
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "awa@ibig.test", "password": "mot-de-passe-1"})
    page = c.get("/prospects").text
    assert "dir@ecole.ci" in page and "e@x.ci" not in page
    c.post(f"/prospects/{mine}/statut", data={"statut": "gagne"})
    assert get(rt, mine).status == "gagne"
    r = c.post(f"/prospects/{other}/statut", data={"statut": "perdu"}, follow_redirects=False)
    assert "Refus" in r.headers["location"] and get(rt, other).status == "qualifie"
    with rt.sessions() as s:
        e = s.scalar(select(JournalEntry).where(JournalEntry.action_type == "prospect.gagne"))
    assert "awa@ibig.test" in e.decided_by


def test_unknown_status_refused(rt):
    pid = prospect(rt)
    with pytest.raises(ValueError):
        rt.commercial.set_status(pid, "supprime", by="x")


# ------------------------------------------------------------------ consentement
def test_followup_always_contains_opt_out(rt, llm, connector):
    llm.draft = "Bonjour, avez-vous pu étudier notre proposition ?"
    prospect(rt)
    outbound(rt, connector, "dir@ecole.ci", NOW)
    rt.commercial.run(NOW + timedelta(days=3))
    (draft,) = followups(rt)
    assert "répondez simplement STOP" in draft.payload["body"]


@pytest.mark.parametrize("body,expected", [
    ("STOP", True),
    ("stop.\n\n> Si vous ne souhaitez plus être recontacté, répondez simplement STOP.", True),
    ("Merci de ne plus me contacter.", True),
    ("Je souhaite me désinscrire", True),
    ("Bonjour, oui je suis intéressé, rappelez-moi.\n\n> ... répondez simplement STOP.", False),
    ("Ne pas oublier : la démo est jeudi, pas de stop pour nous !", False),
])
def test_opt_out_detection(body, expected):
    from ibig_agent.agents.commercial import is_opt_out
    assert is_opt_out(body) is expected


def test_opt_out_reply_stops_followups(rt, llm, connector):
    pid = prospect(rt)
    connector.add("Re: Suite à votre demande", "STOP", sender="dir@ecole.ci")
    llm.triages["Re: Suite"] = {"categorie": "prospect"}
    rt.messagerie.poll()
    assert get(rt, pid).stop_followups
    outbound(rt, connector, "dir@ecole.ci", NOW)
    assert rt.commercial.run(NOW + timedelta(days=30)).relances == 0
