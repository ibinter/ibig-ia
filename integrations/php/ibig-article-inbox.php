<?php
/**
 * IBIG — module d'entrée des articles (cahier des charges, section 11).
 *
 * Reçoit de l'agent IA un article EN BROUILLON et l'enregistre pour relecture.
 * Rien n'est publié : un humain relit puis met en ligne.
 *
 * Sécurité :
 *  - POST JSON uniquement, 1 Mo maximum ;
 *  - signature HMAC-SHA256 de "<horodatage>.<corps>" avec un secret partagé ;
 *  - horodatage à ± 5 minutes et signature à usage unique (anti-rejeu) ;
 *  - contenu actif (script, gestionnaires on…, javascript:) refusé ;
 *  - brouillons stockés hors de la racine web.
 *
 * Installation : voir README.md de ce dossier.
 * Compatible PHP 7.4 et plus, sans dépendance.
 */

declare(strict_types=1);

const IBIG_MAX_BODY = 1048576;
const IBIG_MAX_SKEW = 300;

function ibig_reply(int $status, array $data): void
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_UNESCAPED_UNICODE);
    exit;
}

function ibig_load_config(): array
{
    $path = getenv('IBIG_INBOX_CONFIG') ?: __DIR__ . '/ibig-inbox-config.php';
    if (!is_file($path)) {
        ibig_reply(500, ['ok' => false, 'erreur' => 'configuration absente']);
    }
    $config = require $path;
    if (!is_array($config) || strlen((string)($config['secret'] ?? '')) < 32
        || empty($config['drafts_dir'])) {
        ibig_reply(500, ['ok' => false, 'erreur' => 'configuration invalide']);
    }
    return $config;
}

function ibig_header(string $name): string
{
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return isset($_SERVER[$key]) ? trim((string)$_SERVER[$key]) : '';
}

function ibig_handle(): void
{
    if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
        header('Allow: POST');
        ibig_reply(405, ['ok' => false, 'erreur' => 'méthode non autorisée']);
    }
    $config = ibig_load_config();

    $contentType = $_SERVER['CONTENT_TYPE'] ?? ibig_header('Content-Type');
    if (stripos($contentType, 'application/json') !== 0) {
        ibig_reply(415, ['ok' => false, 'erreur' => 'JSON attendu']);
    }
    $length = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    if ($length > IBIG_MAX_BODY) {
        ibig_reply(413, ['ok' => false, 'erreur' => 'article trop volumineux']);
    }
    $body = (string)file_get_contents('php://input', false, null, 0, IBIG_MAX_BODY + 1);
    if (strlen($body) > IBIG_MAX_BODY) {
        ibig_reply(413, ['ok' => false, 'erreur' => 'article trop volumineux']);
    }

    // --- Authentification : horodatage + signature HMAC
    $timestamp = ibig_header('X-IBIG-Timestamp');
    $signature = strtolower(ibig_header('X-IBIG-Signature'));
    if (!ctype_digit($timestamp) || abs(time() - (int)$timestamp) > IBIG_MAX_SKEW) {
        ibig_reply(401, ['ok' => false, 'erreur' => 'horodatage invalide']);
    }
    $expected = hash_hmac('sha256', $timestamp . '.' . $body, (string)$config['secret']);
    if (!preg_match('/^[a-f0-9]{64}$/', $signature) || !hash_equals($expected, $signature)) {
        ibig_reply(401, ['ok' => false, 'erreur' => 'signature invalide']);
    }

    $dir = rtrim((string)$config['drafts_dir'], '/');
    if (!is_dir($dir) && !mkdir($dir, 0750, true)) {
        ibig_reply(500, ['ok' => false, 'erreur' => 'dossier des brouillons inaccessible']);
    }

    // --- Anti-rejeu : chaque signature ne sert qu'une fois
    $nonces = $dir . '/.signatures';
    if (!is_dir($nonces)) {
        mkdir($nonces, 0750, true);
    }
    foreach (glob($nonces . '/*') ?: [] as $old) {
        if (filemtime($old) < time() - 2 * IBIG_MAX_SKEW) {
            @unlink($old);
        }
    }
    $nonce = @fopen($nonces . '/' . $signature, 'x');
    if ($nonce === false) {
        ibig_reply(409, ['ok' => false, 'erreur' => 'requête déjà reçue']);
    }
    fclose($nonce);

    // --- Validation du contenu
    $data = json_decode($body, true);
    if (!is_array($data)) {
        ibig_reply(400, ['ok' => false, 'erreur' => 'JSON invalide']);
    }
    foreach (['titre' => 300, 'slug' => 120, 'contenu_html' => IBIG_MAX_BODY] as $field => $max) {
        if (!isset($data[$field]) || !is_string($data[$field]) || trim($data[$field]) === ''
            || strlen($data[$field]) > $max) {
            ibig_reply(400, ['ok' => false, 'erreur' => "champ $field manquant ou invalide"]);
        }
    }
    // Uniquement à l'intérieur des balises : un article peut parler de « JavaScript : … ».
    if (preg_match('/<\s*(script|iframe|object|embed|style)\b|<[^>]*\son[a-z]+\s*=|'
        . '<[^>]*javascript\s*:/i', $data['contenu_html'])) {
        ibig_reply(422, ['ok' => false, 'erreur' => 'contenu actif refusé']);
    }
    $slug = trim((string)preg_replace('/[^a-z0-9-]+/', '-', strtolower($data['slug'])), '-');
    $slug = substr($slug !== '' ? $slug : 'article', 0, 80);

    $draft = [
        'statut' => 'brouillon',
        'recu_le' => gmdate('c'),
        'titre' => $data['titre'],
        'slug' => $slug,
        'meta_description' => is_string($data['meta_description'] ?? null)
            ? $data['meta_description'] : '',
        'mot_cle' => is_string($data['mot_cle'] ?? null) ? $data['mot_cle'] : '',
        // « article » (blog) ou « page » (page produit)
        'type' => (($data['type'] ?? '') === 'page') ? 'page' : 'article',
        'contenu_html' => $data['contenu_html'],
    ];

    // Intégration au site : si la configuration fournit 'on_draft', elle enregistre le
    // brouillon dans la base du site et renvoie son identifiant.
    if (isset($config['on_draft']) && is_callable($config['on_draft'])) {
        $id = (string)call_user_func($config['on_draft'], $draft);
    } else {
        $id = gmdate('Ymd-His') . '-' . $slug . '-' . bin2hex(random_bytes(3));
        $file = $dir . '/' . $id . '.json';
        if (file_put_contents($file, json_encode($draft, JSON_UNESCAPED_UNICODE
                | JSON_PRETTY_PRINT), LOCK_EX) === false) {
            ibig_reply(500, ['ok' => false, 'erreur' => 'enregistrement impossible']);
        }
        chmod($file, 0640);
    }
    ibig_reply(201, ['ok' => true, 'id' => $id, 'statut' => 'brouillon']);
}

ibig_handle();
