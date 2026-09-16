<?php
/**
 * Copy to `sommet_sync.config.php` next to sync.php and set a long random token.
 * Generate one on the NAS with:  openssl rand -hex 24
 * The SAME token goes into each Sommet install (Settings -> Sync -> My own server / NAS).
 * Keep sommet_sync.config.php out of any public/web-listing and out of git.
 */
return [
    'token' => 'put_your_token_here',
];
