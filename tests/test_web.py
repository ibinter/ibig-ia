import json
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from ibig_agent.channels.web import (
    PhpEndpointConnector,
    StaticExportConnector,
    WordPressConnector,
    sanitize_html,
    sign,
    slugify,
)
from ibig_agent.config import Site

ROOT = Path(__file__).resolve().parents[1]
ARTICLE = {"titre": "Gérer son école <sans> papier", "slug": "gerer-son-ecole",
           "contenu_html": "<h2>Intro</h2><p>Texte</p>", "meta_description": "Résumé",
           "mot_cle": "gestion scolaire"}


# ------------------------------------------------------------------ nettoyage
def test_sanitizer_removes_active_content():
    dirty = ('<h2 onclick="x()">Titre</h2><script>alert(1)</script><p>Bon <b>texte</b>'
             '<a href="javascript:alert(1)">piège</a> <a href="https://ibigsoft.com" '
             'onmouseover="x">lien</a></p><iframe src="https://x"></iframe><style>p{}</style>'
             '<img src=x onerror=alert(1)><h1>Titre 1</h1><ul><li>point')
    clean = sanitize_html(dirty)
    for bad in ("script", "alert", "onclick", "onmouseover", "iframe", "style", "<img",
                "javascript", "<h1"):
        assert bad not in clean
    assert '<a href="https://ibigsoft.com">lien</a>' in clean
    assert "<h2>Titre</h2>" in clean and "piège" in clean
    assert clean.endswith("</li></ul>")  # balises non fermées refermées


def test_slugify():
    assert slugify("Gérer son école : 5 conseils !") == "gerer-son-ecole-5-conseils"


# ------------------------------------------------------------------ WordPress
def wp_site():
    return Site(nom="WP", url="https://wp.test", pole="SOFT", technologie="wordpress",
                wp_user="agent-ia", wp_password_env="WP_TEST_PWD", actif=True)


def test_wordpress_creates_draft_only(monkeypatch):
    monkeypatch.setenv("WP_TEST_PWD", "app pass word")
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 42, "status": "draft", "link": "https://wp.test/?p=42"})

    conn = WordPressConnector(wp_site(), httpx.Client(transport=httpx.MockTransport(handler)))
    assert conn.create_draft(ARTICLE) == {"mode": "wordpress", "id": 42,
                                          "lien": "https://wp.test/?p=42"}
    assert seen["url"] == "https://wp.test/wp-json/wp/v2/posts"
    assert seen["auth"].startswith("Basic ")
    assert seen["body"]["status"] == "draft"


def test_wordpress_errors_are_raised(monkeypatch):
    monkeypatch.setenv("WP_TEST_PWD", "x")
    for resp in (httpx.Response(401, json={"code": "rest_cannot_create"}),
                 httpx.Response(201, json={"id": 1, "status": "publish"})):
        conn = WordPressConnector(wp_site(), httpx.Client(
            transport=httpx.MockTransport(lambda r, resp=resp: resp)))
        with pytest.raises(RuntimeError):
            conn.create_draft(ARTICLE)


def test_missing_secret_is_explicit():
    with pytest.raises(RuntimeError, match="Secret manquant"):
        wp_site().secret()


# ------------------------------------------------------------------ statique
def test_static_export(tmp_path):
    site = Site(nom="Site Statique", url="https://s.test", pole="SOFT", technologie="statique",
                export_dir=str(tmp_path))
    out = StaticExportConnector(site).create_draft(ARTICLE)
    page = Path(out["fichier"]).read_text(encoding="utf-8")
    assert "<h1>Gérer son école &lt;sans&gt; papier</h1>" in page
    assert "<h2>Intro</h2>" in page and "à relire" in page


