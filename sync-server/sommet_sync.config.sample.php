<?php
/**
 * Copy to `sommet_sync.config.php` next to sync.php and set a long random token.
 * Generate one on the NAS with:  openssl rand -hex 24
 * The SAME token goes into each Sommet install (Settings -> Sync -> My own server / NAS).
 * Keep sommet_sync.config.php out of any public/web-listing and out of git.
 */
return [
    'token' => 'put_your_token_here',
    // After a rotation (php rotate_token.php), the old token keeps working for a while and every
    // device that still uses it gets the new one back and saves it. 'from' is optional.
    // 'previous_tokens' => [['token' => 'old_token', 'until' => 1767225600, 'from' => ['100.64.0.0/10']]],
    // Storage: 'json' (default, any PHP), 'sqlite' (needs pdo_sqlite enabled in the PHP profile),
    // or 'shadow' (serve from JSON, replay every request on SQLite, log to sommet-data/shadow.log).
    // 'store' => 'json',
    // 'sqlite_path' => __DIR__ . '/sommet-data/sommet.db',
];
