<?php
/**
 * SARA — relais CÔTÉ SERVEUR entre le widget du site et l'Agent IA IBIG.
 *
 * Le navigateur parle à CE script ; lui seul connaît la clé de l'agent (jamais envoyée au
 * navigateur). Il vérifie l'origine, limite le débit par visiteur, puis relaie la question
 * à POST {agent_url}/api/sara/question et ne renvoie que : reponse, sources, transmis, ticket.
 * Panne de l'agent : SARA reste polie et passe la main à un conseiller.
 *
 * Installation : voir LISEZMOI.md (copier ce fichier et sara-config.php dans le site).
 */

declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

const FALLBACK = ['reponse' => "Je transmets votre question à un conseiller.", 'sources' => [],
                  'transmis' => true, 'ticket' => null];

function sara_out(int $status, array $data): never
{
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

$cfgFile = __DIR__ . '/sara-config.php';
$cfg = is_file($cfgFile) ? require $cfgFile : [];
$agent = rtrim((string)($cfg['agent_url'] ?? ''), '/');
$key   = (string)($cfg['api_key'] ?? '');
if ($agent === '' || strlen($key) < 32) {
    sara_out(503, ['erreur' => 'SARA non configurée sur ce site']);
}
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    sara_out(405, ['erreur' => 'POST attendu']);
}

/* Origine : seul le site (ou les sites listés) peut interroger ce relais depuis un navigateur */
$origin = (string)($_SERVER['HTTP_ORIGIN'] ?? '');
$allowed = (array)($cfg['allowed_origins'] ?? []);
if ($origin !== '' && $allowed && !in_array($origin, $allowed, true)) {
    sara_out(403, ['erreur' => 'Origine non autorisée']);
}

/* Débit par visiteur (adresse IP) : 20 questions par 10 minutes, fichier temporaire */
$ip = (string)($_SERVER['REMOTE_ADDR'] ?? '?');
$bucket = sys_get_temp_dir() . '/sara-rate-' . hash('sha256', $ip . '|' . $key);
$now = time();
$hits = is_file($bucket) ? array_filter(array_map('intval', explode(',', (string)file_get_contents($bucket))),
                                        fn(int $t): bool => $now - $t < 600) : [];
if (count($hits) >= 20) {
    sara_out(429, ['erreur' => 'Trop de questions, réessayez dans quelques minutes.']);
}
$hits[] = $now;
@file_put_contents($bucket, implode(',', $hits), LOCK_EX);

$in = json_decode((string)file_get_contents('php://input'), true);
$message = is_array($in) ? trim((string)($in['message'] ?? '')) : '';
if ($message === '' || mb_strlen($message) > 2000) {
    sara_out(400, ['erreur' => 'Message vide ou trop long (2000 caractères au plus)']);
}
$conversation = preg_replace('/[^A-Za-z0-9_-]/', '', (string)($in['conversation'] ?? ''));
$contact = mb_substr(trim((string)($in['contact'] ?? '')), 0, 300);

$ch = curl_init($agent . '/api/sara/question');
curl_setopt_array($ch, [
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => json_encode([
        'question' => $message,
        'pole' => (string)($cfg['pole'] ?? 'SOFT'),
        'contact' => $contact,
        'conversation' => substr($conversation, 0, 100),
    ], JSON_UNESCAPED_UNICODE),
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 60,
    CURLOPT_HTTPHEADER => ['Content-Type: application/json', 'Authorization: Bearer ' . $key],
]);
$body = curl_exec($ch);
$status = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

$data = is_string($body) ? json_decode($body, true) : null;
if ($status === 429) {
    sara_out(429, ['erreur' => 'Trop de questions, réessayez dans quelques minutes.']);
}
if ($status !== 200 || !is_array($data)) {
    sara_out(200, FALLBACK);
}
sara_out(200, [
    'reponse'  => (string)($data['reponse'] ?? FALLBACK['reponse']),
    'sources'  => array_map(fn($s) => ['titre' => (string)($s['titre'] ?? ''), 'section' => (string)($s['section'] ?? '')],
                            array_slice((array)($data['sources'] ?? []), 0, 5)),
    'transmis' => (bool)($data['transmis'] ?? false),
    'ticket'   => $data['ticket'] ?? null,
]);