# ------------------------------------------------------------------ module PHP réel
SECRET = "0123456789abcdef0123456789abcdef"


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def php_inbox(tmp_path):
    if not shutil.which("php"):
        pytest.skip("PHP non installé")
    webroot = tmp_path / "www"
    webroot.mkdir()
    shutil.copy(ROOT / "integrations/php/ibig-article-inbox.php", webroot)
    drafts = tmp_path / "brouillons"
    config = tmp_path / "config.php"
    config.write_text(f"<?php return ['secret' => '{SECRET}', 'drafts_dir' => '{drafts}'];")
    port = _free_port()
    proc = subprocess.Popen(
        ["php", "-S", f"127.0.0.1:{port}", "-t", str(webroot)],
        env={"IBIG_INBOX_CONFIG": str(config), "PATH": "/usr/bin:/bin"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/ibig-article-inbox.php"
    for _ in range(50):
        try:
            httpx.get(url, timeout=0.2)
            break
        except httpx.TransportError:
            time.sleep(0.1)
    yield url, drafts
    proc.terminate()
    proc.wait(5)


def post(url, body: bytes, ts=None, secret=SECRET, signature=None):
    ts = ts or str(int(time.time()))
    return httpx.post(url, content=body, headers={
        "Content-Type": "application/json", "X-IBIG-Timestamp": ts,
        "X-IBIG-Signature": signature or sign(secret, ts, body)})


def test_php_module_end_to_end(php_inbox, monkeypatch):
    url, drafts = php_inbox
    monkeypatch.setenv("PHP_TEST_SECRET", SECRET)
    site = Site(nom="PHP", url="https://php.test", pole="SOFT", technologie="php",
                endpoint_url=url, secret_env="PHP_TEST_SECRET")
    article = {**ARTICLE, "contenu_html": "<p>JavaScript : un langage du web.</p>"}
    out = PhpEndpointConnector(site).create_draft(article)
    assert out["mode"] == "php" and out["statut"] == "brouillon"
    (saved,) = drafts.glob("*.json")
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["titre"] == ARTICLE["titre"] and data["statut"] == "brouillon"


def test_php_module_rejects_bad_requests(php_inbox):
    url, _ = php_inbox
    body = json.dumps(ARTICLE).encode()
    assert post(url, body, secret="mauvais-secret-mauvais-secret-xx").status_code == 401
    assert post(url, body, ts=str(int(time.time()) - 3600)).status_code == 401
    ok = post(url, body)
    assert ok.status_code == 201
    replay = httpx.post(url, content=body, headers={
        "Content-Type": "application/json", "X-IBIG-Timestamp": ok.request.headers[
            "X-IBIG-Timestamp"], "X-IBIG-Signature": ok.request.headers["X-IBIG-Signature"]})
    assert replay.status_code == 409
    evil = json.dumps({**ARTICLE, "contenu_html": '<p onclick="x()">a</p>'}).encode()
    assert post(url, evil).status_code == 422
    assert post(url, json.dumps({"titre": "x"}).encode()).status_code == 400
    assert httpx.get(url).status_code == 405


# ------------------------------------------------------------------ diagnostics
def test_wordpress_check_enforces_least_privilege(monkeypatch):
    monkeypatch.setenv("WP_TEST_PWD", "x")
    for roles, ok in ((["author"], True), (["administrator"], False), (["editor"], False),
                      (["subscriber"], False)):
        conn = WordPressConnector(wp_site(), httpx.Client(transport=httpx.MockTransport(
            lambda r, roles=roles: httpx.Response(200, json={"roles": roles}))))
        if ok:
            assert "author" in conn.check()
        else:
            with pytest.raises(RuntimeError):
                conn.check()


def test_php_check_without_creating_draft(php_inbox, monkeypatch):
    url, drafts = php_inbox
    site = Site(nom="PHP", url="https://php.test", pole="SOFT", technologie="php",
                endpoint_url=url, secret_env="PHP_TEST_SECRET")
    monkeypatch.setenv("PHP_TEST_SECRET", SECRET)
    assert "signature acceptée" in PhpEndpointConnector(site).check()
    assert not list(drafts.glob("*.json"))
    monkeypatch.setenv("PHP_TEST_SECRET", "autre-secret-autre-secret-autre-s")
    with pytest.raises(RuntimeError, match="signature refusée"):
        PhpEndpointConnector(site).check()


def test_static_check(tmp_path):
    site = Site(nom="S", url="https://s.test", pole="SOFT", technologie="statique",
                export_dir=str(tmp_path / "x"))
    assert "écriture" in StaticExportConnector(site).check()
