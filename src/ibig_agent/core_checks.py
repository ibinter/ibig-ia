"""Catalogue de contrôles en LECTURE SEULE pour la base d'IBIG CORE (CRM, EDUFORM, Finance, RH, administration).

Chaque contrôle est un SELECT qui renvoie un nombre ; il devient un constat sur la page Anomalies quand il
dépasse son seuil. Les noms de tables et de colonnes viennent de la structure de la base du CORE (sauvegarde
du 13/05/2026) ; un contrôle dont une colonne a changé s'affiche « n'a pas pu s'exécuter » sans rien casser.
L'installation ajoute les contrôles absents (par nom) et ne touche pas aux autres.
"""

from __future__ import annotations

import json
from datetime import datetime

import httpx

from .configstore import service_value, set_service_value

# (module, nom, requête, seuil, gravité)
CATALOG: list[tuple[str, str, str, float, str]] = [
    ("CRM", "Devis envoyés restés sans réponse depuis plus de 30 jours",
     "SELECT COUNT(*) FROM crm_devis WHERE statut = 'ENVOYE' AND date_devis < DATE_SUB(CURDATE(), INTERVAL 30 DAY)",
     0, "warning"),
    ("CRM", "Prospects actifs sans prochaine action ou en retard",
     ("SELECT COUNT(*) FROM crm_prospects WHERE actif = 1 AND statut IN ('CONTACTE','QUALIFIE','PROPOSITION','NEGOCIATION') "
      "AND (prochaine_action IS NULL OR prochaine_action < CURDATE())"), 20, "warning"),
    ("CRM", "Prospects actifs sans commercial responsable",
     "SELECT COUNT(*) FROM crm_prospects WHERE actif = 1 AND commercial_id IS NULL AND statut NOT IN ('GAGNE','PERDU')",
     0, "warning"),
    ("CRM", "Prospects en double (même adresse e-mail)",
     "SELECT COUNT(*) FROM (SELECT email FROM crm_prospects WHERE email <> '' GROUP BY email HAVING COUNT(*) > 1) d",
     0, "warning"),
    ("EDUFORM", "Tranches de paiement en retard",
     "SELECT COUNT(*) FROM edu_echeancier_tranches WHERE statut = 'EN_RETARD'", 0, "warning"),
    ("EDUFORM", "Inscriptions dont les montants ne concordent pas",
     ("SELECT COUNT(*) FROM edu_inscriptions WHERE statut = 'ACTIVE' "
      "AND (reste_a_payer < 0 OR montant_paye > montant_total OR montant_total - montant_paye <> reste_a_payer)"),
     0, "critical"),
    ("Finance", "Dettes fournisseurs en retard",
     "SELECT COUNT(*) FROM fin_dettes WHERE statut = 'EN_RETARD'", 0, "warning"),
    ("Finance", "Dettes dont le reste à payer est incohérent",
     "SELECT COUNT(*) FROM fin_dettes WHERE montant_total - montant_paye <> reste_a_payer", 0, "critical"),
    ("Finance", "Opérations restées en brouillon depuis plus de 30 jours",
     "SELECT COUNT(*) FROM fin_operations WHERE statut = 'DRAFT' AND created_at < DATE_SUB(NOW(), INTERVAL 30 DAY)",
     0, "warning"),
    ("RH", "Contrats arrivés à terme mais encore marqués en cours",
     "SELECT COUNT(*) FROM rh_contrats WHERE statut = 'EN_COURS' AND date_fin IS NOT NULL AND date_fin < CURDATE()",
     0, "warning"),
    ("RH", "Demandes de congé sans réponse depuis plus de 14 jours",
     "SELECT COUNT(*) FROM rh_conges WHERE statut = 'DEMANDE' AND requested_at < DATE_SUB(NOW(), INTERVAL 14 DAY)",
     0, "warning"),
    ("Administration", "Comptes administrateurs actifs non utilisés depuis 90 jours",
     ("SELECT COUNT(*) FROM admin_users WHERE is_active = 1 "
      "AND (last_login_at IS NULL OR last_login_at < DATE_SUB(NOW(), INTERVAL 90 DAY))"), 3, "warning"),
]


