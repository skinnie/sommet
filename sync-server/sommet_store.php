<?php
/**
 * Sommet Sync storage: the same merge rules over two backends.
 *
 *   JsonStore   - one JSON file per collection (`<c>.json` = {records:{uid:rec}, deleted:{uid:ts}}),
 *                 exclusive lock + atomic tmp->rename. Works on any PHP (Synology's Web Station
 *                 ships pdo_sqlite disabled).
 *   SqliteStore - one SQLite file (WAL). Reads and writes touch only the rows involved instead of
 *                 the whole collection. Needs pdo_sqlite.
 *
 * The merge rules (last-writer-wins by updated_at, tombstones, `srv` receive time) live once, in
 * SommetStore::push(); the backends only implement the row operations. sync.php picks the backend
 * from its config ('store' => 'json' | 'sqlite' | 'shadow'); 'shadow' serves from JSON and replays
 * every request on SQLite, logging timings and whether the answers match.
 * See docs/shared_app_db_design.md.
 */

class StoreError extends RuntimeException {}

abstract class SommetStore {
    abstract protected function begin(string $c): void;
    abstract protected function commit(bool $changed): void;
    abstract protected function abort(): void;
    /** updated_at of the live record, or null when there is none. */
    abstract protected function recUpdatedAt(string $uid): ?int;
    /** Tombstone time, or null when there is none. */
    abstract protected function tombTs(string $uid): ?int;
    /** $rec has no uid/srv and already carries the final updated_at. */
    abstract protected function putRec(string $uid, array $rec, int $ua, int $srv): void;
    abstract protected function delRec(string $uid): void;
    abstract protected function putTomb(string $uid, int $ts): void;

    /** Records the server received at or after $since (all when $since <= 0), tombstones after it. */
    abstract public function pull(string $c, int $since, int $now): array;

    /** Applies a device push; returns how many records/tombstones changed something. */
    public function push(string $c, array $records, array $tombs, int $now): int {
        $this->begin($c);
        try {
            $n = 0;
            foreach ($records as $r) {
                if (!is_array($r)) continue;
                $uid = isset($r['uid']) ? (string)$r['uid'] : '';
                if ($uid === '') continue;
                $ua = isset($r['updated_at']) ? (int)$r['updated_at'] : 0;
                $have = $this->recUpdatedAt($uid);
                // updated_at 0 = never edited on the device. A record we already hold stays as it
                // is; a new one gets the server's clock, so devices pulling with a since-cursor
                // receive it (stored as 0 it would never pass the `> since` filter).
                if ($ua <= 0) {
                    if ($have !== null) continue;
                    $ua = $now;
                }
                // Last-writer-wins: keep ours if it is the same edit or newer (devices re-push every
                // record on every sync; re-accepting an unchanged one would give it a new `srv` and
                // send the whole collection to every device again). A tombstone also blocks.
                if ($have !== null && $have >= $ua) continue;
                $tomb = $this->tombTs($uid);
                if ($tomb !== null && $tomb >= $ua) continue;
                $rec = $r;
                unset($rec['uid'], $rec['srv']);
                $rec['updated_at'] = $ua;
                $this->putRec($uid, $rec, $ua, $now); // srv = server receive time (what pulls filter on)
                $n++;
            }
            foreach ($tombs as $uid) {
                $uid = (string)$uid;
                if ($uid === '') continue;
                $this->delRec($uid);
                $prev = $this->tombTs($uid) ?? 0;
                $this->putTomb($uid, max($prev, $now)); // bump so it propagates via since-cursor
                $n++;
            }
            $this->commit($n > 0);
            return $n;
        } catch (Throwable $e) {
            $this->abort();
            throw $e;
        }
    }
}

class JsonStore extends SommetStore {
    private string $dir;
    private $lock = null;
    private string $file = '';
    private array $data = [];

    public function __construct(string $dir) { $this->dir = $dir; }

    private function ensureDir(): void {
        if (!is_dir($this->dir)) @mkdir($this->dir, 0770, true);
        if (!is_dir($this->dir) || !is_writable($this->dir)) throw new StoreError('data dir not writable');
    }
    private function openLocked(string $c, int $mode): void {
        $this->ensureDir();
        $this->file = $this->dir . '/' . $c . '.json';
        $this->lock = fopen($this->file . '.lock', 'c');
        if (!$this->lock) throw new StoreError('lock failed');
        flock($this->lock, $mode);
        $this->data = self::readFile($this->file);
    }
    private function unlock(): void {
        if ($this->lock) { flock($this->lock, LOCK_UN); fclose($this->lock); }
        $this->lock = null;
        $this->data = [];
    }
    /** ['records'=>[uid=>rec], 'deleted'=>[uid=>ts]] of one collection file (empty when missing). */
    public static function readFile(string $file): array {
        $d = ['records' => [], 'deleted' => []];
        if (is_file($file)) {
            $j = json_decode((string)file_get_contents($file), true);
            if (is_array($j)) {
                if (isset($j['records']) && is_array($j['records'])) $d['records'] = $j['records'];
                if (isset($j['deleted']) && is_array($j['deleted'])) $d['deleted'] = $j['deleted'];
            }
        }
        return $d;
    }

