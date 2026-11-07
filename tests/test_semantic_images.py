"""Recherche par le sens (Voyage AI + pgvector) et photos générées par IA (section 14)."""

import base64
import io
import os
from urllib.parse import unquote

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text

from ibig_agent.auth import UserStore
from ibig_agent.configstore import set_service_value
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import AIUsage, MediaAsset, open_db
from ibig_agent.governance import ActionRequest
from ibig_agent.images import OpenAIImages
from ibig_agent.knowledge import KnowledgeBase
from ibig_agent.semantic import SemanticIndex, VoyageClient

PASSWORD = "mot-de-passe-solide"
GUIDE = """---
titre: Guide Scolaby
pole: SOFT
type: guide
---
## Tarifs de l'abonnement
L'abonnement Scolaby est facturé 25 000 FCFA par mois et par établissement.

## Installation sur un ordinateur
Téléchargez l'installateur depuis votre espace client puis lancez-le.
"""
# Concepts : des mots différents qui veulent dire la même chose
CONCEPTS = [("tarif", "tarifs", "prix", "coûte", "coute", "combien", "facturé"),
            ("installation", "installer", "installateur", "télécharger", "téléchargez"),
            ("mot", "passe", "connexion")]


class FakeVoyage:
    def __init__(self):
        self.calls, self.fail = [], False

    def vector(self, s):
        words = s.lower().replace("?", " ").replace("'", " ").split()
        v = [0.05] + [float(sum(w in c for w in words)) for c in CONCEPTS]
        return v + [0.0] * (1024 - len(v))

    def handler(self, request):
        import json

        body = json.loads(request.content)
        self.calls.append((body["input_type"], len(body["input"])))
        if self.fail:
            return httpx.Response(503, json={"detail": "indisponible"})
        return httpx.Response(200, json={
            "data": [{"index": i, "embedding": self.vector(t)}
                     for i, t in enumerate(body["input"])],
            "usage": {"total_tokens": 10 * len(body["input"])}})

    def client(self, key):
        return VoyageClient(key, httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def voyage():
    return FakeVoyage()


def make_kb(tmp_path, body=GUIDE):
    root = tmp_path / "kb"
    (root / "guides").mkdir(parents=True, exist_ok=True)
    (root / "guides" / "scolaby.md").write_text(body, encoding="utf-8")
    return KnowledgeBase(root)


def test_meaning_finds_passage_without_common_words(rt, tmp_path, voyage):
    kb = make_kb(tmp_path)
    question = "Combien ça coûte ?"
    assert kb.search_passages(question, "SOFT") == []  # aucun mot en commun
    index = SemanticIndex(rt.sessions, rt.settings, voyage.client)
    assert index.sync(kb) == {"actif": False}  # pas de clé : mots-clés seuls
    set_service_value(rt.sessions, rt.settings, "voyage_api_key", "pa-cle", "t", secret=True)
    out = index.sync(kb)
    assert out["indexes"] == 2 and out["pgvector"] is False
    kb.semantic = index.scores
    found = kb.search_passages(question, "SOFT")
    assert found and found[0].heading == "Tarifs de l'abonnement"
    assert kb.search(question, "SOFT")[0].titre == "Guide Scolaby"
    with rt.sessions() as s:
        assert s.query(AIUsage).filter_by(purpose="kb.index").count() == 1


def test_index_is_incremental(rt, tmp_path, voyage):
    set_service_value(rt.sessions, rt.settings, "voyage_api_key", "pa-cle", "t", secret=True)
    index = SemanticIndex(rt.sessions, rt.settings, voyage.client)
    index.sync(make_kb(tmp_path))
    assert index.sync(make_kb(tmp_path))["indexes"] == 0
    changed = GUIDE.replace("25 000", "30 000")
    out = index.sync(make_kb(tmp_path, changed))
    assert out["indexes"] == 1 and index.count() == 2


def test_voyage_outage_falls_back_to_keywords(rt, tmp_path, voyage):
    set_service_value(rt.sessions, rt.settings, "voyage_api_key", "pa-cle", "t", secret=True)
    kb = make_kb(tmp_path)
    index = SemanticIndex(rt.sessions, rt.settings, voyage.client)
    index.sync(kb)
    kb.semantic = index.scores
    voyage.fail = True
    assert kb.search_passages("installation ordinateur", "SOFT")[0].heading.startswith("Instal")
    assert "503" in index.last_error


@pytest.mark.skipif(not os.environ.get("IBIG_TEST_POSTGRES_URL"),
                    reason="IBIG_TEST_POSTGRES_URL non défini")
def test_pgvector_search(rt, tmp_path, voyage):
    engine = create_engine(os.environ["IBIG_TEST_POSTGRES_URL"])
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        conn.exec_driver_sql("CREATE SCHEMA public")
    sessions = open_db(engine)
    set_service_value(sessions, rt.settings, "voyage_api_key", "pa-cle", "t", secret=True)
    index = SemanticIndex(sessions, rt.settings, voyage.client)
    if not index.pgvector():
        pytest.skip("extension pgvector absente")
    kb = make_kb(tmp_path)
    assert index.sync(kb)["pgvector"] is True
    with sessions() as s:
        assert s.execute(text("SELECT count(*) FROM kb_embeddings WHERE vec IS NOT NULL")
                         ).scalar() == 2
    kb.semantic = index.scores
    assert kb.search_passages("Combien ça coûte ?", "SOFT")[0].heading.startswith("Tarifs")
    engine.dispose()


def test_runtime_uses_semantic_index(rt):
    assert rt.kb.semantic == rt.semantic.scores
    assert rt.index_knowledge() == {"actif": False}


# ------------------------------------------------------------------ photos
def fake_png():
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1536), (180, 120, 60)).save(buf, "PNG")
    return buf.getvalue()


