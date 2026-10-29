<?php
declare(strict_types=1);

/**
 * Mailaby — point d'entrée de l'Agent IA IBIG (campagnes validées par un humain).
 *
 * À copier dans admin/mailaby/ (voir integrations/mailaby/LISEZMOI.md). Sécurité :
 *   - jeton dans l'en-tête X-Agent-Token ; seule son empreinte SHA-256 est gardée, dans
 *     storage/agent_api.hash (dossier fermé au web). Fichier absent = point d'entrée coupé ;
 *   - aucune lecture de mots de passe, de clés ni de contacts : seulement les étiquettes
 *     (nom + effectif), les comptes d'envoi (nom + adresse) et la création de campagne ;
 *   - une campagne n'est LANCÉE que si l'agent le demande (launch=true), ce qu'il ne fait
 *     qu'après validation humaine ; sinon elle reste en brouillon dans Mailaby.
 *
 * GET  ?action=ping       → {"ok":true}
 * GET  ?action=tags       → étiquettes et contacts actifs (id, name, n) + total actif
 * GET  ?action=accounts   → comptes d'envoi (id, label, from_name, from_email)
 * GET  ?action=stats      → envois, ouvertures, clics, rejets par campagne récente (days=30)
 * POST ?action=campaign   → JSON {name, subject, html, from_email|account_id, tag_ids[],
 *                           mode: any|every, preheader, from_name, send_at, launch}
 */

