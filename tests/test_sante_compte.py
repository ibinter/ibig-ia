"""Santé des tâches planifiées et changement de mot de passe."""

from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ibig_agent.auth import UserStore
from ibig_agent.dashboard.app import create_app
from ibig_agent.db import JobStatus, JournalEntry
from ibig_agent.scheduler import ALERT_AFTER, build_scheduler, job_specs, tracked

PASSWORD = "mot-de-passe-solide"


@pytest.fixture
def store(rt):
    s = UserStore(rt.sessions, rt.org)
    s.create("dg@ibig.test", "Direction", "direction", PASSWORD)
    s.create("admin@ibig.test", "Admin", "admin", PASSWORD)
    s.create("awa@ibig.test", "Awa", "valideur", PASSWORD)
    return s


def login(rt, email, password=PASSWORD):
    c = TestClient(create_app(rt), base_url="https://testserver")
    r = c.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert r.headers["location"] == "/", r.headers["location"]
    return c


def msg(r):
    return unquote(r.headers["location"])


# ------------------------------------------------------------------ tâches
def test_scheduler_uses_every_declared_job(rt):
    sched = build_scheduler(rt)
    assert {j.id for j in sched.get_jobs()} == {s.id for s in job_specs(rt)}
    assert rt.scheduler is sched


def test_tracked_job_records_success_and_alerts_after_repeated_failures(rt):
    tracked(rt, "demo_ok", "Démo", lambda: "3 mails")()
    with rt.sessions() as s:
        row = s.get(JobStatus, "demo_ok")
    assert row.runs == 1 and row.failures == 0 and row.last_result == "3 mails" and row.last_ok

    def boom():
        raise RuntimeError("serveur IMAP injoignable")

    for _ in range(ALERT_AFTER + 1):
        tracked(rt, "demo_ko", "Relève", boom)()
    with rt.sessions() as s:
        row = s.get(JobStatus, "demo_ko")
        alerts = s.scalars(select(JournalEntry).where(
            JournalEntry.action_type == "system.job_failed")).all()
    assert row.failures == ALERT_AFTER + 1 and "IMAP injoignable" in row.last_error
    assert len(alerts) == 1  # une seule alerte par série d'échecs
    tracked(rt, "demo_ko", "Relève", lambda: "ok")()
    with rt.sessions() as s:
        row = s.get(JobStatus, "demo_ko")
    assert row.failures == 0 and not row.alerted and row.total_failures == ALERT_AFTER + 1


def test_health_page_lists_jobs_and_runs_one(rt, store):
    tracked(rt, "mail_poll", "Relève des mails", lambda: (_ for _ in ()).throw(OSError("x")))()
    c = login(rt, "dg@ibig.test")
    page = c.get("/sante").text
    assert "Relève des mails" in page and "Purge des données anciennes" in page
    assert "1 échec de suite" in page
    r = c.post("/sante/watch_alerts/lancer", follow_redirects=False)
    assert "lancée" in msg(r)
    with rt.sessions() as s:
        assert s.get(JobStatus, "watch_alerts").runs == 1
    assert c.post("/sante/data_retention/lancer").status_code == 404  # pas de bouton
    assert c.post("/sante/inconnue/lancer").status_code == 404
    assert login(rt, "awa@ibig.test").get("/sante").status_code == 403


# ------------------------------------------------------------------ mot de passe
def test_password_change_rules_and_other_devices_logged_out(rt, store):
    phone = login(rt, "dg@ibig.test")      # autre appareil
    c = login(rt, "dg@ibig.test")
    assert "Changer mon mot de passe" in c.get("/compte").text
    bad = [({"actuel": "faux-mot-de-passe", "nouveau": "nouveau-secret-2026",
             "confirmation": "nouveau-secret-2026"}, "actuel incorrect"),
           ({"actuel": PASSWORD, "nouveau": "nouveau-secret-2026",
             "confirmation": "autre-chose-2026"}, "ne correspondent pas"),
           ({"actuel": PASSWORD, "nouveau": "court", "confirmation": "court"}, "10 caractères")]
    for data, why in bad:
        assert why in msg(c.post("/compte/mot-de-passe", data=data, follow_redirects=False))
    r = c.post("/compte/mot-de-passe", follow_redirects=False,
               data={"actuel": PASSWORD, "nouveau": "nouveau-secret-2026",
                     "confirmation": "nouveau-secret-2026"})
    assert "Mot de passe changé" in msg(r)
    assert c.get("/compte", follow_redirects=False).status_code == 200  # cet appareil reste
    assert phone.get("/", follow_redirects=False).headers["location"] == "/login"
    login(rt, "dg@ibig.test", "nouveau-secret-2026")


def test_admin_resets_a_password_and_closes_sessions(rt, store):
    awa = login(rt, "awa@ibig.test")
    uid = next(u.id for u in store.all() if u.email == "awa@ibig.test")
    data = {"nouveau": "provisoire-2026-awa!", "confirmation": "provisoire-2026-awa!"}
    assert login(rt, "dg@ibig.test").post(f"/utilisateurs/{uid}/mot-de-passe",
                                          data=data).status_code == 403
    r = login(rt, "admin@ibig.test").post(f"/utilisateurs/{uid}/mot-de-passe", data=data,
                                          follow_redirects=False)
    assert "sessions ouvertes sont fermées" in msg(r)
    assert awa.get("/", follow_redirects=False).headers["location"] == "/login"
    login(rt, "awa@ibig.test", "provisoire-2026-awa!")


def test_logout_everywhere_keeps_current_device(rt, store):
    other = login(rt, "dg@ibig.test")
    c = login(rt, "dg@ibig.test")
    assert "déconnectés" in msg(c.post("/compte/deconnecter-partout", follow_redirects=False))
    assert c.get("/", follow_redirects=False).status_code == 200
    assert other.get("/", follow_redirects=False).headers["location"] == "/login"