    protected function begin(string $c): void { $this->openLocked($c, LOCK_EX); }
    protected function commit(bool $changed): void {
        try {
            if ($changed) { // an all-unchanged re-push no longer rewrites the whole file
                $tmp = $this->file . '.tmp';
                $out = ['records' => $this->data['records'] ?: new stdClass(),
                        'deleted' => $this->data['deleted'] ?: new stdClass()];
                if (file_put_contents($tmp, json_encode($out), LOCK_EX) === false) throw new StoreError('write failed');
                if (!rename($tmp, $this->file)) throw new StoreError('commit failed');
            }
        } finally { $this->unlock(); }
    }
    protected function abort(): void { $this->unlock(); }
    protected function recUpdatedAt(string $uid): ?int {
        return isset($this->data['records'][$uid]) ? (int)$this->data['records'][$uid]['updated_at'] : null;
    }
    protected function tombTs(string $uid): ?int {
        return isset($this->data['deleted'][$uid]) ? (int)$this->data['deleted'][$uid] : null;
    }
    protected function putRec(string $uid, array $rec, int $ua, int $srv): void {
        $rec['srv'] = $srv;
        $this->data['records'][$uid] = $rec;
    }
    protected function delRec(string $uid): void { unset($this->data['records'][$uid]); }
    protected function putTomb(string $uid, int $ts): void { $this->data['deleted'][$uid] = $ts; }

    public function pull(string $c, int $since, int $now): array {
        // Writes swap the file in atomically, so a shared lock is enough for a read.
        $this->openLocked($c, LOCK_SH);
        try {
            $out = ['records' => [], 'deleted' => [], 'now' => $now];
            foreach ($this->data['records'] as $uid => $rec) {
                // Filter on when the SERVER received the record (`srv`), not the device's own
                // `updated_at`: a record another device uploads late carries an old updated_at
                // (2026-11-08: ~35 watch moves and 81 eTrex rides never reached the ThinkPad).
                // `>=` so a write landing in the same millisecond as a pull is not lost.
                // Records stored before that fix have no `srv`.
                $srv = isset($rec['srv']) ? (int)$rec['srv'] : (isset($rec['updated_at']) ? (int)$rec['updated_at'] : 0);
                if ($since <= 0 || $srv >= $since) {
                    unset($rec['srv']);
                    $rec['uid'] = (string)$uid;
                    $out['records'][] = $rec;
                }
            }
            foreach ($this->data['deleted'] as $uid => $ts) {
                if ((int)$ts > $since) $out['deleted'][] = (string)$uid;
            }
            return $out;
        } finally { $this->unlock(); }
    }
}

class SqliteStore extends SommetStore {
    private PDO $db;
    private string $c = '';
    private array $st = [];