def install(monitor, connection: str, by: str) -> dict:
    """Ajoute à la connexion les contrôles du catalogue qui n'y sont pas encore (comparaison par nom)."""
    have = {c.label for c in monitor.checks() if c.connection == connection}
    added = skipped = 0
    for module, name, sql, threshold, severity in CATALOG:
        label = f"{module} : {name}"
        if label in have:
            skipped += 1
            continue
        monitor.add_check(connection, label, sql, threshold, severity, by)
        added += 1
    return {"added": added, "skipped": skipped}


# Détails demandés pour préparer une correction : identifiants et chiffres SEULEMENT (aucun nom, e-mail,
# téléphone ni salaire). 50 lignes au plus. Clé = numéro du contrôle dans CATALOG (à partir de 1).
DETAILS: dict[int, str] = {
    3: "SELECT id, statut FROM crm_prospects WHERE actif = 1 AND commercial_id IS NULL AND statut NOT IN ('GAGNE','PERDU') LIMIT 50",
    4: ("SELECT MIN(id) AS garde, GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM crm_prospects WHERE email <> '' "
        "GROUP BY email HAVING COUNT(*) > 1 LIMIT 50"),
    5: ("SELECT id, echeancier_id, numero_tranche, date_limite, montant_restant FROM edu_echeancier_tranches "
        "WHERE statut = 'EN_RETARD' ORDER BY date_limite LIMIT 50"),
    6: ("SELECT id, matricule, montant_total, montant_paye, reste_a_payer FROM edu_inscriptions WHERE statut = 'ACTIVE' "
        "AND (reste_a_payer < 0 OR montant_paye > montant_total OR montant_total - montant_paye <> reste_a_payer) LIMIT 50"),
    10: ("SELECT id, employe_id, type_contrat, date_fin FROM rh_contrats WHERE statut = 'EN_COURS' "
         "AND date_fin IS NOT NULL AND date_fin < CURDATE() LIMIT 50"),
    12: ("SELECT id, role, last_login_at, created_at FROM admin_users WHERE is_active = 1 "
         "AND (last_login_at IS NULL OR last_login_at < DATE_SUB(NOW(), INTERVAL 90 DAY)) ORDER BY id LIMIT 100"),
}

# Correction proposée (à faire valider) pour chaque contrôle qui en a une : (quoi faire, comment).
FIXES: dict[int, tuple[str, str]] = {
    3: ("Affecter un commercial responsable à chaque prospect de la liste.",
        "Dans le CRM, ouvrir chaque prospect et choisir son commercial (ou répartir en lot)."),
    4: ("Fusionner les doublons en gardant la fiche « garde » et en rattachant les autres à elle.",
        "Dans le CRM, fusionner ou supprimer les fiches en double après vérification des interactions."),
    5: ("Relancer les participants dont la tranche est en retard (montant restant indiqué).",
        "Utiliser les relances d'encaissement d'EDUFORM, ou appeler le participant."),
    6: (("Recalculer le reste à payer = montant total − montant payé. Si le payé dépasse le total, vérifier le "
         "paiement en trop (remboursement ou erreur de saisie) AVANT toute correction."),
        ("UPDATE edu_inscriptions SET reste_a_payer = montant_total - montant_paye WHERE id IN (<identifiants>) "
         "AND montant_paye <= montant_total;  -- à valider par la comptabilité, après sauvegarde de la table")),
    10: ("Pour chaque contrat arrivé à terme : le renouveler (nouvelle date de fin) ou le clôturer.",
         "UPDATE rh_contrats SET statut = 'EXPIRE' WHERE id IN (<identifiants à clôturer>);  -- décision RH par contrat"),
    12: (("Désactiver les comptes qui ne servent plus ; garder ceux dont la personne est connue et revient. "
          "Aucun compte n'est désactivé sans confirmation de son titulaire ou de la direction."),
         "UPDATE admin_users SET is_active = 0 WHERE id IN (<identifiants confirmés>);  -- après vérification des rôles"),
}


# ---------------------------------------------------------------------------------------------------
# Point d'entrée posé sur IBIG CORE : il exécute CES requêtes côté serveur (avec l'accès à la base que le
# CORE possède déjà) et ne renvoie que des nombres. Aucun mot de passe de base n'est échangé.
ENDPOINT_DIR = "admin/agent_ibig"
ENDPOINT = "agent_controles.php"
SETTING_URL, SETTING_TOKEN, SETTING_STATE = "core_url", "core_token", "core_state"


BS = chr(92)   # antislash


def _php_str(text: str) -> str:
    return "'" + text.replace(BS, BS + BS).replace("'", BS + "'") + "'"


