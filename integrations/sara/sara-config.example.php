<?php
/**
 * Réglages du relais SARA. Copier ce fichier en « sara-config.php » (jamais dans Git, jamais
 * dans le JavaScript) et le protéger : le serveur du site ne doit pas le servir en clair
 * (il ne contient que du PHP, il est exécuté, pas affiché).
 */
return [
    // Adresse de l'Agent IA IBIG (sans « / » final)
    'agent_url' => 'https://agents.ibigsoft.com',
    // Même valeur que IBIG_SARA_API_KEY dans l'agent (32 caractères au minimum)
    'api_key' => '',
    // Pôle dont la base de connaissances répond : SOFT, EDUFORM, IMMOTRUST, MARKET, DIGITAL…
    'pole' => 'SOFT',
    // Sites autorisés à interroger le relais depuis un navigateur (origine exacte)
    'allowed_origins' => ['https://ibigsoft.com'],
];