require_once __DIR__ . '/inc/mailaby.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function agent_out(int $code, array $data): never
{
    http_response_code($code);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

$hashFile = __DIR__ . '/storage/agent_api.hash';
$expected = is_file($hashFile) ? trim((string)file_get_contents($hashFile)) : '';
if (!preg_match('/^[0-9a-f]{64}$/', $expected)) {
    agent_out(503, ['error' => "Point d'entrée de l'agent non activé"]);
}
$token = (string)($_SERVER['HTTP_X_AGENT_TOKEN'] ?? '');
if ($token === '' || !hash_equals($expected, hash('sha256', $token))) {
    usleep(300000); // freine les essais
    agent_out(403, ['error' => 'Jeton invalide']);
}

$pdo = $GLOBALS['pdo'] ?? null;
if (!$pdo instanceof PDO || !mailaby_installed($pdo) || !mailaby_schema_current($pdo)) {
    agent_out(503, ['error' => 'Mailaby non installé ou à mettre à jour (install.php)']);
}

$action = (string)($_GET['action'] ?? '');

if ($action === 'ping') {
    agent_out(200, ['ok' => true]);
}

if ($action === 'tags' && $_SERVER['REQUEST_METHOD'] === 'GET') {
    $tags = [];
    foreach (mailaby_tags_all($pdo, 0) as $t) {
        $tags[] = ['id' => (int)$t['id'], 'name' => (string)$t['name'], 'n' => (int)$t['n']];
    }
    $total = (int)$pdo->query('SELECT COUNT(*) FROM mailaby_contacts WHERE unsubscribed = 0')->fetchColumn();
    agent_out(200, ['tags' => $tags, 'total_actifs' => $total]);
}

if ($action === 'accounts' && $_SERVER['REQUEST_METHOD'] === 'GET') {
    $rows = $pdo->query('SELECT id, label, from_name, from_email FROM mailaby_accounts ORDER BY id')->fetchAll(PDO::FETCH_ASSOC);
    agent_out(200, ['accounts' => $rows]);
}

if ($action === 'stats' && $_SERVER['REQUEST_METHOD'] === 'GET') {
    $days  = max(1, min(365, (int)($_GET['days'] ?? 30)));
    $since = date('Y-m-d H:i:s', time() - $days * 86400);
    $st = $pdo->prepare(
        "SELECT c.id, c.name, c.status, c.created_at,
                SUM(r.status = 'sent') AS sent,
                SUM(r.opened_at IS NOT NULL) AS opened,
                SUM(r.clicked_at IS NOT NULL) AS clicked,
                SUM(r.bounced_at IS NOT NULL) AS bounced
         FROM mailaby_campaigns c JOIN mailaby_recipients r ON r.campaign_id = c.id
         WHERE c.created_at >= ? GROUP BY c.id ORDER BY c.id DESC LIMIT 50"
    );
    $st->execute([$since]);
    $camps = [];
    foreach ($st->fetchAll(PDO::FETCH_ASSOC) as $c) {
        $camps[] = ['id' => (int)$c['id'], 'name' => (string)$c['name'], 'status' => (string)$c['status'],
                    'created_at' => (string)$c['created_at'], 'sent' => (int)$c['sent'],
                    'opened' => (int)$c['opened'], 'clicked' => (int)$c['clicked'], 'bounced' => (int)$c['bounced']];
    }
    $unsub = (int)$pdo->query('SELECT COUNT(*) FROM mailaby_contacts WHERE unsubscribed = 1')->fetchColumn();
    agent_out(200, ['days' => $days, 'campagnes' => $camps, 'desinscrits' => $unsub]);
}

if ($action === 'campaign' && $_SERVER['REQUEST_METHOD'] === 'POST') {
    $in = json_decode((string)file_get_contents('php://input'), true);
    if (!is_array($in)) agent_out(400, ['error' => 'Corps JSON attendu']);

    $subject = trim((string)($in['subject'] ?? ''));
    $html    = (string)($in['html'] ?? '');
    if ($subject === '' || mb_strlen($subject) > 500) agent_out(422, ['error' => 'Objet absent ou trop long']);
    if (trim(strip_tags($html)) === '' || strlen($html) > 500000) agent_out(422, ['error' => 'Contenu absent ou trop long']);

    // Compte expéditeur : par adresse, sinon par identifiant
    $acc = null;
    if (!empty($in['from_email'])) {
        $st = $pdo->prepare('SELECT * FROM mailaby_accounts WHERE from_email = ? ORDER BY id LIMIT 1');
        $st->execute([strtolower(trim((string)$in['from_email']))]);
        $acc = $st->fetch(PDO::FETCH_ASSOC) ?: null;
    } elseif (!empty($in['account_id'])) {
        $st = $pdo->prepare('SELECT * FROM mailaby_accounts WHERE id = ?');
        $st->execute([(int)$in['account_id']]);
        $acc = $st->fetch(PDO::FETCH_ASSOC) ?: null;
    }
    if (!$acc) agent_out(422, ['error' => "Compte d'envoi introuvable dans Mailaby (adresse d'expédition)"]);

    // Ciblage : les étiquettes demandées doivent exister ; sans étiquette = tous les actifs
    $mode = in_array(($in['mode'] ?? 'any'), ['any', 'every'], true) ? $in['mode'] : 'any';
    $tagIds = array_values(array_unique(array_filter(array_map('intval', (array)($in['tag_ids'] ?? [])))));
    if ($tagIds) {
        $marks = implode(',', array_fill(0, count($tagIds), '?'));
        $st = $pdo->prepare("SELECT id FROM mailaby_tags WHERE id IN ($marks)");
        $st->execute($tagIds);
        $known = array_map('intval', $st->fetchAll(PDO::FETCH_COLUMN));
        if (count($known) !== count($tagIds)) agent_out(422, ['error' => 'Étiquette inconnue']);
    } else {
        $mode = 'all';
    }

    $preheader = mb_substr(trim(strip_tags((string)($in['preheader'] ?? ''))), 0, 200);
    $fromName  = mb_substr(trim(strip_tags((string)($in['from_name'] ?? ''))), 0, 200);
    $name      = mb_substr(trim((string)($in['name'] ?? '')) ?: $subject, 0, 190);

    $pdo->prepare(
        "INSERT INTO mailaby_campaigns (user_id,name,account_id,subject,body_html,attachments_json,throttle_sec,add_unsub,track_opens,track_clicks,
                                        window_start,window_end,daily_cap,segment_mode,segment_tags_json,segment_filter_json,ab_enabled,subject_b,
                                        ab_test_pct,ab_wait_hours,preheader,from_name,reply_to,status,created_at)
         VALUES (?,?,?,?,?,'[]',10,1,1,1,NULL,NULL,0,?,?,NULL,0,'',20,4,?,?,'', 'draft', ?)"
    )->execute([(int)$acc['user_id'], $name, (int)$acc['id'], $subject, $html, $mode, json_encode($tagIds), $preheader, $fromName, mailaby_now()]);
    $id = (int)$pdo->lastInsertId();

    $status = 'draft';
    $n = null;
    if (!empty($in['launch'])) {
        $n = mailaby_build_recipients($pdo, $id);
        if ($n === 0) {
            agent_out(422, ['error' => 'Aucun destinataire (contacts vides ou tous désabonnés)', 'id' => $id, 'status' => 'draft']);
        }
        $sendAt = trim(str_replace('T', ' ', (string)($in['send_at'] ?? '')));
        if ($sendAt !== '') {
            if (strlen($sendAt) === 16) $sendAt .= ':00';
            if (!preg_match('/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/', $sendAt)) {
                agent_out(422, ['error' => 'Date d’envoi invalide', 'id' => $id, 'status' => 'draft']);
            }
            $pdo->prepare("UPDATE mailaby_campaigns SET status='scheduled', send_at=? WHERE id=?")->execute([$sendAt, $id]);
            $status = 'scheduled';
        } else {
            $pdo->prepare("UPDATE mailaby_campaigns SET status='sending', send_at=NULL WHERE id=?")->execute([$id]);
            $status = 'sending';
        }
    }
    agent_out(201, ['id' => $id, 'status' => $status, 'recipients' => $n]);
}

agent_out(404, ['error' => 'Action inconnue']);
