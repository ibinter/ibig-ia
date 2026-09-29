from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ibig_agent.agents.veille import business_hours_between
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JournalEntry, ProcessedMessage

# Abidjan = UTC+0 toute l'année : heures locales = heures UTC.
MON_9 = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)   # lundi
FRI_17 = datetime(2026, 10, 2, 17, 0, tzinfo=UTC)  # vendredi
NOW = datetime(2026, 9, 28, 15, 0, tzinfo=UTC)     # lundi 15 h


def bh(a, b):
    return business_hours_between(a, b, "Africa/Abidjan", 8, 18)


def test_business_hours():
    assert bh(MON_9, MON_9 + timedelta(hours=2)) == 2
    assert bh(FRI_17, FRI_17 + timedelta(days=3)) == 10  # 1 h ven. + 9 h lun. (week-end exclu)
    assert bh(FRI_17, datetime(2026, 10, 5, 9, 0, tzinfo=UTC)) == 2  # 1 h ven. + 1 h lun.
    assert bh(datetime(2026, 9, 28, 6, 0, tzinfo=UTC), MON_9) == 1   # ouverture à 8 h
    assert bh(MON_9, MON_9 - timedelta(hours=1)) == 0


def mail(rt, mid, received, category="client", sentiment="neutre", pole="SOFT",
         decision="brouillon", processed=None, **kw):
    with rt.sessions() as s:
        s.add(ProcessedMessage(mailbox="contact@ibigsoft.com", message_id=mid,
                               sender="c@x.ci", subject=f"Sujet {mid}", pole=pole,
                               category=category, sentiment=sentiment, decision=decision,
                               received_at=received, processed_at=processed or received,
                               summary=f"Résumé {mid}", **kw))
        s.commit()


def reply(rt, mid, at, action="mail.reply"):
    with rt.sessions() as s:
        s.add(JournalEntry(agent="messagerie", action_type=action, level=2, channel="mail",
                           status="executed", summary="r", details={"ref": mid},
                           decided_by="Awa", created_at=at))
        s.commit()


def item(report, name):
    return next(i for i in report.items if i.nom.startswith(name))


# ------------------------------------------------------------------ indicateurs
def test_response_time_in_business_hours(rt):
    mail(rt, "<a>", MON_9)
    reply(rt, "<a>", MON_9 + timedelta(hours=1))
    mail(rt, "<b>", MON_9)
    reply(rt, "<b>", MON_9 + timedelta(hours=5))
    mail(rt, "<c>", MON_9)                                    # sans réponse
    mail(rt, "<d>", MON_9, category="fournisseur")            # hors périmètre
    ind = item(rt.veille.indicators(7, NOW), "Délai de première réponse")
    assert ind.valeur == "5.0 h" and ind.etat == "alerte"     # médiane de [1, 5] -> 5
    assert "50 %" in ind.detail and "1 mail(s) sans réponse" in ind.detail


def test_acknowledgement_does_not_count_as_reply(rt):
    mail(rt, "<a>", MON_9)
    reply(rt, "<a>", MON_9 + timedelta(minutes=1), action="mail.ack")
    assert item(rt.veille.indicators(7, NOW), "Délai de première réponse").etat == "a_mesurer"


def test_classified_share_and_unchecked_actions(rt):
    mail(rt, "<a>", MON_9)
    mail(rt, "<b>", MON_9, decision="a_trier_manuel")
    report = rt.veille.indicators(7, NOW)
    assert item(report, "Mails lus et classés").valeur.startswith("50 %")
    assert item(report, "Actions sensibles").etat == "ok"
    with rt.sessions() as s:  # une action de niveau 2 exécutée sans décision humaine
        s.add(JournalEntry(agent="x", action_type="mail.reply", level=2, channel="mail",
                           status="executed", summary="anomalie", created_at=NOW))
        s.commit()
    assert item(rt.veille.indicators(7, NOW), "Actions sensibles").valeur == "1"


def test_publications_per_pole(rt):
    for _ in range(3):
        with rt.sessions() as s:
            s.add(JournalEntry(agent="communication", action_type="social.post", level=2,
                               channel="facebook", pole="SOFT", status="executed",
                               summary="p", decided_by="Awa", created_at=NOW))
            s.commit()
    ind = item(rt.veille.indicators(7, NOW), "Publications par pôle")
    assert "SOFT 3.0" in ind.valeur and "SOFT" not in ind.detail and ind.etat == "alerte"


