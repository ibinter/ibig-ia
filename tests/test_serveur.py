"""Analyse du relevé serveur : seuils, incidents `srv|…`, résolution automatique et panneau."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ibig_agent import serveur
from ibig_agent.ecosystem import EcosystemService

NOW = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)


def healthy(at: datetime = NOW) -> dict:
    return {
        "version": 1, "at": at.isoformat(), "hostname": "vps", "uptime_s": 86400 * 3, "load": [1.0, 1.0, 1.0],
        "cores": 6, "mem": {"total_mb": 12000, "available_mb": 7000}, "reboot_required": False,
        "disks": [{"mount": "/", "used_pct": 30.0, "free_gb": 60}],
        "containers": [{"name": "ibig-agent", "image": "x", "state": "running", "health": "healthy", "restarts": 0}],
        "pm2": [{"name": "api", "status": "online", "restarts": 1, "mem_mb": 80}], "failed_units": [],
        "nginx": {"errors_15m_by_site": {}, "errors_15m_total": 0, "requests_15m": 500, "http5xx_15m": 0},
        "security": {"ssh_failed_24h": 100, "banned_now": 3}, "updates": {"pending": 0, "security": 0},
        "certs": [], "backups": [{"name": "Agent IA", "count": 14, "age_h": 5.0}],
        "services": {"nginx": "active", "docker": "active"},
    }


def kinds(d):
    return {(i.kind, i.severity) for i in serveur.evaluate(d, NOW)}


def test_a_healthy_server_has_no_issue_and_a_missing_file_is_silent():
    assert serveur.evaluate(healthy(), NOW) == []
    assert serveur.evaluate(None, NOW) == []


def test_thresholds_for_disk_memory_load_and_stale_report():
    d = healthy()
    d["disks"][0]["used_pct"] = 88
    assert ("srv_disque", "warning") in kinds(d)
    d["disks"][0]["used_pct"] = 95
    assert ("srv_disque", "critical") in kinds(d)
    d = healthy()
    d["mem"]["available_mb"] = 300
    assert ("srv_memoire", "critical") in kinds(d)
    d = healthy()
    d["load"] = [9, 10, 9]
    assert ("srv_charge", "warning") in kinds(d)
    d["load"] = [20, 20, 20]
    assert ("srv_charge", "critical") in kinds(d)
    assert ("srv_collecteur", "warning") in kinds(healthy(NOW - timedelta(minutes=20)))
    assert ("srv_collecteur", "critical") in kinds(healthy(NOW - timedelta(hours=3)))


def test_containers_apps_units_backups_and_services():
    d = healthy()
    d["containers"] += [{"name": "db", "image": "pg", "state": "exited", "health": "", "restarts": 0},
                        {"name": "web", "image": "w", "state": "running", "health": "unhealthy", "restarts": 0},
                        {"name": "x-run-1", "image": "t", "state": "exited", "health": "", "restarts": 0}]
    d["pm2"][0]["status"] = "errored"
    d["failed_units"] = ["sys-kernel-debug.mount", "backup.service"]
    d["backups"] = [{"name": "Santarex", "count": 3, "age_h": 108.9}, {"name": "Vide", "count": 0, "age_h": None}]
    d["services"]["nginx"] = "failed"
    issues = serveur.evaluate(d, NOW)
    titles = " | ".join(i.title for i in issues)
    assert "db arrêté" in titles and "web en mauvaise santé" in titles and "x-run-1" not in titles
    assert "api hors ligne" in titles and "1 service(s) système" in titles       # l'unité ignorée est écartée
    assert "Santarex trop ancienne (4.5 jours)" in titles and "aucune sauvegarde trouvée pour Vide" in titles
    assert "service nginx inactif" in titles
    assert all("CAUSE PROBABLE" in i.suggestion and "CORRECTION" in i.suggestion for i in issues)


def test_web_errors_updates_reboot_and_ssh():
    d = healthy()
    d["nginx"] = {"errors_15m_by_site": {"a.ci": 30, "b.ci": 150, "c.ci": 2}, "errors_15m_total": 182,
                  "requests_15m": 100, "http5xx_15m": 10}
    d["updates"] = {"pending": 59, "security": 2}
    d["reboot_required"] = True
    d["security"]["ssh_failed_24h"] = 9000
    issues = {i.key: i.severity for i in serveur.evaluate(d, NOW)}
    assert issues["srv|nginx|a.ci"] == "warning" and issues["srv|nginx|b.ci"] == "critical"
    assert "srv|nginx|c.ci" not in issues and "srv|http5xx" in issues
    assert issues["srv|maj_securite"] == "warning" and issues["srv|redemarrage"] == "warning"
    assert issues["srv|ssh"] == "warning"
    d["updates"] = {"pending": 59, "security": 0}
    assert {i.key: i.severity for i in serveur.evaluate(d, NOW)}["srv|maj"] == "info"


@pytest.fixture
def svc(rt, tmp_path):
    rt.settings.state_dir = tmp_path
    alerts = []
    s = EcosystemService(rt.sessions, rt.settings, alert=lambda *a: alerts.append(a),
                         client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok"))),
                         cert_check=lambda host: 60)
    s.alerts = alerts
    return s


def write(tmp_path, data):
    (tmp_path / "serveur.json").write_text(json.dumps(data), encoding="utf-8")


def test_server_incidents_open_alert_once_and_resolve_by_themselves(svc, tmp_path):
    assert svc.run_server(NOW) == [] and svc.server_state(NOW) is None
    d = healthy()
    d["containers"][0]["state"] = "exited"
    write(tmp_path, d)
    assert len(svc.run_server(NOW)) == 1
    (inc,) = svc.incidents()
    assert inc.asset_id is None and inc.severity == "critical" and "ibig-agent" in inc.title
    svc.run_server(NOW + timedelta(minutes=2))
    assert svc.incidents()[0].count == 2
    write(tmp_path, healthy(NOW + timedelta(minutes=4)))
    svc.run_server(NOW + timedelta(minutes=4))
    assert svc.incidents() == []


def test_summary_and_alert_include_the_server(svc, tmp_path):
    svc.run_probes(NOW)                      # inventaire vide : aucune sonde, pas de relevé
    d = healthy(datetime.now(UTC))
    d["backups"][0]["age_h"] = 90
    write(tmp_path, d)
    svc.run_probes(NOW)
    data = svc.summary(NOW)
    assert data["level"] == "critique" and "serveur" in data["message"]
    assert len(svc.alerts) == 1 and "urgent" in svc.alerts[0][1]
    state = svc.server_state(datetime.now(UTC))
    assert state["containers"] == [1, 1] and state["pm2"] == [1, 1] and state["disk_pct"] == 30.0


def test_external_heartbeat_is_optional_stored_encrypted_and_reports_failures(rt):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200 if "ok" in request.url.path else 500)

    s = EcosystemService(rt.sessions, rt.settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert s.heartbeat(NOW) is None and s.heartbeat_status()["configured"] is False
    from ibig_agent.ecosystem import EcosystemError
    with pytest.raises(EcosystemError, match="https"):
        s.set_heartbeat_url("http://clair.exemple/ping", "dg")
    s.set_heartbeat_url("https://hc.exemple/ping/ok", "dg")
    assert s.heartbeat(NOW) is True and calls == ["https://hc.exemple/ping/ok"]
    st = s.heartbeat_status()
    assert st["configured"] and st["host"] == "hc.exemple" and st["ok"] is True and "ping" not in str(st)
    s.set_heartbeat_url("https://hc.exemple/ping/panne", "dg")
    assert s.heartbeat(NOW) is False and s.heartbeat_status()["detail"] == "refusé par le service"
    s.set_heartbeat_url("", "dg")
    assert s.heartbeat(NOW) is None and s.heartbeat_status()["configured"] is False


def test_open_findings_of_other_modules_are_mirrored_and_closed_when_fixed(rt):
    from ibig_agent.anomalies import Finding
    items = [Finding("db|crm|1", "donnees", "critical", "Base crm", "crm : doublons (12)", "12 doublons"),
             Finding("x", "bug", "warning", "Site", "Bug vu", "", status="resolved")]
    s = EcosystemService(rt.sessions, rt.settings, findings=lambda: items)
    assert len(s.run_findings(NOW)) == 1
    (inc,) = s.incidents()
    assert inc.key == "anom|db|crm|1" and inc.kind == "anom_donnees" and inc.severity == "critical"
    items[0].status = "resolved"
    s.run_findings(NOW + timedelta(minutes=5))
    assert s.incidents() == []


def test_ai_diagnosis_is_on_demand_stored_and_errors_are_simple(rt):
    class FakeLLM:
        def __init__(self):
            self.seen = []

        def write(self, purpose, system, user, max_tokens=4000):
            self.seen.append(user)
            return "Cause : conteneur arrêté. Vérifier docker logs."

    llm = FakeLLM()
    from ibig_agent.ecosystem import EcosystemError
    s = EcosystemService(rt.sessions, rt.settings, findings=list, llm=None)
    with pytest.raises(EcosystemError, match="IA"):
        s.diagnose(1, "dg")
    s = EcosystemService(rt.sessions, rt.settings, findings=list, llm=llm)
    with rt.sessions() as db:
        from ibig_agent.db import EcoIncident
        row = EcoIncident(key="srv|x", kind="srv_conteneur", severity="critical", title="Conteneur db arrêté")
        db.add(row)
        db.commit()
        iid = row.id
    assert "docker logs" in s.diagnose(iid, "dg")
    assert "Conteneur db arrêté" in llm.seen[0] and s.diagnoses([iid, 999]) == {iid: "Cause : conteneur arrêté. Vérifier docker logs."}
    with pytest.raises(EcosystemError, match="introuvable"):
        s.diagnose(999, "dg")


def test_fix_mission_is_created_from_an_incident_through_the_autopilot(rt):
    from ibig_agent.autopilot import PlannedTask
    from ibig_agent.db import EcoIncident
    s = EcosystemService(rt.sessions, rt.settings, findings=list)
    with rt.sessions() as db:
        row = EcoIncident(key="srv|y", kind="srv_conteneur", severity="critical", title="Conteneur db arrêté",
                          suggestion="CAUSE PROBABLE : mémoire\nCORRECTION : docker start db\nRISQUE : élevé")
        db.add(row)
        db.commit()
        iid = row.id
    title, goal, steps = s.fix_plan(iid)
    assert title.startswith("Corriger : Conteneur db") and steps[1][0] == "Appliquer la correction"
    assert "docker start db" in steps[1][1] and "mémoire" in steps[0][1]
    mid = rt.autopilot.create_from(title, goal, [PlannedTask(t, d, "", [n] if n else [])
                                                  for n, (t, d) in enumerate(steps)], "", "dg", "test")
    s.link_mission(iid, mid, "dg")
    assert s.missions_for([iid, 5]) == {iid: mid}
    assert len(rt.autopilot.tasks(mid)) == 3


def test_autopilot_missions_get_a_default_budget_so_they_are_not_blocked(rt):
    from ibig_agent.autopilot import DEFAULT_BUDGET_USD, PlannedTask
    mid = rt.autopilot.create_from("t", "o", [PlannedTask("a", "", "", [])], "", "dg", "test")
    m = rt.missions.get(mid)
    assert m.budget_usd == DEFAULT_BUDGET_USD
    assert rt.missions.blocking_failures(rt.missions.checks(m)) == []
