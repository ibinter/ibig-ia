"""Configuration depuis le tableau de bord : coffre, base de connaissances, boîtes,
objectifs de la direction et plan de la semaine."""

from datetime import date
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from ibig_agent.auth import UserStore
from ibig_agent.configstore import directives_text
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JournalEntry
from ibig_agent.vault import VaultError, decrypt, encrypt

PASSWORD = "mot-de-passe-solide"


@pytest.fixture
def dg(rt):
    store = UserStore(rt.sessions, rt.org)
    store.create("dg@ibig.test", "Direction", "direction", PASSWORD)
    store.create("awa@ibig.test", "Awa", "valideur", PASSWORD)
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": PASSWORD})
    return c


def login(rt, email):
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": email, "password": PASSWORD})
    return c


def test_vault_round_trip_and_wrong_key():
    token = encrypt("k" * 40, "secret-imap")
    assert "secret-imap" not in token and decrypt("k" * 40, token) == "secret-imap"
    with pytest.raises(VaultError):
        decrypt("z" * 40, token)


def test_pole_sheet_completed_in_dashboard_unlocks_the_pole(rt, dg):
    path = next(d.path for d in rt.kb.documents if d.type == "fiche_pole" and d.pole == "SOFT")
    assert "À COMPLÉTER" in dg.get(f"/connaissances/document?chemin={path}").text
    body = "## Identité\nIBIG SOFT édite 14 solutions.\n\n## Offres\n- IBIG School : sur devis\n"
    r = dg.post("/connaissances/document", data={"chemin": path, "contenu": body, "valide": "1"},
                follow_redirects=False)
    assert "document complet" in unquote(r.headers["location"])
    assert rt.communication.pole_ready("SOFT")
    assert "IBIG School : sur devis" in rt.kb.context_for("SOFT")
    # Un « À COMPLÉTER » restant empêche la validation, même cochée
    dg.post("/connaissances/document", data={"chemin": path, "valide": "1",
                                              "contenu": body + "Prix : À COMPLÉTER\n"})
    assert not rt.communication.pole_ready("SOFT")
    dg.post("/connaissances/restaurer", data={"chemin": path})
    assert path not in rt.kb.overrides


def test_validator_cannot_edit_knowledge_or_mailboxes(rt, dg):
    awa = login(rt, "awa@ibig.test")
    path = rt.kb.documents[0].path
    assert awa.post("/connaissances/document",
                    data={"chemin": path, "contenu": "x"}).status_code == 403
    assert awa.get("/boites").status_code == 403


def test_path_traversal_is_refused(dg):
    assert dg.get("/connaissances/document?chemin=../config/poles.yaml").status_code == 404


def test_new_support_guide(rt, dg):
    dg.post("/connaissances/guide", data={"titre": "IBIG School : inscrire un élève",
                                          "pole": "SOFT"})
    guide = next(d for d in rt.kb.documents if d.type == "guide")
    assert guide.pole == "SOFT" and guide.meta["reponses_auto"] is False


def test_mailbox_added_from_dashboard_is_encrypted_and_live(rt, dg, connector):
    from ibig_agent.db import MailboxAccount

    rt.connector_factory = lambda box: type(connector)(box)
    r = dg.post("/boites", data={"adresse": "Info@IBIGSOFT.com", "hebergeur": "lws",
                                 "pole": "SOFT", "secret": "motdepasse-imap"},
                follow_redirects=False)
    assert "raccordée" in unquote(r.headers["location"])
    with rt.sessions() as s:
        row = s.get(MailboxAccount, "info@ibigsoft.com")
    assert "motdepasse-imap" not in row.secret_enc and row.imap_host == "mail.lws-hosting.com"
    box = rt.connectors["info@ibigsoft.com"].mailbox
    assert box.secret() == "motdepasse-imap" and box.pole == "SOFT"
    assert "motdepasse-imap" not in dg.get("/boites").text
    r = dg.post("/boites/tester", data={"adresse": "info@ibigsoft.com"}, follow_redirects=False)
    assert "info@ibigsoft.com :" in unquote(r.headers["location"])
    dg.post("/boites/retirer", data={"adresse": "info@ibigsoft.com"})
    assert "info@ibigsoft.com" not in rt.connectors
    assert all(m.adresse != "info@ibigsoft.com" for m in rt.org.mailboxes)


