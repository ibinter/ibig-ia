"""Lot 3 — Prospecteur : lecture des sources, déduplication, score explicable, mission."""

import httpx
from sqlalchemy import select

from ibig_agent.db import Mission, Prospect, ProspectActivity
from ibig_agent.prospecteur import make_runner, parse_csv, score

CSV = """Nom;Entreprise;Email;Téléphone;Ville;Lien
Awa Traoré;Pharma Cocody;awa@pharmacocody.ci;0102030405;Abidjan;https://fb.com/pharmacocody
Doublon;Pharma Cocody;AWA@pharmacocody.ci;;;
Koffi;;koffi@gmail.com;;;
Sans mail;Truc;;;;
;;noreply@societe.ci;;;
"""


def mission(rt, **kw):
    data = {"name": "Pharmacies Abidjan", "kind": "prospection", "pole": "SOFT", "zone": "Abidjan",
            "target": "pharma", "objective": "Trouver des pharmacies", "agent": "prospecteur",
            "autonomy": "validation", "channel": "", "budget_usd": "5", **kw}
    mid = rt.missions.create(data, "Awa")
    with rt.sessions() as s:
        return s.get(Mission, mid)


def test_csv_columns_are_recognised_and_noise_dropped():
    rows = parse_csv(CSV)
    assert [r.email for r in rows] == ["awa@pharmacocody.ci", "awa@pharmacocody.ci",
                                       "koffi@gmail.com"]
    assert rows[0].company == "Pharma Cocody" and rows[0].origin_url.startswith("https://fb")


def test_score_is_explained_and_rewards_fit(rt):
    m = mission(rt)
    rows = parse_csv(CSV)
    good, weak = score(rows[0], m), score(rows[2], m)
    assert good.score > weak.score
    assert any("cible" in r for r in good.reasons)
    assert "personnelle" in " ".join(weak.reasons)


def test_runner_creates_cards_without_duplicates_and_never_contacts(rt, tmp_path):
    m = mission(rt)
    (tmp_path / f"mission_{m.id}.csv").write_text(CSV, encoding="utf-8")
    run = make_runner(rt.sessions, tmp_path)
    result = run(m)
    assert result.ok and "2 fiche(s) créée(s)" in result.summary
    with rt.sessions() as s:
        rows = s.scalars(select(Prospect)).all()
        acts = s.scalars(select(ProspectActivity)).all()
    assert {p.email for p in rows} == {"awa@pharmacocody.ci", "koffi@gmail.com"}
    assert all(p.assigned_agent == "prospecteur" and p.collected_at for p in rows)
    assert len(acts) == 2 and "score" in acts[0].summary
    assert not (tmp_path / f"mission_{m.id}.csv").exists()      # fichier traité, pas retraité
    again = run(m)
    assert again.ok and "Aucune" in again.summary


def test_existing_crm_contacts_are_not_duplicated(rt, tmp_path):
    with rt.sessions() as s:
        s.add(Prospect(email="awa@pharmacocody.ci", name="Déjà là"))
        s.commit()
    m = mission(rt)
    (tmp_path / f"mission_{m.id}.csv").write_text(CSV, encoding="utf-8")
    result = make_runner(rt.sessions, tmp_path)(m)
    assert "1 fiche(s) créée(s)" in result.summary
    with rt.sessions() as s:
        assert s.scalar(select(Prospect.name).where(Prospect.email == "awa@pharmacocody.ci")) \
            == "Déjà là"


def test_public_page_read_only_when_robots_allows(rt, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /prive")
        return httpx.Response(200, text="Contact : <a>info@societe.ci</a> logo@x.png")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ok = mission(rt, target="https://annuaire.ci/pharmacies")
    result = make_runner(rt.sessions, tmp_path, client)(ok)
    assert result.ok and result.done and "1 fiche(s)" in result.summary
    blocked = mission(rt, name="Privé", target="https://annuaire.ci/prive/liste")
    result = make_runner(rt.sessions, tmp_path, client)(blocked)
    assert result.ok and "robots.txt" in result.summary


def test_prospecting_mission_is_ready_to_launch_now(rt):
    m = mission(rt)
    checks = rt.missions.checks(rt.missions.get(m.id))
    assert next(c for c in checks if c.label == "Agent exécutant").ok
