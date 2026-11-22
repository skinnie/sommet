<?php
/**
 * Sommet Sync — self-hosted shared-app-database endpoint (#SYNC-1).
 *
 * Storage (sommet_store.php, config 'store'): 'json' (default) = one JSON file per collection,
 * exclusive lock + atomic rename, works on any PHP; 'sqlite' = one SQLite file, needs pdo_sqlite
 * (Synology's Web Station ships it disabled: tick it in the PHP profile); 'shadow' = served from
 * JSON while every request is replayed on SQLite and logged to sommet-data/shadow.log. Every Sommet install
 * (Linux/Mac/Windows/phone) talks to it over HTTP with a token and merges the same way Ember does:
 * per-record `uid`, a `deleted` tombstone set, last-writer-wins by `updated_at`.
 * See docs/shared_app_db_design.md.
 *
 * Deploy: drop this file + sommet_store.php + sommet_sync.config.php into a web folder (e.g. Synology Web Station).
 * It creates sommet-data/ (collection JSONs) and sommet-data/blobs/ next to itself on first write.
 * The web user (Synology: `http`) must be able to WRITE this folder.
 *
 * API (token in header `X-Sommet-Token`):
 *   GET  sync.php?c=<collection>&since=<ms>   -> {records:[...], deleted:[uid...], now:<ms>}
 *   POST sync.php?c=<collection>  body {records,deleted}  -> {ok:true, now:<ms>, applied:N}
 *   GET  sync.php?blob=1&uid=<uid>&fmt=gpx    -> raw track bytes (404 if none)
 *   POST sync.php?blob=1&uid=<uid>&fmt=gpx    -> raw body stored as the blob; {ok:true}
 */

header('Content-Type: application/json');
require __DIR__ . '/sommet_store.php';

// ---- config -------------------------------------------------------------------------------
$cfgFile = __DIR__ . '/sommet_sync.config.php';
$CONFIG = is_file($cfgFile) ? (require $cfgFile) : [];
$TOKEN = isset($CONFIG['token']) ? (string)$CONFIG['token'] : (getenv('SOMMET_SYNC_TOKEN') ?: '');
$PREVIOUS = isset($CONFIG['previous_tokens']) && is_array($CONFIG['previous_tokens']) ? $CONFIG['previous_tokens'] : [];
$DATA_DIR = isset($CONFIG['data_dir']) ? (string)$CONFIG['data_dir'] : (__DIR__ . '/sommet-data');
$BLOB_DIR = $DATA_DIR . '/blobs';
$MAX_BODY = 32 * 1024 * 1024; // 32 MB — a big FIT/GPX is a few MB; cap abuse.
$STORE_MODE = isset($CONFIG['store']) ? (string)$CONFIG['store'] : 'json'; // json | sqlite | shadow
$SQLITE_PATH = isset($CONFIG['sqlite_path']) ? (string)$CONFIG['sqlite_path'] : ($DATA_DIR . '/sommet.db');
// weight: one reading per day (uid = the date), so a device without intervals.icu still has them.
// routes, pois: the route library and the POI library (one record each; a route's GPX is a blob
// under the uid "route:<record uid>"). A server without them answers 400 "unknown collection",
// which the apps read as "this server is older" and skip.
$COLLECTIONS = ['activities', 'gear', 'gear_reminder', 'gear_assignment', 'activity_gear', 'weight',
                'routes', 'pois'];

// ---- helpers ------------------------------------------------------------------------------
function fail($code, $msg) {
    http_response_code($code);
    echo json_encode(['error' => $msg]);
    exit;
}
function now_ms() { return (int)round(microtime(true) * 1000); }

// Token rotation (sync/11): a token listed in 'previous_tokens' still works until its 'until'
// (unix seconds), optionally only from the address prefixes in 'from' (e.g. the tailnet
// '100.64.0.0/10'). A device that uses one gets the current token back in the reply header
// X-Sommet-Token-New and saves it, so a new token reaches every device without pasting.
function ip_in_cidr($ip, $cidr) {
    if (strpos($cidr, '/') === false) $cidr .= (strpos($cidr, ':') === false ? '/32' : '/128');
    [$net, $bits] = explode('/', $cidr, 2);
    $a = @inet_pton($ip); $b = @inet_pton($net);
    if ($a === false || $b === false || strlen($a) !== strlen($b)) return false;
    $bits = (int)$bits;
    $whole = intdiv($bits, 8); $rest = $bits % 8;
    if (substr($a, 0, $whole) !== substr($b, 0, $whole)) return false;
    if ($rest === 0) return true;
    $mask = chr((0xff << (8 - $rest)) & 0xff);
    return (($a[$whole] & $mask) === ($b[$whole] & $mask));
}
function previous_token_ok($prev, $got, $ip, $now) {
    if (!is_array($prev) || !isset($prev['token']) || (string)$prev['token'] === '') return false;
    if (!hash_equals((string)$prev['token'], $got)) return false;
    if (!isset($prev['until']) || (int)$prev['until'] < $now) return false;
    if (!empty($prev['from'])) {
        foreach ((array)$prev['from'] as $cidr) if (ip_in_cidr($ip, (string)$cidr)) return true;
        return false;
    }
    return true;
}
function require_token($TOKEN, $PREVIOUS) {
    if ($TOKEN === '') fail(500, 'server has no token configured');
    $got = (string)(isset($_SERVER['HTTP_X_SOMMET_TOKEN']) ? $_SERVER['HTTP_X_SOMMET_TOKEN'] : '');
    if ($got !== '' && hash_equals($TOKEN, $got)) return;
    $ip = isset($_SERVER['REMOTE_ADDR']) ? (string)$_SERVER['REMOTE_ADDR'] : '';
    foreach ((array)$PREVIOUS as $prev) {
        if ($got !== '' && previous_token_ok($prev, $got, $ip, time())) {
            header('X-Sommet-Token-New: ' . $TOKEN);
            return;
        }
    }
    fail(401, 'bad or missing token');
}
function blob_path($BLOB_DIR, $uid, $fmt) {
    // Never trust uid as a path. Address blobs by a hash; keep fmt for the extension only.
    $fmt = preg_match('/^[a-z0-9]{1,8}$/', (string)$fmt) ? $fmt : 'bin';
    return $BLOB_DIR . '/' . hash('sha256', (string)$uid) . '.' . $fmt;
}
function ensure_dir($d) {
    if (!is_dir($d)) @mkdir($d, 0770, true);
    if (!is_dir($d) || !is_writable($d)) fail(500, 'data dir not writable');
}
// ---- routing ------------------------------------------------------------------------------
require_token($TOKEN, $PREVIOUS);
$method = $_SERVER['REQUEST_METHOD'];

