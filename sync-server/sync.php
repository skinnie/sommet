<?php
/**
 * Sommet Sync — self-hosted shared-app-database endpoint (#SYNC-1).
 *
 * FILE-BASED store (no SQLite): one JSON file per collection, written with an exclusive lock +
 * atomic rename, exactly like the app's proven Ember sync.php on the same NAS. This deliberately
 * avoids PDO-SQLite, which Synology's Web Station PHP profile ships disabled. Every Sommet install
 * (Linux/Mac/Windows/phone) talks to it over HTTP with a token and merges the same way Ember does:
 * per-record `uid`, a `deleted` tombstone set, last-writer-wins by `updated_at`.
 * See docs/shared_app_db_design.md.
 *
 * Deploy: drop this file + sommet_sync.config.php into a web folder (e.g. Synology Web Station).
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

// ---- config -------------------------------------------------------------------------------
$cfgFile = __DIR__ . '/sommet_sync.config.php';
$CONFIG = is_file($cfgFile) ? (require $cfgFile) : [];
$TOKEN = isset($CONFIG['token']) ? (string)$CONFIG['token'] : (getenv('SOMMET_SYNC_TOKEN') ?: '');
$DATA_DIR = isset($CONFIG['data_dir']) ? (string)$CONFIG['data_dir'] : (__DIR__ . '/sommet-data');
$BLOB_DIR = $DATA_DIR . '/blobs';
$MAX_BODY = 32 * 1024 * 1024; // 32 MB — a big FIT/GPX is a few MB; cap abuse.
$COLLECTIONS = ['activities', 'gear', 'gear_reminder', 'gear_assignment', 'activity_gear'];

// ---- helpers ------------------------------------------------------------------------------
function fail($code, $msg) {
    http_response_code($code);
    echo json_encode(['error' => $msg]);
    exit;
}
function now_ms() { return (int)round(microtime(true) * 1000); }

function require_token($TOKEN) {
    if ($TOKEN === '') fail(500, 'server has no token configured');
    $got = isset($_SERVER['HTTP_X_SOMMET_TOKEN']) ? $_SERVER['HTTP_X_SOMMET_TOKEN'] : '';
    if (!hash_equals($TOKEN, (string)$got)) fail(401, 'bad or missing token');
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
function coll_file($DATA_DIR, $c) { return $DATA_DIR . '/' . $c . '.json'; }

// Read-modify-write a collection under an exclusive lock. $mutator($data) may change $data;
// return true to persist. $data shape: ['records'=>{uid:rec}, 'deleted'=>{uid:ts}].
function with_collection($DATA_DIR, $c, $mutator) {
    ensure_dir($DATA_DIR);
    $file = coll_file($DATA_DIR, $c);
    $lock = fopen($file . '.lock', 'c');
    if (!$lock) fail(500, 'lock failed');
    flock($lock, LOCK_EX);
    try {
        $data = ['records' => new stdClass(), 'deleted' => new stdClass()];
        if (is_file($file)) {
            $raw = file_get_contents($file);
            $j = json_decode($raw, true);
            if (is_array($j)) {
                $data['records'] = isset($j['records']) && is_array($j['records']) ? $j['records'] : [];
                $data['deleted'] = isset($j['deleted']) && is_array($j['deleted']) ? $j['deleted'] : [];
            }
        } else {
            $data['records'] = [];
            $data['deleted'] = [];
        }
        $ret = $mutator($data);
        if ($ret !== false) {
            $tmp = $file . '.tmp';
            if (file_put_contents($tmp, json_encode($data), LOCK_EX) === false) fail(500, 'write failed');
            if (!rename($tmp, $file)) fail(500, 'commit failed');
        }
        return $ret;
    } finally {
        flock($lock, LOCK_UN);
        fclose($lock);
    }
}

// ---- routing ------------------------------------------------------------------------------
require_token($TOKEN);
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

if ($method === 'GET') {
    $since = isset($_GET['since']) ? (int)$_GET['since'] : 0;
    $out = ['records' => [], 'deleted' => [], 'now' => $now];
    with_collection($DATA_DIR, $c, function ($data) use (&$out, $since) {
        foreach ($data['records'] as $uid => $rec) {
            $ua = isset($rec['updated_at']) ? (int)$rec['updated_at'] : 0;
            if ($ua > $since) {
                $rec['uid'] = $uid;
                $out['records'][] = $rec;
            }
        }
        foreach ($data['deleted'] as $uid => $ts) {
            if ((int)$ts > $since) $out['deleted'][] = (string)$uid;
        }
        return false; // read-only, don't rewrite
    });
    echo json_encode($out);
    exit;
}

if ($method === 'POST') {
    $raw = file_get_contents('php://input', false, null, 0, $MAX_BODY + 1);
    if (strlen($raw) > $MAX_BODY) fail(413, 'body too large');
    $body = json_decode($raw, true);
    if (!is_array($body)) fail(400, 'bad json');
    $records = isset($body['records']) && is_array($body['records']) ? $body['records'] : [];
    $tombs   = isset($body['deleted']) && is_array($body['deleted']) ? $body['deleted'] : [];

    $applied = with_collection($DATA_DIR, $c, function (&$data) use ($records, $tombs, $now) {
        $n = 0;
        foreach ($records as $r) {
            $uid = isset($r['uid']) ? (string)$r['uid'] : '';
            if ($uid === '') continue;
            $ua = isset($r['updated_at']) ? (int)$r['updated_at'] : $now;
            // last-writer-wins: keep ours if it is newer; a tombstone of the same uid also blocks.
            if (isset($data['records'][$uid]) && (int)$data['records'][$uid]['updated_at'] > $ua) continue;
            if (isset($data['deleted'][$uid]) && (int)$data['deleted'][$uid] >= $ua) continue;
            $rec = $r;
            unset($rec['uid']);
            $rec['updated_at'] = $ua;
            $data['records'][$uid] = $rec;
            $n++;
        }
        foreach ($tombs as $uid) {
            $uid = (string)$uid;
            if ($uid === '') continue;
            unset($data['records'][$uid]);   // drop the live row
            $prev = isset($data['deleted'][$uid]) ? (int)$data['deleted'][$uid] : 0;
            $data['deleted'][$uid] = max($prev, $now); // bump so it propagates via since-cursor
            $n++;
        }
        return $n;
    });
    echo json_encode(['ok' => true, 'now' => $now, 'applied' => $applied]);
    exit;
}

fail(405, 'method');
