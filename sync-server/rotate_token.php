<?php
/**
 * Rotate the Sommet Sync token (sync/11). Run on the server, next to sync.php:
 *
 *     php rotate_token.php [days] [cidr ...]
 *
 * Writes a new random token into sommet_sync.config.php and keeps the old one in
 * 'previous_tokens' for <days> (default 30), optionally only from the given address prefixes.
 * Every device that syncs in that window gets the new token back and saves it; nobody pastes it.
 * Prints nothing secret. Keeps a backup of the config as sommet_sync.config.php.bak-<time>.
 */
if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
$file = __DIR__ . '/sommet_sync.config.php';
$cfg = is_file($file) ? (require $file) : [];
if (!is_array($cfg) || empty($cfg['token'])) { fwrite(STDERR, "no token in $file\n"); exit(1); }
$days = isset($argv[1]) ? max(1, (int)$argv[1]) : 30;
$from = array_slice($argv, 2);
$now = time();
$prev = [];
foreach ((array)($cfg['previous_tokens'] ?? []) as $p) {
    if (is_array($p) && isset($p['until']) && (int)$p['until'] >= $now) $prev[] = $p;
}
$old = ['token' => (string)$cfg['token'], 'until' => $now + $days * 86400];
if ($from) $old['from'] = $from;
$prev[] = $old;
$cfg['token'] = bin2hex(random_bytes(24));
$cfg['previous_tokens'] = $prev;
copy($file, $file . '.bak-' . date('Ymd-His', $now));
$tmp = $file . '.tmp';
file_put_contents($tmp, "<?php\n// Written by rotate_token.php " . date('c', $now) . "\nreturn " . var_export($cfg, true) . ";\n");
@chmod($tmp, fileperms($file) & 0777);
rename($tmp, $file);
echo "New token written. The old one works until " . date('Y-m-d H:i', $old['until'])
    . ($from ? ' from ' . implode(', ', $from) : '') . ".\n";