def render_php() -> str:
    """Fichier PHP du point d'entrée, généré depuis le catalogue (une seule source de vérité)."""
    rows = "\n".join(f"    {n} => {_php_str(sql)}," for n, (_, _, sql, _, _) in enumerate(CATALOG, 1))
    drows = chr(10).join(f"    {n} => {_php_str(sql)}," for n, sql in DETAILS.items())
    return f"""<?php
declare(strict_types=1);

/**
 * IBIG CORE — point d'entrée de l'Agent IA IBIG, contrôles en LECTURE SEULE.
 * Fichier généré par l'agent (core_checks.render_php) : ne pas le modifier à la main.
 *
 * Sécurité :
 *   - jeton dans l'en-tête X-Agent-Token ; seule son empreinte SHA-256 est gardée, dans
 *     agent_controles.hash (fermé au web par .htaccess). Fichier absent = point d'entrée coupé ;
 *   - l'agent ne peut pas envoyer de requête : il demande « controles » et ce fichier exécute sa
 *     liste fixe de SELECT ; il ne renvoie QUE des nombres (aucun nom, montant ni document) ;
 *   - la session de base est ouverte en lecture seule.
 *
 * GET ?action=ping       → {{"ok":true}}
 * GET ?action=details&id=N → identifiants et chiffres seulement (50 lignes au plus)
 * GET ?action=controles  → {{"controles":{{"1":{{"v":3}},"2":{{"e":"..."}}}},"at":"..."}}
 */

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function agent_out(int $code, array $data): never
{{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}}

$hashFile = __DIR__ . '/agent_controles.hash';
$expected = is_file($hashFile) ? trim((string)file_get_contents($hashFile)) : '';
if (!preg_match('/^[0-9a-f]{{64}}$/', $expected)) {{
    agent_out(503, ['error' => "Point d'entrée de l'agent non activé"]);
}}
$token = (string)($_SERVER['HTTP_X_AGENT_TOKEN'] ?? '');
if ($token === '' || !hash_equals($expected, hash('sha256', $token))) {{
    usleep(300000); // freine les essais
    agent_out(403, ['error' => 'Jeton invalide']);
}}

$action = (string)($_GET['action'] ?? '');
if ($action === 'ping') {{
    agent_out(200, ['ok' => true]);
}}

$queries = [
{rows}
];

if ($action === 'controles' && $_SERVER['REQUEST_METHOD'] === 'GET') {{
    $cfg = require dirname(__DIR__) . '/inc/config.php';
    try {{
        $pdo = new PDO(
            "mysql:host={{$cfg['host']}};dbname={{$cfg['dbname']}};charset={{$cfg['charset']}}",
            $cfg['user'], $cfg['password'],
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
        try {{ $pdo->exec('SET SESSION TRANSACTION READ ONLY'); }} catch (Throwable $e) {{}}
    }} catch (Throwable $e) {{
        agent_out(503, ['error' => 'Base de données injoignable']);
    }}
    $out = [];
    foreach ($queries as $id => $sql) {{
        try {{
            $out[(string)$id] = ['v' => (float)$pdo->query($sql)->fetchColumn()];
        }} catch (Throwable $e) {{
            $out[(string)$id] = ['e' => 'requête impossible'];
        }}
    }}
    agent_out(200, ['controles' => $out, 'at' => date('c')]);
}}

if ($action === 'details' && $_SERVER['REQUEST_METHOD'] === 'GET') {{
    $details = [
{drows}
    ];
    $id = (int)($_GET['id'] ?? 0);
    if (!isset($details[$id])) {{
        agent_out(404, ['error' => 'Détail inconnu']);
    }}
    $cfg = require dirname(__DIR__) . '/inc/config.php';
    try {{
        $pdo = new PDO(
            "mysql:host={{$cfg['host']}};dbname={{$cfg['dbname']}};charset={{$cfg['charset']}}",
            $cfg['user'], $cfg['password'],
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
        try {{ $pdo->exec('SET SESSION TRANSACTION READ ONLY'); }} catch (Throwable $e) {{}}
        agent_out(200, ['id' => $id, 'lignes' => $pdo->query($details[$id])->fetchAll(PDO::FETCH_ASSOC)]);
    }} catch (Throwable $e) {{
        agent_out(503, ['error' => 'Requête impossible']);
    }}
}}

agent_out(404, ['error' => 'Action inconnue']);
"""


