<?php
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
 * GET ?action=ping       → {"ok":true}
 * GET ?action=details&id=N → identifiants et chiffres seulement (50 lignes au plus)
 * GET ?action=controles  → {"controles":{"1":{"v":3},"2":{"e":"..."}},"at":"..."}
 */

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function agent_out(int $code, array $data): never
{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

$hashFile = __DIR__ . '/agent_controles.hash';
$expected = is_file($hashFile) ? trim((string)file_get_contents($hashFile)) : '';
if (!preg_match('/^[0-9a-f]{64}$/', $expected)) {
    agent_out(503, ['error' => "Point d'entrée de l'agent non activé"]);
}
$token = (string)($_SERVER['HTTP_X_AGENT_TOKEN'] ?? '');
if ($token === '' || !hash_equals($expected, hash('sha256', $token))) {
    usleep(300000); // freine les essais
    agent_out(403, ['error' => 'Jeton invalide']);
}

$action = (string)($_GET['action'] ?? '');
if ($action === 'ping') {
    agent_out(200, ['ok' => true]);
}

$queries = [
    1 => 'SELECT COUNT(*) FROM crm_devis WHERE statut = \'ENVOYE\' AND date_devis < DATE_SUB(CURDATE(), INTERVAL 30 DAY)',
    2 => 'SELECT COUNT(*) FROM crm_prospects WHERE actif = 1 AND statut IN (\'CONTACTE\',\'QUALIFIE\',\'PROPOSITION\',\'NEGOCIATION\') AND (prochaine_action IS NULL OR prochaine_action < CURDATE())',
    3 => 'SELECT COUNT(*) FROM crm_prospects WHERE actif = 1 AND commercial_id IS NULL AND statut NOT IN (\'GAGNE\',\'PERDU\')',
    4 => 'SELECT COUNT(*) FROM (SELECT email FROM crm_prospects WHERE email <> \'\' GROUP BY email HAVING COUNT(*) > 1) d',
    5 => 'SELECT COUNT(*) FROM edu_echeancier_tranches WHERE statut = \'EN_RETARD\'',
    6 => 'SELECT COUNT(*) FROM edu_inscriptions WHERE statut = \'ACTIVE\' AND (reste_a_payer < 0 OR montant_paye > montant_total OR montant_total - montant_paye <> reste_a_payer)',
    7 => 'SELECT COUNT(*) FROM fin_dettes WHERE statut = \'EN_RETARD\'',
    8 => 'SELECT COUNT(*) FROM fin_dettes WHERE montant_total - montant_paye <> reste_a_payer',
    9 => 'SELECT COUNT(*) FROM fin_operations WHERE statut = \'DRAFT\' AND created_at < DATE_SUB(NOW(), INTERVAL 30 DAY)',
    10 => 'SELECT COUNT(*) FROM rh_contrats WHERE statut = \'EN_COURS\' AND date_fin IS NOT NULL AND date_fin < CURDATE()',
    11 => 'SELECT COUNT(*) FROM rh_conges WHERE statut = \'DEMANDE\' AND requested_at < DATE_SUB(NOW(), INTERVAL 14 DAY)',
    12 => 'SELECT COUNT(*) FROM admin_users WHERE is_active = 1 AND (last_login_at IS NULL OR last_login_at < DATE_SUB(NOW(), INTERVAL 90 DAY))',
];

if ($action === 'controles' && $_SERVER['REQUEST_METHOD'] === 'GET') {
    $cfg = require dirname(__DIR__) . '/inc/config.php';
    try {
        $pdo = new PDO(
            "mysql:host={$cfg['host']};dbname={$cfg['dbname']};charset={$cfg['charset']}",
            $cfg['user'], $cfg['password'],
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
        try { $pdo->exec('SET SESSION TRANSACTION READ ONLY'); } catch (Throwable $e) {}
    } catch (Throwable $e) {
        agent_out(503, ['error' => 'Base de données injoignable']);
    }
    $out = [];
    foreach ($queries as $id => $sql) {
        try {
            $out[(string)$id] = ['v' => (float)$pdo->query($sql)->fetchColumn()];
        } catch (Throwable $e) {
            $out[(string)$id] = ['e' => 'requête impossible'];
        }
    }
    agent_out(200, ['controles' => $out, 'at' => date('c')]);
}

if ($action === 'details' && $_SERVER['REQUEST_METHOD'] === 'GET') {
    $details = [
    3 => 'SELECT id, statut FROM crm_prospects WHERE actif = 1 AND commercial_id IS NULL AND statut NOT IN (\'GAGNE\',\'PERDU\') LIMIT 50',
    4 => 'SELECT MIN(id) AS garde, GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM crm_prospects WHERE email <> \'\' GROUP BY email HAVING COUNT(*) > 1 LIMIT 50',
    5 => 'SELECT id, echeancier_id, numero_tranche, date_limite, montant_restant FROM edu_echeancier_tranches WHERE statut = \'EN_RETARD\' ORDER BY date_limite LIMIT 50',
    6 => 'SELECT id, matricule, montant_total, montant_paye, reste_a_payer FROM edu_inscriptions WHERE statut = \'ACTIVE\' AND (reste_a_payer < 0 OR montant_paye > montant_total OR montant_total - montant_paye <> reste_a_payer) LIMIT 50',
    10 => 'SELECT id, employe_id, type_contrat, date_fin FROM rh_contrats WHERE statut = \'EN_COURS\' AND date_fin IS NOT NULL AND date_fin < CURDATE() LIMIT 50',
    12 => 'SELECT id, role, last_login_at, created_at FROM admin_users WHERE is_active = 1 AND (last_login_at IS NULL OR last_login_at < DATE_SUB(NOW(), INTERVAL 90 DAY)) ORDER BY id LIMIT 100',
    ];
    $id = (int)($_GET['id'] ?? 0);
    if (!isset($details[$id])) {
        agent_out(404, ['error' => 'Détail inconnu']);
    }
    $cfg = require dirname(__DIR__) . '/inc/config.php';
    try {
        $pdo = new PDO(
            "mysql:host={$cfg['host']};dbname={$cfg['dbname']};charset={$cfg['charset']}",
            $cfg['user'], $cfg['password'],
            [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
        );
        try { $pdo->exec('SET SESSION TRANSACTION READ ONLY'); } catch (Throwable $e) {}
        agent_out(200, ['id' => $id, 'lignes' => $pdo->query($details[$id])->fetchAll(PDO::FETCH_ASSOC)]);
    } catch (Throwable $e) {
        agent_out(503, ['error' => 'Requête impossible']);
    }
}

agent_out(404, ['error' => 'Action inconnue']);
