"""Publication automatique programmée (section 8), veille des commentaires Facebook
(section 6) et validation depuis WhatsApp (section 12)."""

from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote

import httpx
import pytest
from fastapi.testclient import TestClient

from ibig_agent.agents.validation_whatsapp import parse_command
from ibig_agent.auth import UserStore
from ibig_agent.channels.social import oauth1_header
from ibig_agent.channels.whatsapp import InboundMessage, WhatsAppClient
from ibig_agent.config import WhatsAppNumber
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JournalEntry, PendingAction, ScheduledPost, SocialComment
from ibig_agent.governance import ActionRequest
from ibig_agent.publishing import (
    is_connected,
    media_token,
    publish_due,
    read_media_token,
    save_credentials,
    social_executors,
)

PASSWORD = "mot-de-passe-solide"
LATER = datetime(2100, 1, 1, tzinfo=UTC)


class FakeMeta:
    """API Graph et Threads simulées : enregistre les appels."""

    def __init__(self):
        self.calls = []
        self.fail = False
        self.comments = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode()) if request.method == "POST" else {}
        self.calls.append((request.method, request.url.path,
                           {k: v[0] for k, v in form.items()}))
        if self.fail:
            return httpx.Response(400, json={"error": {"message": "jeton expiré"}})
        path = request.url.path
        if path.endswith("/photos"):
            return httpx.Response(200, json={"id": "p1", "post_id": "page_42"})
        if path.endswith("/feed") and request.method == "POST":
            return httpx.Response(200, json={"id": "page_43"})
        if path.endswith("/feed"):
            return httpx.Response(200, json={"data": [{"id": "page_1", "comments": {
                "data": self.comments}}]})
        if path.endswith("/media"):
            return httpx.Response(200, json={"id": "container_1"})
        if path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "ig_77"})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        return httpx.Response(200, json={"name": "IBIG Soft"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def meta(rt):
    fake = FakeMeta()
    rt.social_client_factory = fake.client
    rt.governor.executors.update(social_executors(rt.sessions, rt.settings, fake.client))
    return fake


def connect(rt, reseau="facebook_page", compte="IBIG Soft", **creds):
    creds = creds or {"page_id": "4242", "token": "jeton-page"}
    save_credentials(rt.sessions, rt.settings, reseau, compte, creds, "test")


def validated_post(rt, reseau="facebook_page", compte="IBIG Soft", day="2099-10-05"):
    pid = rt.governor.submit(ActionRequest(
        agent="communication", action_type="social.post", channel="facebook", account=compte,
        pole="SOFT", title=f"Lundi 05/10 · {reseau} · Nouvelle version de Scolaby",
        payload={"reseau": reseau, "compte": compte, "date": day,
                 "texte": "Scolaby 3 arrive. Découvrez les nouveautés.",
                 "brief_visuel": "École"})).pending_id
    return rt.governor.approve(pid, by="Awa <awa@ibig.test>")


def test_validated_post_is_scheduled_then_published_with_its_visual(rt, meta):
    connect(rt)
    out = validated_post(rt)
    assert out.status == "executed" and out.result["mode"] == "programme"
    with rt.sessions() as s:
        sp = s.get(ScheduledPost, out.result["publication"])
    assert sp.status == "programme" and sp.titre == "Nouvelle version de Scolaby"
    assert sp.pole == "SOFT" and sp.pending_id == out.pending_id
    # 5 octobre à 10 h, heure d'Abidjan (UTC)
    assert sp.publish_at.replace(tzinfo=UTC) == datetime(2099, 10, 5, 10, tzinfo=UTC)

    assert publish_due(rt.governor, rt.sessions, now=datetime(2099, 10, 5, 9, tzinfo=UTC)) == 0
    assert meta.calls == []
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 1
    _, path, form = meta.calls[-1]
    assert path == "/v23.0/4242/photos" and form["caption"].startswith("Scolaby 3")
    assert form["url"].startswith("https://tableau.ibig.test/media/")
    with rt.sessions() as s:
        sp = s.get(ScheduledPost, sp.id)
        entry = s.query(JournalEntry).filter_by(action_type="social.schedule_approved").one()
    assert sp.status == "publie" and sp.result["id"] == "page_42"
    assert entry.status == "executed" and entry.agent == "communication"
    # Jamais publiée deux fois
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 0


def test_unconnected_account_stays_manual(rt, meta):
    out = validated_post(rt)
    assert out.result["mode"] == "manuel"
    with rt.sessions() as s:
        assert s.query(ScheduledPost).count() == 0


def test_instagram_publishes_image_container(rt, meta):
    connect(rt, "instagram", "@ib_inter_officiel", ig_user_id="178", token="jeton-ig")
    out = validated_post(rt, "instagram", "@ib_inter_officiel")
    publish_due(rt.governor, rt.sessions, now=LATER)
    paths = [c[1] for c in meta.calls]
    assert paths == ["/v23.0/178/media", "/v23.0/178/media_publish"]
    assert meta.calls[0][2]["image_url"].startswith("https://tableau.ibig.test/media/")
    with rt.sessions() as s:
        assert s.get(ScheduledPost, out.result["publication"]).result["id"] == "ig_77"


def test_schedule_action_only_publishes_validated_text(rt, meta):
    # Un agent ne peut pas inventer une publication : l'exécuteur relit la base.
    out = rt.governor.submit(ActionRequest(
        agent="communication", action_type="social.schedule_approved", channel="facebook",
        title="tentative", payload={"publication": 999, "texte": "non validé"}))
    assert out.status == "failed" and meta.calls == []


def test_stopped_channel_holds_publication(rt, meta):
    connect(rt)
    validated_post(rt)
    rt.governor.set_stopped("facebook", True, by="dg")
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 0
    assert meta.calls == []
    rt.governor.set_stopped("facebook", False, by="dg")
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 1


def test_failure_is_shown_and_can_be_retried(rt, meta, accounts):
    connect(rt)
    out = validated_post(rt)
    meta.fail = True
    publish_due(rt.governor, rt.sessions, now=LATER)
    sid = out.result["publication"]
    with rt.sessions() as s:
        sp = s.get(ScheduledPost, sid)
    assert sp.status == "echec" and "jeton expiré" in sp.result["erreur"]
    c = login(rt, "dg@ibig.test")
    assert "jeton expiré" in c.get("/reseaux").text
    c.post(f"/reseaux/programme/{sid}/reessayer")
    meta.fail = False
    assert publish_due(rt.governor, rt.sessions, now=LATER) == 1


def test_media_link_is_signed_and_renders_png(rt, meta):
    connect(rt)
    sid = validated_post(rt).result["publication"]
    token = media_token(rt.settings.secret_key, sid)
    assert read_media_token(rt.settings.secret_key, token) == sid
    assert read_media_token(rt.settings.secret_key, token, now=LATER.timestamp()) is None
    c = TestClient(create_app(rt))
    r = c.get(f"/media/{token}.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    forged = f"{sid + 1}.{token.split('.')[1]}.{token.split('.')[2]}"
    assert c.get(f"/media/{forged}.png").status_code == 404


def test_x_oauth_header_is_signed():
    creds = {"consumer_key": "ck", "consumer_secret": "cs", "access_token": "at",
             "access_secret": "as"}
    h = oauth1_header("POST", "https://api.x.com/2/tweets", creds, nonce="n", timestamp="1")
    assert h.startswith("OAuth ") and 'oauth_signature="' in h
    assert h == oauth1_header("POST", "https://api.x.com/2/tweets", creds, "n", "1")
    assert h != oauth1_header("POST", "https://api.x.com/2/tweets",
                              {**creds, "access_secret": "autre"}, "n", "1")


# ------------------------------------------------------------------ tableau de bord
@pytest.fixture
def accounts(rt):
    store = UserStore(rt.sessions, rt.org)
    store.create("awa@ibig.test", "Awa", "valideur", PASSWORD)
    store.create("dg@ibig.test", "Direction", "direction", PASSWORD)
    store.create("admin@ibig.test", "Admin", "admin", PASSWORD)
    return store


def login(rt, email):
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": email, "password": PASSWORD})
    return c


def test_networks_page_saves_encrypted_access(rt, meta, accounts):
    c = login(rt, "dg@ibig.test")
    page = c.get("/reseaux").text
    assert "IBIG Soft" in page and "publication à la main" in page  # groupes, TikTok
    idx = next(i for i, a in enumerate(rt.org.social_accounts)
               if a.reseau == "facebook_page" and a.compte == "IBIG Soft")
    r = c.post(f"/reseaux/{idx}/acces", data={"page_id": "4242", "token": "jeton-secret"},
               follow_redirects=False)
    assert "enregistr" in unquote(r.headers["location"])
    assert is_connected(rt.sessions, rt.settings, "facebook_page", "IBIG Soft")
    assert "jeton-secret" not in c.get("/reseaux").text
    r = c.post(f"/reseaux/{idx}/tester", follow_redirects=False)
    assert "IBIG Soft" in unquote(r.headers["location"])
    # Un valideur ne raccorde rien
    v = login(rt, "awa@ibig.test")
    assert v.post(f"/reseaux/{idx}/acces", data={"token": "x"}).status_code == 403


# ------------------------------------------------------------------ veille des commentaires
def test_negative_comment_raises_alert_once(rt, meta):
    connect(rt)
    meta.comments = [
        {"id": "c1", "message": "Merci pour la formation, bravo !", "from": {"name": "Ama"}},
        {"id": "c2", "message": "C'est une arnaque, jamais reçu ma commande",
         "from": {"name": "Koffi"}},
    ]
    res = rt.comments.run()
    assert (res.pages, res.nouveaux, res.negatifs) == (1, 2, 1)
    with rt.sessions() as s:
        assert s.get(SocialComment, "c2").sentiment == "negatif"
        assert s.get(SocialComment, "c1").sentiment == "positif"
        alerts = s.query(JournalEntry).filter_by(action_type="veille.alert").all()
    assert len(alerts) == 1 and alerts[0].pole == "SOFT"
    assert "arnaque" in alerts[0].details["texte"]
    assert rt.comments.run().nouveaux == 0


# ------------------------------------------------------------------ validation WhatsApp
def test_parse_command():
    assert parse_command("OK 12") == (True, 12, "")
    assert parse_command("ok n°12") == (True, 12, "")
    assert parse_command("NON 7 : prix erroné") == (False, 7, "prix erroné")
    assert parse_command("bonjour") is None


@pytest.fixture
def wa(rt, meta, monkeypatch):
    monkeypatch.setenv("WA_TOKEN", "jeton-wa")
    number = WhatsAppNumber(nom="Validation", numero="2250700000000", phone_number_id="555",
                            pole="GROUPE", token_env="WA_TOKEN")
    rt.whatsapp_clients["555"] = WhatsAppClient(number, "v23.0", meta.client())
    return meta


def inbound(text, sender="2250711111111"):
    return InboundMessage("555", sender, "Awa", "wamid.x", datetime.now(UTC), "text", text)


def test_validator_approves_from_whatsapp(rt, wa, accounts, connector):
    awa = accounts.by_email("awa@ibig.test")
    accounts.set_phone(awa.id, "+225 07 11 11 11 11")
    pid = rt.governor.submit(ActionRequest(
        agent="messagerie", action_type="mail.reply", channel="mail", pole="SOFT",
        title="Réponse à M. Kouassi",
        payload={"mailbox": connector.mailbox.adresse, "to": "a@b.ci", "subject": "S",
                 "body": "Bonjour, voici notre offre."})).pending_id
    # Alerte sortante au valideur du pôle, une seule fois
    assert rt.wa_validation.notify() == 1
    assert rt.wa_validation.notify() == 0
    sent = [c for c in wa.calls if c[1].endswith("/messages")]
    assert len(sent) == 1

    assert rt.wa_validation.handle(inbound(f"OK {pid}")) is True
    with rt.sessions() as s:
        pa = s.get(PendingAction, pid)
    assert pa.status == "executed" and "(WhatsApp)" in pa.decided_by
    assert connector.sent[-1]["body"].startswith("Bonjour, voici")


def test_whatsapp_validation_is_limited_to_own_poles(rt, wa, accounts, connector):
    awa = accounts.by_email("awa@ibig.test")
    accounts.set_phone(awa.id, "2250711111111")
    pid = rt.governor.submit(ActionRequest(
        agent="messagerie", action_type="mail.reply", channel="mail", pole="MARKET",
        title="Autre pôle", payload={"mailbox": connector.mailbox.adresse, "to": "a@b.ci",
                                     "subject": "S", "body": "B"})).pending_id
    assert "introuvable" in rt.wa_validation.decide(awa, f"OK {pid}")
    with rt.sessions() as s:
        assert s.get(PendingAction, pid).status == "pending"
    # Un numéro inconnu suit le parcours client habituel
    assert rt.wa_validation.handle(inbound("OK 1", sender="2250799999999")) is False


def test_admin_sets_validator_phone(rt, accounts):
    c = login(rt, "admin@ibig.test")
    awa = accounts.by_email("awa@ibig.test")
    r = c.post(f"/utilisateurs/{awa.id}/telephone", data={"telephone": "+225 07 11 11 11 11"},
               follow_redirects=False)
    assert "+2250711111111" in unquote(r.headers["location"])
    assert accounts.by_phone("2250711111111").email == "awa@ibig.test"
    r = c.post(f"/utilisateurs/{awa.id}/telephone", data={"telephone": "abc"},
               follow_redirects=False)
    assert "Refusé" in unquote(r.headers["location"])


def test_accounts_channels_and_whatsapp_numbers_from_dashboard(rt, accounts, meta):
    c = login(rt, "dg@ibig.test")
    r = c.post("/reseaux/comptes", data={"reseau": "whatsapp_chaine",
                                         "compte": "Chaîne IBIG SOFT", "pole": "SOFT"},
               follow_redirects=False)
    assert "à publier à la main" in unquote(r.headers["location"])
    assert any(a.compte == "Chaîne IBIG SOFT" and not a.publication_auto
               for a in rt.org.social_accounts)
    c.post("/reseaux/comptes", data={"reseau": "facebook_page", "compte": "IBIG Digital",
                                     "pole": "DIGITAL"})
    assert any(a.compte == "IBIG Digital" and a.publication_auto
               for a in rt.org.social_accounts)
    # Un compte de canaux.yaml ne se retire pas d'ici
    r = c.post("/reseaux/comptes/retirer", data={"reseau": "facebook_page",
                                                 "compte": "IBIG Soft"},
               follow_redirects=False)
    assert "Refusé" in unquote(r.headers["location"])
    c.post("/reseaux/comptes/retirer", data={"reseau": "facebook_page",
                                             "compte": "IBIG Digital"})
    assert not any(a.compte == "IBIG Digital" for a in rt.org.social_accounts)

    r = c.post("/reseaux/whatsapp", data={"nom": "EDUFORM accueil", "numero": "+225 07 12 34 56 78",
                                          "phone_number_id": "1098", "pole": "EDUFORM",
                                          "jeton": "jeton-secret"}, follow_redirects=False)
    assert "+2250712345678" in unquote(r.headers["location"])
    client = rt.whatsapp_clients["1098"]
    assert client.number.token() == "jeton-secret"
    assert "jeton-secret" not in c.get("/reseaux").text
    c.post("/reseaux/whatsapp/retirer", data={"phone_number_id": "1098"})
    assert "1098" not in rt.whatsapp_clients
    assert login(rt, "awa@ibig.test").post("/reseaux/whatsapp", data={
        "nom": "x", "numero": "2250700000000", "phone_number_id": "1", "pole": "SOFT",
        "jeton": "t"}).status_code == 403


def test_calendar_shows_real_publication_state(rt, meta, accounts):
    from datetime import timedelta

    connect(rt)
    day = (datetime.now(UTC).date() + timedelta(days=7)).isoformat()
    out = validated_post(rt, day=day)  # programmée à 10 h le jour prévu
    page = login(rt, "dg@ibig.test").get(f"/calendrier?semaine={day}").text
    assert "Nouvelle version de Scolaby" in page and "programmée" in page
    assert "Page Facebook" in page
    publish_due(rt.governor, rt.sessions, now=LATER)
    page = login(rt, "dg@ibig.test").get(f"/calendrier?semaine={day}").text
    assert "publiée" in page
    assert out.result["mode"] == "programme"
