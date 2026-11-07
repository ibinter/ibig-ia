"""Centre de contrôle de l'écosystème : inventaire, sondes, incidents, suggestions et page."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ibig_agent import ecosystem
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import EcoAsset, EcoCheck
from ibig_agent.ecosystem import EcosystemError, EcosystemService, parse_codes

NOW = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
YAML = """actifs:
  - {nom: "Site A", url: "https://a.exemple.ci", genre: site, hebergement: vps, pole: SOFT, criticite: haute}
  - {nom: "Démo B", url: "https://b.exemple.ci/", genre: logiciel, hebergement: ftp, criticite: basse}
  - {nom: "API C", url: "https://c.exemple.ci", genre: api, codes_ok: "200-404"}
  - {nom: "À confirmer", url: "https://d.exemple.ci", actif: false, notes: "DNS absent"}
"""


class Fake:
    """Faux Internet : code par domaine, corps de page et en-têtes configurables."""

    def __init__(self):
        self.status = {}
        self.body = {}
        self.headers = {"strict-transport-security": "max-age=1"}
        self.redirect_http = True
        self.files = {"/robots.txt": "User-agent: *", "/sitemap.xml": "<urlset/>"}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if request.url.scheme == "http":
            return httpx.Response(301 if self.redirect_http else 200, headers={"location": "https://x"})
        code = self.status.get(host, 200)
        if path in self.files and code < 500:
            ctype = "application/xml" if path.endswith("xml") else "text/plain"
            return httpx.Response(200, text=self.files[path], headers={"content-type": ctype})
        if path in ("/robots.txt", "/sitemap.xml"):
            return httpx.Response(404, text="<html>")
        return httpx.Response(code, text=self.body.get(host, "<html>IBIG accueil</html>"),
                              headers={"content-type": "text/html", **self.headers})


@pytest.fixture
def world(rt, tmp_path):
    fake = Fake()
    alerts = []
    svc = EcosystemService(rt.sessions, rt.settings, alert=lambda *a: alerts.append(a),
                           client=httpx.Client(transport=httpx.MockTransport(fake)),
                           cert_check=lambda host: 60)
    path = tmp_path / "ecosysteme.yaml"
    path.write_text(YAML, encoding="utf-8")
    svc.sync_config(path)
    return svc, fake, alerts


def site_a(svc):
    return next(a for a in svc.assets() if a.name == "Site A")


def test_codes_parsing():
    assert parse_codes("200-399") == (200, 399)
    assert parse_codes("404") == (404, 404)
    assert parse_codes("n'importe quoi") == (200, 399)


def test_inventory_import_is_idempotent_and_unconfirmed_assets_are_not_probed(rt, world, tmp_path):
    svc, _, _ = world
    assert len(svc.assets()) == 4
    again = svc.sync_config(tmp_path / "ecosysteme.yaml")
    assert again == {"created": 0, "updated": 4, "missing_file": False}
    assert svc.sync_config(tmp_path / "absent.yaml")["missing_file"] is True
    assert [a.name for a in svc.assets(active_only=True)] == ["API C", "Démo B", "Site A"]
    assert svc.run_probes(NOW)["checked"] == 3
    # le vrai fichier d'inventaire du dépôt est lisible et complet
    real = svc.sync_config(Path(rt.settings.config_dir) / "ecosysteme.yaml")
    assert real["missing_file"] is False and real["created"] >= 30


def test_an_isolated_failure_opens_nothing_two_in_a_row_open_a_critical_incident(world):
    svc, fake, alerts = world
    fake.status["a.exemple.ci"] = 503
    svc.run_probes(NOW)
    assert svc.incidents() == []                                # un seul raté : pas d'alerte
    svc.run_probes(NOW + timedelta(minutes=5))
    (inc,) = [i for i in svc.incidents() if i.kind == "dispo"]
    assert inc.severity == "critical" and "503" in inc.title
    assert "docker" in inc.suggestion and "CORRECTION" in inc.suggestion and "RISQUE" in inc.suggestion
    assert len(alerts) == 1 and "urgent" in alerts[0][1]
    svc.run_probes(NOW + timedelta(minutes=10))
    assert len(alerts) == 1 and svc.incidents()[0].count == 2   # pas de spam
    fake.status["a.exemple.ci"] = 200                           # retour à la normale : résolu seul
    svc.run_probes(NOW + timedelta(minutes=15))
    assert svc.incidents() == []


def test_low_criticality_never_raises_an_urgent_alert(world):
    svc, fake, alerts = world
    fake.status["b.exemple.ci"] = 500
    svc.run_probes(NOW)
    svc.run_probes(NOW + timedelta(minutes=5))
    (inc,) = [i for i in svc.incidents() if i.kind == "dispo"]
    assert inc.severity == "warning" and alerts == []
    assert "error_log" in inc.suggestion                          # hébergement FTP : erreur PHP


def test_an_api_root_404_is_normal_but_a_502_is_not(world):
    svc, fake, _ = world
    fake.status["c.exemple.ci"] = 404
    svc.run_probes(NOW)
    svc.run_probes(NOW + timedelta(minutes=5))
    assert svc.incidents() == []
    fake.status["c.exemple.ci"] = 502
    svc.run_probes(NOW + timedelta(minutes=10))
    svc.run_probes(NOW + timedelta(minutes=15))
    assert [i.kind for i in svc.incidents()] == ["dispo"]


def test_a_page_that_loses_its_expected_text_is_flagged_as_content_problem(rt, world):
    svc, fake, _ = world
    with rt.sessions() as s:
        s.query(EcoAsset).filter_by(name="Site A").one().expect_text = "IBIG"
        s.commit()
    fake.body["a.exemple.ci"] = "<html>Site en maintenance</html>"
    svc.run_probes(NOW)
    svc.run_probes(NOW + timedelta(minutes=5))
    (inc,) = [i for i in svc.incidents() if i.kind == "contenu"]
    assert inc.severity == "critical" and "piratée" in inc.suggestion


def test_slow_runs_and_certificates_raise_their_own_incidents(world, monkeypatch):
    svc, _, _ = world
    monkeypatch.setattr(ecosystem, "SLOW_MS", {"haute": -1, "moyenne": -1, "basse": -1})
    for n in range(3):
        svc.run_probes(NOW + timedelta(minutes=5 * n))
    assert {i.kind for i in svc.incidents()} == {"vitesse"}
    monkeypatch.setattr(ecosystem, "SLOW_MS", {"haute": 9999, "moyenne": 9999, "basse": 9999})
    svc.cert_check = lambda host: 2
    svc.run_probes(NOW + timedelta(minutes=30))
    kinds = {(i.kind, i.severity) for i in svc.incidents()}
    assert ("cert", "critical") in kinds and ("cert", "warning") in kinds   # actif « haute » puis « basse »
    assert not any(k == "vitesse" for k, _ in kinds)                         # vitesse revenue à la normale


def test_hygiene_checks_https_redirect_hsts_robots_and_sitemap(world):
    svc, fake, _ = world
    fake.redirect_http, fake.headers, fake.files = False, {}, {}
    out = svc.run_hygiene(NOW)
    mine = {i.kind: i.severity for i in svc.incidents() if i.asset_id == site_a(svc).id}
    assert out["opened"] >= 4
    assert mine["http_redirect"] == "warning" and mine["hsts"] == "info" and "robots" in mine
    assert "RewriteRule" in next(i.suggestion for i in svc.incidents() if i.kind == "http_redirect")
    fake.redirect_http, fake.headers = True, {"strict-transport-security": "max-age=1"}
    fake.files = {"/robots.txt": "x", "/sitemap.xml": "<urlset/>"}
    svc.run_hygiene(NOW + timedelta(days=1))
    assert svc.incidents() == []


def test_summary_goes_from_unknown_to_normal_to_critical_and_briefing_follows(world):
    svc, fake, _ = world
    assert svc.summary(NOW)["level"] == "inconnu"
    svc.run_probes(NOW)
    data = svc.summary(NOW)
    assert data["level"] == "ok" and data["message"] == "Tout est normal"
    assert data["totals"]["suivis"] == 3 and data["totals"]["a_confirmer"] == 1
    assert data["totals"]["disponibilite"] == 100.0 and data["totals"]["cert_min"] == 60
    assert "Tout est normal" in svc.briefing(NOW)
    fake.status["a.exemple.ci"] = 500
    svc.run_probes(NOW + timedelta(minutes=5))
    svc.run_probes(NOW + timedelta(minutes=10))
    data = svc.summary(NOW + timedelta(minutes=10))
    assert data["level"] == "critique" and data["totals"]["critique"] == 1
    assert "Site A" in svc.briefing(NOW + timedelta(minutes=10))


def test_adding_and_importing_assets_validates_input(world):
    svc, _, _ = world
    with pytest.raises(EcosystemError, match="invalide"):
        svc.add_asset("x", "pas-une-adresse")
    with pytest.raises(EcosystemError, match="déjà"):
        svc.add_asset("Site A bis", "https://a.exemple.ci/")
    r = svc.import_text("https://nouveau.exemple.ci\nMon appli | https://appli.exemple.ci | ftp | SOFT | haute\n"
                        "# commentaire\nmauvaise ligne\nSite A | https://a.exemple.ci")
    assert r == {"added": 2, "rejected": 2}
    assert len(svc.assets()) == 6


def test_old_measurements_are_purged(rt, world):
    svc, _, _ = world
    svc.run_probes(NOW - timedelta(days=100))
    svc.run_probes(NOW)
    assert svc.purge(NOW) == 3
    with rt.sessions() as s:
        assert s.query(EcoCheck).filter(EcoCheck.at < NOW - timedelta(days=90)).count() == 0
        assert s.query(EcoCheck).count() == 3


def test_ecosystem_page_live_fragment_and_access(rt, world, monkeypatch):
    svc, fake, _ = world
    start = datetime.now(UTC)
    svc.run_probes(start)
    monkeypatch.setattr(type(rt), "ecosystem", property(lambda self: svc))
    users = UserStore(rt.sessions, rt.org)
    users.create("dg@ibig.test", "Direction", "direction", "mot-de-passe-solide")
    users.create("v@ibig.test", "Valideur", "valideur", "mot-de-passe-solide")

    def client(email):
        c = TestClient(create_app(rt), base_url="https://testserver")
        c.post("/login", data={"email": email, "password": "mot-de-passe-solide"})
        return c

    dg = client("dg@ibig.test")
    page = dg.get("/ecosysteme")
    assert page.status_code == 200 and "Tout est normal" in page.text and "Site A" in page.text
    assert 'href="/ecosysteme"' in dg.get("/").text
    live = dg.get("/ecosysteme/live")
    assert live.status_code == 200 and "<html" not in live.text and "Site A" in live.text
    fake.status["a.exemple.ci"] = 503
    svc.run_probes(start + timedelta(minutes=5))
    svc.run_probes(start + timedelta(minutes=10))
    live = dg.get("/ecosysteme/live").text
    assert "répond en erreur 503" in live and "CAUSE PROBABLE" in live
    iid = svc.incidents()[0].id
    dg.post(f"/ecosysteme/incident/{iid}/pris")
    assert svc.incidents()[0].status == "ack"
    done = dg.post("/ecosysteme/actif", data={"nom": "Nouveau", "url": "https://n.exemple.ci",
                                              "genre": "site", "hebergement": "ftp"},
                   follow_redirects=False)
    assert done.status_code == 303 and any(a.url == "https://n.exemple.ci" for a in svc.assets())
    v = client("v@ibig.test")
    assert v.get("/ecosysteme").status_code == 403 and v.get("/ecosysteme/live").status_code == 403