    public function __construct(string $path) {
        if (!extension_loaded('pdo_sqlite')) throw new StoreError('pdo_sqlite not enabled in this PHP');
        $this->db = new PDO('sqlite:' . $path, null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION]);
        $this->db->exec('PRAGMA busy_timeout = 15000');
        $this->db->exec('PRAGMA journal_mode = WAL');
        $this->db->exec('PRAGMA synchronous = NORMAL');
        $this->db->exec('CREATE TABLE IF NOT EXISTS records (c TEXT NOT NULL, uid TEXT NOT NULL,
                           updated_at INTEGER NOT NULL, srv INTEGER NOT NULL, body TEXT NOT NULL,
                           PRIMARY KEY (c, uid)) WITHOUT ROWID');
        $this->db->exec('CREATE INDEX IF NOT EXISTS records_srv ON records (c, srv)');
        $this->db->exec('CREATE TABLE IF NOT EXISTS deleted (c TEXT NOT NULL, uid TEXT NOT NULL,
                           ts INTEGER NOT NULL, PRIMARY KEY (c, uid)) WITHOUT ROWID');
        $this->db->exec('CREATE INDEX IF NOT EXISTS deleted_ts ON deleted (c, ts)');
        $this->db->exec('CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)');
    }

    private function q(string $sql): PDOStatement {
        return $this->st[$sql] ??= $this->db->prepare($sql);
    }

    protected function begin(string $c): void { $this->c = $c; $this->db->exec('BEGIN IMMEDIATE'); }
    protected function commit(bool $changed): void { $this->db->exec($changed ? 'COMMIT' : 'ROLLBACK'); }
    protected function abort(): void { if ($this->db->inTransaction()) $this->db->exec('ROLLBACK'); }
    protected function recUpdatedAt(string $uid): ?int {
        $s = $this->q('SELECT updated_at FROM records WHERE c = ? AND uid = ?');
        $s->execute([$this->c, $uid]);
        $v = $s->fetchColumn();
        $s->closeCursor();
        return $v === false ? null : (int)$v;
    }
    protected function tombTs(string $uid): ?int {
        $s = $this->q('SELECT ts FROM deleted WHERE c = ? AND uid = ?');
        $s->execute([$this->c, $uid]);
        $v = $s->fetchColumn();
        $s->closeCursor();
        return $v === false ? null : (int)$v;
    }
    protected function putRec(string $uid, array $rec, int $ua, int $srv): void {
        $this->q('INSERT OR REPLACE INTO records (c, uid, updated_at, srv, body) VALUES (?, ?, ?, ?, ?)')
             ->execute([$this->c, $uid, $ua, $srv, json_encode($rec)]);
    }
    protected function delRec(string $uid): void {
        $this->q('DELETE FROM records WHERE c = ? AND uid = ?')->execute([$this->c, $uid]);
    }
    protected function putTomb(string $uid, int $ts): void {
        $this->q('INSERT OR REPLACE INTO deleted (c, uid, ts) VALUES (?, ?, ?)')->execute([$this->c, $uid, $ts]);
    }

    public function pull(string $c, int $since, int $now): array {
        $out = ['records' => [], 'deleted' => [], 'now' => $now];
        $this->db->exec('BEGIN'); // one consistent snapshot for records + tombstones
        try {
            $s = $since <= 0
                ? $this->q('SELECT uid, body FROM records WHERE c = ?')
                : $this->q('SELECT uid, body FROM records WHERE c = ? AND srv >= ?');
            $s->execute($since <= 0 ? [$c] : [$c, $since]);
            while ($row = $s->fetch(PDO::FETCH_NUM)) {
                $rec = json_decode($row[1], true);
                if (!is_array($rec)) continue;
                $rec['uid'] = (string)$row[0];
                $out['records'][] = $rec;
            }
            $t = $this->q('SELECT uid FROM deleted WHERE c = ? AND ts > ?');
            $t->execute([$c, $since]);
            while (($uid = $t->fetchColumn()) !== false) $out['deleted'][] = (string)$uid;
        } finally { $this->db->exec('COMMIT'); }
        return $out;
    }

    public function meta(string $k): ?string {
        $s = $this->q('SELECT v FROM meta WHERE k = ?');
        $s->execute([$k]);
        $v = $s->fetchColumn();
        $s->closeCursor();
        return $v === false ? null : (string)$v;
    }

    /**
     * One-time copy of the JSON store into this database (skipped when already done). Runs in one
     * transaction, so a crash leaves either nothing or everything. Returns the record count copied,
     * or -1 when the import had already happened.
     */
    public function importFromJson(string $dir, array $collections): int {
        $this->db->exec('BEGIN IMMEDIATE');
        try {
            if ($this->meta('imported_at') !== null) { $this->db->exec('ROLLBACK'); return -1; }
            $n = 0;
            foreach ($collections as $c) {
                $d = JsonStore::readFile($dir . '/' . $c . '.json');
                foreach ($d['records'] as $uid => $rec) {
                    if (!is_array($rec)) continue;
                    $ua = isset($rec['updated_at']) ? (int)$rec['updated_at'] : 0;
                    $srv = isset($rec['srv']) ? (int)$rec['srv'] : $ua; // same fallback as JsonStore::pull
                    unset($rec['srv'], $rec['uid']);
                    $this->q('INSERT OR REPLACE INTO records (c, uid, updated_at, srv, body) VALUES (?, ?, ?, ?, ?)')
                         ->execute([$c, (string)$uid, $ua, $srv, json_encode($rec)]);
                    $n++;
                }
                foreach ($d['deleted'] as $uid => $ts) {
                    $this->q('INSERT OR REPLACE INTO deleted (c, uid, ts) VALUES (?, ?, ?)')
                         ->execute([$c, (string)$uid, (int)$ts]);
                }
            }
            $this->q('INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)')->execute(['imported_at', (string)time()]);
            $this->db->exec('COMMIT');
            return $n;
        } catch (Throwable $e) {
            if ($this->db->inTransaction()) $this->db->exec('ROLLBACK');
            throw $e;
        }
    }
}

/** Order-independent fingerprint of a pull answer, to compare two backends. */
function sommet_pull_fingerprint(array $out): string {
    $recs = $out['records'];
    usort($recs, fn($a, $b) => strcmp((string)$a['uid'], (string)$b['uid']));
    $canon = array_map(function ($r) { ksort($r); return $r; }, $recs);
    $del = $out['deleted'];
    sort($del, SORT_STRING);
    return hash('sha256', json_encode([$canon, $del]));
}
