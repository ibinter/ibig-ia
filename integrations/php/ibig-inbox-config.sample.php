<?php
// Copier en ibig-inbox-config.php, HORS de la racine web si possible
// (sinon, définir la variable d'environnement IBIG_INBOX_CONFIG vers son chemin).
return [
    // Secret partagé avec l'agent (variable d'environnement indiquée dans
    // config/sites.yaml, champ secret_env). 32 caractères minimum :
    //   php -r "echo bin2hex(random_bytes(32)), PHP_EOL;"
    'secret' => getenv('IBIG_INBOX_SECRET') ?: '',

    // Dossier des brouillons reçus, hors de la racine web.
    'drafts_dir' => '/var/lib/ibig/brouillons',

    // Facultatif : enregistrer directement le brouillon dans la base du site.
    // 'on_draft' => function (array $draft): string {
    //     $pdo = new PDO('mysql:host=localhost;dbname=site', 'user', 'motdepasse');
    //     $stmt = $pdo->prepare('INSERT INTO articles (titre, slug, contenu, statut)
    //                            VALUES (?, ?, ?, "brouillon")');
    //     $stmt->execute([$draft['titre'], $draft['slug'], $draft['contenu_html']]);
    //     return (string)$pdo->lastInsertId();
    // },
];