def test_objectives_reach_the_writing_agents_and_the_weekly_plan(rt, dg, llm):
    dg.post("/objectifs", data={"texte": "Pousser la rentrée IBIG School", "pole": "SOFT"})
    dg.post("/objectifs", data={"texte": "Salon de l'immobilier", "pole": "IMMOTRUST"})
    assert "rentrée IBIG School" in directives_text(rt.sessions, "SOFT")
    assert "Salon" not in directives_text(rt.sessions, "SOFT")
    text = rt.planner.run(date(2026, 10, 5))
    assert text == llm.draft
    with rt.sessions() as s:
        plan = s.query(JournalEntry).filter(JournalEntry.summary.like("Plan de la semaine%")).one()
    assert plan.status == "executed"
    assert "Pousser la rentrée" in dg.get("/objectifs").text


def test_editorial_calendar_shows_posts_on_their_day(rt, dg):
    from ibig_agent.config import SocialAccount

    account = SocialAccount(reseau="linkedin", compte="IBIG Soft", pole="SOFT",
                            publication_auto=False)
    rt.communication.write_post("SOFT", account, "Rentrée scolaire", date(2026, 10, 7))
    page = dg.get("/calendrier?semaine=2026-10-05").text
    assert "Rentrée scolaire" in page and "à valider" in page
    assert "Rentrée scolaire" not in dg.get("/calendrier?semaine=2026-10-12").text


def test_best_posts_shape_style_but_never_validate_facts(rt, dg):
    text = "Rentrée : IBIG School à 9 999 FCFA seulement ! Écrivez à promo@ibig.test"
    dg.post("/publications", data={"texte": text, "pole": "SOFT", "reseau": "facebook_page",
                                   "resultats": "300 likes"})
    assert "IBIG School à 9 999 FCFA" in dg.get("/publications").text
    examples = rt.kb.style_examples("SOFT", "facebook_page")
    assert "9 999 FCFA" in examples and "ne font pas foi" in examples
    kinds = {i.kind for i in rt.kb.verify_facts(text)}
    assert {"prix", "email"} <= kinds  # une ancienne publication n'est pas une source
    path = next(d.path for d in rt.kb.documents if d.type == "publication")
    dg.post("/publications/retirer", data={"chemin": path})
    assert rt.kb.style_examples("SOFT") == ""


def test_branded_visual_for_each_post(rt, dg):
    import xml.etree.ElementTree as ET

    from ibig_agent.config import SocialAccount
    from ibig_agent.dashboard.visuals import render_svg

    account = SocialAccount(reseau="linkedin", compte="IBIG Soft", pole="SOFT",
                            publication_auto=False)
    pid = rt.communication.write_post("SOFT", account, "Rentrée <scolaire> & IBIG", date(2026, 10, 7))
    r = dg.get(f"/visuel/{pid}.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    root = ET.fromstring(r.text)  # XML valide, texte échappé
    assert root.get("width") == "1200" and root.get("height") == "675"  # format LinkedIn
    assert "Rentrée &lt;scolaire&gt;" in r.text
    assert "Visuel aux couleurs IBIG" in dg.get("/validations").text
    assert login(rt, "awa@ibig.test").get(f"/visuel/{pid}.svg").status_code == 200  # SOFT
    tall = ET.fromstring(render_svg("Titre", "Message", "EDUFORM", "tiktok"))
    assert tall.get("height") == "1920"


def test_bulk_mailboxes_are_added_encrypted(rt):
    from urllib.parse import unquote

    from fastapi.testclient import TestClient

    from ibig_agent.auth import UserStore
    from ibig_agent.dashboard.app import create_app
    from ibig_agent.db import MailboxAccount

    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "Direction", "direction",
                                          "mot-de-passe-solide")
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-solide"})
    lignes = ("a@ibigsoft.com ; secret-a\n"
              "b@ibig-eduform.com\tsecret;b\tEDUFORM\n"
              "pas-une-adresse ; x\n"
              "c@ibigsoft.com\n")
    r = c.post("/boites/lot", data={"lignes": lignes, "pole": "SOFT"},
               follow_redirects=False)
    msg = unquote(r.headers["location"])
    assert "2 boîte(s) raccordée(s)" in msg and "ligne 3" in msg and "ligne 4" in msg
    assert "secret" not in msg
    with rt.sessions() as s:
        rows = {m.adresse: m for m in s.query(MailboxAccount).all()}
    assert rows["a@ibigsoft.com"].pole == "SOFT"
    # Collé depuis Excel (tabulations) : un « ; » dans le mot de passe est conservé
    assert rows["b@ibig-eduform.com"].pole == "EDUFORM"
    assert "secret-a" not in rows["a@ibigsoft.com"].secret_enc
    assert "a@ibigsoft.com" in rt.connectors
