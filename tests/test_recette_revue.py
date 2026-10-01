import csv
from datetime import timedelta

from fastapi.testclient import TestClient

from ibig_agent import recette
from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JournalEntry, ProcessedMessage, Ticket, utcnow
from ibig_agent.governance import ActionRequest


def seed_mails(rt, n=250):
    with rt.sessions() as s:
        s.add_all([ProcessedMessage(
            mailbox="contact@ibigsoft.com", message_id=f"<{i}@x>", sender=f"client{i}@x.ci",
            subject=f"Objet {i}", summary=f"Résumé {i}", pole="SOFT",
            category="support" if i % 2 else "prospect", decision="brouillon")
            for i in range(n)])
        s.commit()


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def write(path, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=recette.COLUMNS, delimiter=";")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ R-02
def test_sample_has_no_sender_address(rt, tmp_path):
    seed_mails(rt)
    path = tmp_path / "r02.csv"
    assert recette.write_sample(rt.sessions, path, 200, seed=1) == 200
    rows = read(path)
    assert len(rows) == 200 and list(rows[0]) == recette.COLUMNS
    assert "@x.ci" not in path.read_text(encoding="utf-8-sig")


def test_score_passes_at_95_percent(rt, tmp_path):
    seed_mails(rt)
    path = tmp_path / "r02.csv"
    recette.write_sample(rt.sessions, path, 200, seed=1)
    rows = read(path)
    for i, r in enumerate(rows):
        r["categorie_attendue"] = r["categorie_agent"]   # l'agent a juste…
        r["pole_attendu"] = "SOFT"
        if i < 9:                                        # … sauf 9 fois
            r["categorie_attendue"] = "client"
    write(path, rows)
    score = recette.score_file(path, rt.org.pole_codes)
    assert score.total == 200 and score.both_ok == 191 and score.passed
    assert "ATTEINT" in score.as_text() and "client" in score.as_text()
    rows[9]["categorie_attendue"] = "client"
    rows[10]["categorie_attendue"] = "client"
    write(path, rows)
    assert not recette.score_file(path, rt.org.pole_codes).passed  # 94,5 %


def test_score_rules(rt, tmp_path):
    path = tmp_path / "r02.csv"
    base = {"id": "1", "date": "", "boite": "b", "objet": "o", "resume": "",
            "pole_agent": "SOFT", "categorie_agent": "support", "commentaire": ""}
    write(path, [
        {**base, "pole_attendu": "", "categorie_attendue": ""},           # non remplie
        {**base, "id": "2", "pole_attendu": "SOFT", "categorie_attendue": ""},  # vide = juste
        {**base, "id": "3", "pole_attendu": "XYZ", "categorie_attendue": "support"},
    ])
    score = recette.score_file(path, rt.org.pole_codes)
    assert (score.total, score.both_ok, score.ignored, len(score.invalid)) == (1, 1, 1, 1)
    assert not score.passed and "insuffisant" in score.as_text()


# ------------------------------------------------------------------ revue mensuelle
def test_monthly_review(rt, connector):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "DG", "direction", "mot-de-passe-1")
    now = utcnow()
    with rt.sessions() as s:
        s.add(JournalEntry(agent="messagerie", action_type="mail.ack", level=1, channel="mail",
                           status="failed", summary="Échec : SMTP indisponible"))
        s.add_all([ProcessedMessage(mailbox="m", message_id=f"<n{i}>", pole="SOFT",
                                    category="client", sentiment="negatif")
                   for i in range(2)])
        s.add_all([Ticket(channel="sara", question="Comment exporter les bulletins scolaires ?")
                   for _ in range(3)])
        s.commit()
    for i in range(6):
        out = rt.governor.submit(ActionRequest(
            agent="communication", action_type="social.post", channel="facebook",
            title=f"post {i}", payload={"texte": "t"}))
        if i < 3:
            rt.governor.reject(out.pending_id, by="Awa", reason="hors sujet")
        else:
            rt.governor.approve(out.pending_id, by="Awa")
    review = rt.revue.run(now)
    text = review.as_text()
    assert "mail.ack : 1" in text and "SMTP indisponible" in text
    assert "SOFT 2" in text
    assert "social.post : 6 décision(s), 3 rejet(s)" in text
    assert any("social.post : 3 rejets sur 6" in p for p in review.pistes)
    assert any("bulletins exporter scolaires" in p and "3 fois" in p for p in review.pistes)
    assert any("3 ticket(s)" in p for p in review.pistes)
    (mail,) = [m for m in connector.sent if "Revue mensuelle" in m["subject"]]
    assert mail["to"] == "dg@ibig.test"


def test_reports_page_keeps_full_text(rt):
    UserStore(rt.sessions, rt.org).create("dg@ibig.test", "DG", "direction", "mot-de-passe-1")
    rt.chef.run_daily()
    rt.revue.run()
    c = TestClient(create_app(rt), base_url="https://testserver")
    c.post("/login", data={"email": "dg@ibig.test", "password": "mot-de-passe-1"})
    page = c.get("/rapports").text
    assert "Revue mensuelle" in page and "Pistes pour la revue" in page
    assert "Mails reçus (24 h)" in page


def test_review_period_excludes_old_data(rt):
    with rt.sessions() as s:
        s.add(JournalEntry(agent="x", action_type="mail.ack", level=1, channel="mail",
                           status="failed", summary="ancien",
                           created_at=utcnow() - timedelta(days=45)))
        s.commit()
    assert "ancien" not in rt.revue.build().as_text()