// --- blob transfer (raw bytes, not JSON) ---
if (isset($_GET['blob'])) {
    ensure_dir($BLOB_DIR);
    $uid = isset($_GET['uid']) ? $_GET['uid'] : '';
    if ($uid === '') fail(400, 'blob needs uid');
    $fmt = isset($_GET['fmt']) ? $_GET['fmt'] : 'gpx';
    $path = blob_path($BLOB_DIR, $uid, $fmt);
    if ($method === 'GET') {
        if (!is_file($path)) fail(404, 'no blob');
        header('Content-Type: application/octet-stream');
        header('Content-Length: ' . filesize($path));
        readfile($path);
        exit;
    }
    if ($method === 'POST') {
        $raw = file_get_contents('php://input', false, null, 0, $MAX_BODY + 1);
        if (strlen($raw) > $MAX_BODY) fail(413, 'blob too large');
        $tmp = $path . '.tmp';
        if (@file_put_contents($tmp, $raw, LOCK_EX) === false || !rename($tmp, $path)) fail(500, 'blob write failed');
        echo json_encode(['ok' => true]);
        exit;
    }
    fail(405, 'method');
}

// --- record collections ---
$c = isset($_GET['c']) ? $_GET['c'] : '';
if (!in_array($c, $COLLECTIONS, true)) fail(400, 'unknown collection');
$now = now_ms();

if ($method === 'POST') {
    $raw = file_get_contents('php://input', false, null, 0, $MAX_BODY + 1);
    if (strlen($raw) > $MAX_BODY) fail(413, 'body too large');
    $body = json_decode($raw, true);
    if (!is_array($body)) fail(400, 'bad json');
    $records = isset($body['records']) && is_array($body['records']) ? $body['records'] : [];
    $tombs   = isset($body['deleted']) && is_array($body['deleted']) ? $body['deleted'] : [];
} elseif ($method !== 'GET') {
    fail(405, 'method');
}
$since = isset($_GET['since']) ? (int)$_GET['since'] : 0;

// The request against one store: [answer, milliseconds].
$run = function (SommetStore $st) use ($method, $c, $since, $now, &$records, &$tombs) {
    $t0 = microtime(true);
    if ($method === 'GET') {
        $ans = $st->pull($c, $since, $now);
    } else {
        $ans = ['ok' => true, 'now' => $now, 'applied' => $st->push($c, $records, $tombs, $now)];
    }
    return [$ans, (microtime(true) - $t0) * 1000];
};

try {
    $primary = $STORE_MODE === 'sqlite' ? new SqliteStore($SQLITE_PATH) : new JsonStore($DATA_DIR);
    [$answer, $ms] = $run($primary);
} catch (StoreError $e) {
    fail(500, $e->getMessage());
}
echo json_encode($answer);

// Shadow test: the JSON store answered; now replay the same request on SQLite, after the reply
// has gone out, and log how long each took and whether they agree. Never affects the device.
if ($STORE_MODE === 'shadow') {
    if (function_exists('fastcgi_finish_request')) fastcgi_finish_request();
    $line = ['t' => $now, 'm' => $method, 'c' => $c, 'since' => $since, 'json_ms' => round($ms, 1)];
    try {
        $sh = new SqliteStore($SQLITE_PATH);
        $imported = $sh->importFromJson($DATA_DIR, $COLLECTIONS);
        if ($imported >= 0) {
            $line['imported'] = $imported; // the JSON store already holds this request's changes
        } else {
            [$sAns, $sMs] = $run($sh);
            $line['sqlite_ms'] = round($sMs, 1);
            if ($method === 'GET') {
                $line['n'] = count($answer['records']);
                $line['match'] = sommet_pull_fingerprint($answer) === sommet_pull_fingerprint($sAns);
                if (!$line['match']) $line['n_sqlite'] = count($sAns['records']);
            } else {
                $line['in'] = count($records) + count($tombs);
                $line['applied'] = $answer['applied'];
                $line['match'] = $answer['applied'] === $sAns['applied'];
                if (!$line['match']) $line['applied_sqlite'] = $sAns['applied'];
            }
        }
    } catch (Throwable $e) {
        $line['error'] = substr($e->getMessage(), 0, 300);
    }
    $log = $DATA_DIR . '/shadow.log';
    if (is_file($log) && filesize($log) > 5 * 1024 * 1024) @rename($log, $log . '.1');
    @file_put_contents($log, json_encode($line) . "\n", FILE_APPEND | LOCK_EX);
}
exit;