class FakeOpenAI:
    def __init__(self):
        self.prompts = []

    def handler(self, request):
        import json

        if request.method == "GET":
            return httpx.Response(200, json={"id": "gpt-image-1"})
        body = json.loads(request.content)
        self.prompts.append(body)
        return httpx.Response(200, json={"data": [
            {"b64_json": base64.b64encode(fake_png()).decode()}]})

    def factory(self, key, model="gpt-image-1"):
        return OpenAIImages(key, model, httpx.Client(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def accounts(rt):
    store = UserStore(rt.sessions, rt.org)
    store.create("awa@ibig.test", "Awa", "valideur", PASSWORD)
    store.create("kofi@ibig.test", "Kofi", "valideur", PASSWORD)
    store.create("dg@ibig.test", "Direction", "direction", PASSWORD)
    return store


def login(rt, email):
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": email, "password": PASSWORD})
    return c


def social_post(rt, reseau="instagram"):
    return rt.governor.submit(ActionRequest(
        agent="communication", action_type="social.post", channel="instagram",
        account="@ib_inter_officiel", pole="SOFT",
        title=f"Lundi 05/10 · {reseau} · Rentrée scolaire avec Scolaby",
        payload={"reseau": reseau, "compte": "@ib_inter_officiel", "date": "2099-10-05",
                 "texte": "La rentrée approche.", "brief_visuel": "Directrice d'école "
                 "souriante devant un ordinateur"})).pending_id


def test_photo_generated_on_request_and_used_in_visual(rt, accounts):
    fake = FakeOpenAI()
    rt.image_factory = fake.factory
    pid = social_post(rt)
    c = login(rt, "awa@ibig.test")
    assert "clé OpenAI" not in c.get("/validations").text  # conseil réservé à la direction
    r = c.post(f"/validations/{pid}/photo", follow_redirects=False)
    assert "Aucune clé OpenAI" in unquote(r.headers["location"])

    set_service_value(rt.sessions, rt.settings, "openai_api_key", "sk-cle", "t", secret=True)
    assert "Générer une photo" in c.get("/validations").text
    r = c.post(f"/validations/{pid}/photo", follow_redirects=False)
    assert "Photo générée" in unquote(r.headers["location"])
    prompt = fake.prompts[0]
    assert prompt["size"] == "1024x1536" and "aucun texte" in prompt["prompt"]
    assert "Directrice d'école" in prompt["prompt"]
    with rt.sessions() as s:
        asset = s.query(MediaAsset).filter_by(pending_id=pid).one()
        assert asset.mime == "image/jpeg" and asset.data[:2] == b"\xff\xd8"
        assert s.query(AIUsage).filter_by(purpose="image.photo").count() == 1
    page = c.get("/validations").text
    assert f"/visuel/{pid}.png?p={asset.id}" in page and "Retirer la photo" in page
    img = c.get(f"/visuel/{pid}.png")
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    assert Image.open(io.BytesIO(img.content)).size == (1080, 1350)

    # Un valideur d'un autre pôle ne génère rien
    k = login(rt, "kofi@ibig.test")
    r = k.post(f"/validations/{pid}/photo", follow_redirects=False)
    assert "Refusé" in unquote(r.headers["location"]) and len(fake.prompts) == 1

    c.post(f"/validations/{pid}/photo/retirer")
    with rt.sessions() as s:
        assert s.query(MediaAsset).filter_by(pending_id=pid).count() == 0


def test_photo_respects_monthly_budget(rt, accounts):
    fake = FakeOpenAI()
    rt.image_factory = fake.factory
    set_service_value(rt.sessions, rt.settings, "openai_api_key", "sk-cle", "t", secret=True)
    with rt.sessions() as s:
        s.add(AIUsage(model="claude", purpose="x", cost_usd=rt.settings.monthly_ai_budget_usd))
        s.commit()
    pid = social_post(rt)
    r = login(rt, "dg@ibig.test").post(f"/validations/{pid}/photo", follow_redirects=False)
    assert "Plafond" in unquote(r.headers["location"]) and fake.prompts == []


def test_services_page_configures_voyage_and_openai(rt, accounts, voyage):
    fake = FakeOpenAI()
    rt.image_factory = fake.factory
    rt.semantic.client_factory = voyage.client
    c = login(rt, "dg@ibig.test")
    page = c.get("/services").text
    assert "Voyage AI" in page and "Photos par IA" in page
    r = c.post("/services/voyage", data={"cle": "pa-secret"}, follow_redirects=False)
    assert "passages indexés" in unquote(r.headers["location"])
    assert rt.semantic.count() > 10
    page = c.get("/services").text
    assert "pa-secret" not in page and "passages indexés" in page
    c.post("/services/openai", data={"cle": "sk-secret", "modele": "gpt-image-1-mini"})
    r = c.post("/services/openai/tester", follow_redirects=False)
    assert "gpt-image-1-mini" in unquote(r.headers["location"])
    assert login(rt, "awa@ibig.test").get("/services").status_code == 403