def test_draft_quality_r08(rt, connector):
    from ibig_agent.governance import ActionRequest
    for body in ("ok", "ok", "ok", "modifié"):
        out = rt.governor.submit(ActionRequest(
            agent="messagerie", action_type="mail.reply", channel="mail", title="t",
            payload={"mailbox": connector.mailbox.adresse, "to": "a@b.ci", "subject": "s",
                     "body": "ok"}))
        rt.governor.approve(out.pending_id, by="Awa", payload_override=(
            {"body": body, "modifie_par_valideur": True} if body != "ok" else None))
    ind = item(rt.veille.indicators(7), "Brouillons de réponse")
    assert ind.valeur == "75 %" and ind.etat == "alerte"


def test_reply_ref_is_recorded_end_to_end(rt, llm, connector):
    faq_id = next(f.id for f in rt.kb.faq if "essai" in f.id)
    connector.add("Essai", "Durée de l'essai ?", mid="<essai@x>")
    llm.triages["Essai"] = {"categorie": "support", "faq_id": faq_id}
    rt.messagerie.poll()
    with rt.sessions() as s:
        e = s.scalar(select(JournalEntry).where(JournalEntry.action_type == "mail.faq_reply"))
    assert e.details["ref"] == "<essai@x>"
    assert item(rt.veille.indicators(7), "Délai de première réponse").etat == "ok"


# ------------------------------------------------------------------ alertes
@pytest.fixture
def direction(rt):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "DG", "direction", "mot-de-passe-1")


def alerts(rt):
    with rt.sessions() as s:
        return s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "veille.alert")).all()


def test_negative_mail_alerts_direction_and_pole_validator_once(rt, connector, direction):
    mail(rt, "<n>", NOW - timedelta(minutes=10), sentiment="negatif")
    mail(rt, "<spam>", NOW - timedelta(minutes=10), sentiment="negatif", category="spam")
    result = rt.veille.check_alerts(NOW)
    assert result.negatifs == 1
    assert {m["to"] for m in connector.sent} == {"dg@ibig.test", "awa@ibig.test"}
    assert "Sujet <n>" in connector.sent[0]["body"]
    assert rt.veille.check_alerts(NOW).negatifs == 0  # déjà signalé


def test_unanswered_mail_alert_after_target(rt, direction):
    mail(rt, "<late>", NOW - timedelta(hours=3))
    mail(rt, "<fresh>", NOW - timedelta(hours=1))
    mail(rt, "<done>", NOW - timedelta(hours=3))
    reply(rt, "<done>", NOW - timedelta(hours=2))
    assert rt.veille.check_alerts(NOW).sans_reponse == 1
    (a,) = alerts(rt)
    assert a.details["refs"] == ["<late>"] and a.pole == "SOFT"


def test_negative_mail_can_also_be_flagged_unanswered(rt, direction):
    mail(rt, "<angry>", NOW - timedelta(minutes=10), sentiment="negatif")
    assert rt.veille.check_alerts(NOW).negatifs == 1
    later = NOW + timedelta(hours=3)
    result = rt.veille.check_alerts(later)
    assert result.negatifs == 0 and result.sans_reponse == 1


def test_weekend_does_not_trigger_unanswered_alert(rt, direction):
    sat = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
    mail(rt, "<we>", sat)
    assert rt.veille.check_alerts(sat + timedelta(hours=26)).sans_reponse == 0  # dimanche


def test_message_spike(rt, direction):
    for i in range(12):
        mail(rt, f"<s{i}>", NOW - timedelta(minutes=5), category="spam")
    assert rt.veille.check_alerts(NOW).pic
    assert not rt.veille.check_alerts(NOW).pic  # une alerte par heure


def test_alerts_logged_even_without_notification_mailbox(rt):
    rt.settings.notification_mailbox = ""
    mail(rt, "<n>", NOW - timedelta(minutes=10), sentiment="negatif")
    result = rt.veille.check_alerts(NOW)
    assert result.negatifs == 1 and result.envoyees == 0 and len(alerts(rt)) == 1


def test_veille_cannot_act_on_channels(rt):
    from ibig_agent.governance import ActionRequest, GovernanceError
    with pytest.raises(GovernanceError):
        rt.governor.submit(ActionRequest(agent="veille", action_type="mail.reply",
                                         channel="mail", title="x"))


# ------------------------------------------------------------------ tableau de bord
def test_indicators_page(rt, direction):
    store = UserStore(rt.sessions, rt.org)
    store.create("awa@ibig.test", "Awa", "valideur", "mot-de-passe-1")
    mail(rt, "<n>", NOW - timedelta(minutes=10), sentiment="negatif", pole="EDUFORM")
    rt.veille.check_alerts(NOW)
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "awa@ibig.test", "password": "mot-de-passe-1"})
    page = c.get("/indicateurs?jours=30").text
    assert "Délai de première réponse" in page and "moins de 2 h" in page
    assert "mécontent" not in page  # alerte d'un autre pôle
    c2 = TestClient(create_app(rt), base_url="https://testserver")
    c2.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-1"})
    assert "mécontent" in c2.get("/indicateurs").text
