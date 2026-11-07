<?php
/**
 * Exemple : relayer une question du chat SARA vers l'agent IA IBIG, CÔTÉ SERVEUR.
 *
 * La clé ne doit jamais apparaître dans le JavaScript du navigateur : le widget SARA
 * envoie la question à ce script du site, qui appelle l'API de l'agent avec la clé.
 */

declare(strict_types=1);

function sara_ask(string $question, string $contact = '', string $conversation = ''): array
{
    $payload = json_encode([
        'question' => mb_substr($question, 0, 2000),
        'pole' => 'SOFT',
        'contact' => $contact,             // mail ou téléphone, si le client l'a donné
        'conversation' => $conversation,   // identifiant de la conversation du chat
    ], JSON_UNESCAPED_UNICODE);

    $ch = curl_init(getenv('IBIG_AGENT_URL') . '/api/sara/question');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $payload,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 60,
        CURLOPT_HTTPHEADER => [
            'Content-Type: application/json',
            'Authorization: Bearer ' . getenv('IBIG_SARA_API_KEY'),
        ],
    ]);
    $body = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    $data = is_string($body) ? json_decode($body, true) : null;
    if ($status !== 200 || !is_array($data)) {
        // En cas de panne, SARA reste polie et passe la main.
        return ['reponse' => "Je transmets votre question à un conseiller.", 'transmis' => true];
    }
    // $data : reponse, sources [{titre, section}], transmis (bool), ticket (numéro ou null)
    return $data;
}