HTACCESS = f"""# Seul le fichier agent_controles.php est joignable ; l'empreinte du jeton reste privée.
<FilesMatch "{BS}.(hash|json|log|txt)$">
  <IfModule mod_authz_core.c>
    Require all denied
  </IfModule>
  <IfModule !mod_authz_core.c>
    Order deny,allow
    Deny from all
  </IfModule>
</FilesMatch>
"""


class CoreMonitor:
    """Lit les contrôles d'IBIG CORE par son point d'entrée et les transforme en constats (page Anomalies)."""

    def __init__(self, sessions, settings, anomalies, client=None) -> None:
        self._sessions, self.settings, self.anomalies = sessions, settings, anomalies
        self._client = client

    def configure(self, base_url: str, token: str, by: str) -> None:
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith("https://") or len(token) < 32:
            raise ValueError("Adresse https:// et jeton d'au moins 32 caractères requis")
        set_service_value(self._sessions, self.settings, SETTING_URL, base_url, by)
        set_service_value(self._sessions, self.settings, SETTING_TOKEN, token, by, secret=True)

    def configured(self) -> bool:
        return bool(service_value(self._sessions, self.settings, SETTING_URL)
                    and service_value(self._sessions, self.settings, SETTING_TOKEN))

    def fetch(self) -> dict[int, dict]:
        base = service_value(self._sessions, self.settings, SETTING_URL)
        token = service_value(self._sessions, self.settings, SETTING_TOKEN)
        if not (base and token):
            raise ValueError("IBIG CORE n'est pas raccordé")
        client = self._client or httpx.Client(timeout=30)
        resp = client.get(f"{base}/{ENDPOINT}", params={"action": "controles"},
                          headers={"X-Agent-Token": token, "accept": "application/json"})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code != 200 or not isinstance(data, dict):
            raise ValueError(f"IBIG CORE {resp.status_code} : {str(data.get('error', ''))[:120] or 'réponse illisible'}")
        return {int(k): v for k, v in (data.get("controles") or {}).items()}

    def details(self, n: int) -> list[dict]:
        """Lignes (identifiants et chiffres seulement) derrière le contrôle n ; vide si indisponible."""
        base = service_value(self._sessions, self.settings, SETTING_URL)
        token = service_value(self._sessions, self.settings, SETTING_TOKEN)
        if not (base and token) or n not in DETAILS:
            return []
        client = self._client or httpx.Client(timeout=30)
        try:
            resp = client.get(f"{base}/{ENDPOINT}", params={"action": "details", "id": n},
                              headers={"X-Agent-Token": token, "accept": "application/json"})
            return list(resp.json().get("lignes", [])) if resp.status_code == 200 else []
        except (ValueError, httpx.HTTPError):
            return []

    def run(self, now: datetime | None = None) -> dict:
        from .anomalies import Finding
        from .db import utcnow

        now = now or utcnow()
        findings = self.anomalies.findings()
        out = {"checks": len(CATALOG), "anomalies": 0, "errors": 0}
        produced: set[str] = set()
        new_critical = []
        try:
            values = self.fetch()
        except (ValueError, httpx.HTTPError) as exc:
            f = Finding("core|injoignable", "donnees", "warning", "IBIG CORE",
                        "IBIG CORE : les contrôles n'ont pas pu être lus", str(exc)[:200])
            self.anomalies._upsert(findings, f, now)
            self.anomalies._save(findings)
            return {**out, "errors": len(CATALOG)}
        for n, (module, name, _sql, threshold, severity) in enumerate(CATALOG, 1):
            res = values.get(n, {})
            if "v" not in res:
                out["errors"] += 1
                continue
            if res["v"] > threshold:
                out["anomalies"] += 1
                f = Finding(f"core|{n}", "donnees", severity, "IBIG CORE", f"IBIG CORE — {module} : {name} ({res['v']:g})",
                            f"Le contrôle « {name} » renvoie {res['v']:g} (seuil toléré : {threshold:g}).")
                produced.add(f.key)
                if self.anomalies._upsert(findings, f, now) and severity == "critical":
                    new_critical.append(f)
        for f in findings:
            if f.key.startswith("core|") and f.key not in produced and f.status != "resolved":
                f.status = "resolved"
        self.anomalies._save(findings)
        self.anomalies._notify(new_critical)
        set_service_value(self._sessions, self.settings, SETTING_STATE,
                          json.dumps({"at": now.isoformat(), **out}), "agent")
        return out
