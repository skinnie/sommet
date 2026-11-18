<?php
/**
 * Copy to `sommet_sync.config.php` next to sync.php and set a long random token.
 * Generate one on the NAS with:  openssl rand -hex 24
 * The SAME token goes into each Sommet install (Settings -> Sync -> My own server / NAS).
 * Keep sommet_sync.config.php out of any public/web-listing and out of git.
 */
return [
    'token' => 'put_your_token_here',
    // Storage: 'json' (default, any PHP), 'sqlite' (needs pdo_sqlite enabled in the PHP profile),
    // or 'shadow' (serve from JSON, replay every request on SQLite, log to sommet-data/shadow.log).
    // 'store' => 'json',
    // 'sqlite_path' => __DIR__ . '/sommet-data/sommet.db',
];
