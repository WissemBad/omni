"""Long operations started from the interface (conversions, playermodels, sound export, indexing, setup steps).

* **One heavy job at a time.** Conversions, exports, indexing and setup steps share the CPU, the disk and the sandbox:
  they wait in a queue (FIFO, a job can be moved to the front) instead of running side by side. Quick, user-driven
  jobs (one playermodel) run at once. An identical job already queued or running is refused.
* **Persistent.** Jobs, their log tail and every result live in ``<workspace>/jobs.sqlite``: the history survives a
  restart, a job that was cut short is marked *interrupted* and can be resumed (``retry``).
* **Observable.** ``version`` changes at every state change; ``wait`` lets the SSE endpoint push updates instead of the
  interface polling. Results are paged (``results``), failures are grouped by cause (``report``).

The job dict keeps its old shape (``id kind label source phase done total error summary started ended cancellable``)
so callers only add what they need: ``heavy``, ``request`` (what a retry replays), ``dedupe``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path

from fastapi import HTTPException

log = logging.getLogger("omni.jobs")

HEAVY_KINDS = {"props", "sounds", "setup", "textures", "maintenance"}
FINAL = ("done", "error", "cancelled", "interrupted")
KEEP = 200                                   # jobs kept in the history
LOG_LINES = 400

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
    id TEXT PRIMARY KEY, kind TEXT, label TEXT, source TEXT, phase TEXT, done INTEGER, total INTEGER, error TEXT,
    summary TEXT, started REAL, ended REAL, cancellable INTEGER, request TEXT, counts TEXT, log TEXT, models INTEGER,
    last_model TEXT, heavy INTEGER);
CREATE TABLE IF NOT EXISTS results(
    job TEXT, seq INTEGER, status TEXT, key TEXT, data TEXT, PRIMARY KEY(job, seq)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS results_status ON results(job, status);
"""

# (pattern, cause): what a failed result's first error is grouped under in the report
_CAUSES = [
    (r"timed out", "studiomdl : délai dépassé (maillage trop lourd)"),
    (r"ACCESS_VIOLATION", "studiomdl : plantage"),
    (r"produced no files", "studiomdl : aucun fichier produit"),
    (r"Aborted Processing|vertex sort", "studiomdl : maillage refusé"),
    (r"PermissionError|WinError 32|Access is denied|Permission denied", "Fichier verrouillé (GMod ouvert ?)"),
    (r"FileNotFoundError|No such file", "Fichier introuvable"),
    (r"no geometry", "Aucune géométrie"),
    (r"process crashed", "Plantage du processus de conversion"),
    (r"only decal", "Calques décalcomanie seulement"),
    (r"NativeError|panicked", "Erreur du cœur Rust (fichier corrompu ?)"),
    (r"MemoryError", "Mémoire insuffisante"),
]


def cause_of(result: dict) -> str:
    """Short, stable name of why a result failed (the first error, with ids and numbers removed)."""
    text = (result.get("errors") or [""])[0] if isinstance(result.get("errors"), list) else str(result.get("errors") or "")
    for pattern, cause in _CAUSES:
        if re.search(pattern, text):
            return cause
    text = re.sub(r"0x[0-9a-fA-F]+|[0-9A-F]{8,}|\d+", "#", text)
    return text[:90].strip() or "Cause inconnue"


class Jobs:
    def __init__(self, db_path: Path | None = None):
        self.items: dict[str, dict] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.version = 0
        self.starters: dict = {}                       # op -> start(sid, body) used by ``retry``
        self.on_finish: list = []                      # callables(job) run when a job reaches a final phase
        self._fns: dict[str, object] = {}
        self._queue: deque[str] = deque()
        self._pending: dict[str, list] = {}            # results not written to the database yet
        self._dirty: set[str] = set()
        self._db = sqlite3.connect(str(db_path) if db_path else ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self.lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._load()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True, name="jobs-flush")
        self._flusher.start()

    # ------------------------------------------------------------------------------------------ persistence
    def _load(self) -> None:
        """History from the last session; what was queued or running when omni stopped is *interrupted*."""
        rows = self._db.execute("SELECT * FROM jobs ORDER BY started DESC LIMIT ?", (KEEP,)).fetchall()
        for r in rows:
            job = self._row_to_job(r)
            if job["phase"] in ("queued", "running"):
                job.update(phase="interrupted", ended=time.time(), cancellable=False,
                           error="omni a été fermé pendant ce travail : « Reprendre » relance ce qui reste")
                self._dirty.add(job["id"])
            self.items[job["id"]] = job
            self.cancels[job["id"]] = threading.Event()
        stale = [r["id"] for r in self._db.execute("SELECT id FROM jobs ORDER BY started DESC LIMIT -1 OFFSET ?", (KEEP,))]
        for jid in stale:
            self._db.execute("DELETE FROM jobs WHERE id=?", (jid,))
            self._db.execute("DELETE FROM results WHERE job=?", (jid,))
        self._db.commit()
        self._save_dirty()

    @staticmethod
    def _row_to_job(r: sqlite3.Row) -> dict:
        def j(v, default):
            try:
                return json.loads(v) if v else default
            except ValueError:
                return default
        return {"id": r["id"], "kind": r["kind"], "label": r["label"], "source": r["source"], "phase": r["phase"],
                "done": r["done"], "total": r["total"], "error": r["error"] or "", "summary": j(r["summary"], None),
                "started": r["started"], "ended": r["ended"], "cancellable": bool(r["cancellable"]),
                "request": j(r["request"], None), "counts": j(r["counts"], {}), "log": j(r["log"], []),
                "models": r["models"] or 0, "model": r["last_model"] or "", "heavy": bool(r["heavy"]),
                "cancelling": False, "results": []}

    def _save_dirty(self) -> None:
        with self.lock:
            ids, self._dirty = list(self._dirty), set()
            pending, self._pending = self._pending, {}
            for jid, rows in pending.items():
                self._db.executemany("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?)", rows)
            for jid in ids:
                job = self.items.get(jid)
                if job is None:
                    continue
                self._db.execute(
                    "INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (jid, job["kind"], job["label"], job["source"], job["phase"], job["done"], job["total"], job["error"],
                     json.dumps(job["summary"], default=str), job["started"], job["ended"], int(job["cancellable"]),
                     json.dumps(job.get("request")), json.dumps(job["counts"]), json.dumps(job["log"][-LOG_LINES:]),
                     job["models"], job["model"], int(job["heavy"])))
            self._db.commit()

    def _flush_loop(self) -> None:
        while True:
            time.sleep(0.7)
            try:
                if self._dirty or self._pending:
                    self._save_dirty()
            except sqlite3.Error:
                log.exception("jobs database write failed")

    def _touch(self, job: dict | None = None) -> None:
        with self.cond:
            if job is not None:
                self._dirty.add(job["id"])
            self.version += 1
            self.cond.notify_all()

    def wait(self, since: int, timeout: float = 15.0) -> int:
        """Block until ``version`` moves past ``since`` (or the timeout): the SSE endpoint's heartbeat."""
        with self.cond:
            if self.version == since:
                self.cond.wait(timeout)
            return self.version

    # ------------------------------------------------------------------------------------------ lifecycle
    def create(self, kind: str, label: str, source: str, total: int = 0, *, heavy: bool | None = None,
               cancellable: bool = True, request: dict | None = None, dedupe: str | None = None) -> dict:
        heavy = (kind in HEAVY_KINDS or (kind == "character" and total > 1)) if heavy is None else heavy
        if dedupe is None and request is not None:
            dedupe = hashlib.sha1(json.dumps([kind, source, request], sort_keys=True, default=str).encode()).hexdigest()
        elif dedupe is None:
            dedupe = f"{kind}:{source}:{label}"
        job = {"id": uuid.uuid4().hex[:8], "kind": kind, "label": label, "source": source,
               "phase": "queued" if heavy else "running", "done": 0, "total": total, "results": [], "log": [],
               "error": "", "summary": None, "started": time.time(), "ended": None, "cancellable": cancellable,
               "cancelling": False, "heavy": heavy, "request": request, "counts": {}, "models": 0, "model": "",
               "_dedupe": dedupe}
        with self.lock:
            if heavy:
                twin = next((j for j in self.items.values() if j["heavy"] and j["phase"] in ("queued", "running")
                             and j.get("_dedupe") == dedupe), None)
                if twin is not None:
                    raise HTTPException(409, f"Déjà dans la file : {twin['label']}")
            self.items[job["id"]] = job
            self.cancels[job["id"]] = threading.Event()
            self._prune()
        self._touch(job)
        return job

    def _prune(self) -> None:
        final = sorted((j for j in self.items.values() if j["phase"] in FINAL), key=lambda j: j["started"])
        for j in final[:max(0, len(final) - KEEP)]:
            self.items.pop(j["id"], None)
            self.cancels.pop(j["id"], None)
            self._db.execute("DELETE FROM jobs WHERE id=?", (j["id"],))
            self._db.execute("DELETE FROM results WHERE job=?", (j["id"],))

    def cancel_event(self, job: dict) -> threading.Event:
        return self.cancels[job["id"]]

    def log(self, job: dict, message: str) -> None:
        with self.lock:
            job["log"].append(message)
            if len(job["log"]) > LOG_LINES:
                del job["log"][:100]
        self._touch(job)

    def count(self, job: dict, done: int, total: int | None = None) -> None:
        with self.lock:
            job["done"] = done
            if total is not None:
                job["total"] = total
        self._touch(job)

    def result(self, job: dict, r: dict) -> None:
        with self.lock:
            job["done"] += 1
            seq = job.setdefault("_seq", 0)
            job["_seq"] = seq + 1
            status = r.get("status", "")
            job["counts"][status] = job["counts"].get(status, 0) + 1
            if r.get("model") and status not in ("FAILED", "SKIPPED"):
                job["models"] += 1
                job["model"] = r["model"]
            self._pending.setdefault(job["id"], []).append((job["id"], seq, status, str(r.get("key", "")), json.dumps(r, default=str)))
            job["results"].append(r)
            if len(job["results"]) > 300:                  # the database has them all; memory keeps the recent ones
                del job["results"][:100]
        self._touch(job)

    def finish(self, job: dict, error: str = "", summary=None) -> None:
        with self.lock:
            if job["phase"] in FINAL:
                return
            cancelled = self.cancels.get(job["id"], threading.Event()).is_set()
            job.update(phase="error" if error else "cancelled" if cancelled else "done", error=error, summary=summary,
                       ended=time.time(), cancellable=False, cancelling=False)
            self._fns.pop(job["id"], None)
        self._touch(job)
        self._save_dirty()                                 # a finished job is on disk before anyone is told
        for cb in list(self.on_finish):
            try:
                cb(job)
            except Exception:  # noqa: BLE001 - a notification must never break the queue
                log.exception("job finish callback failed")
        self._pump()

    def run(self, job: dict, fn) -> None:
        """Run ``fn(job)`` (now for a light job, when its turn comes for a heavy one); its return value is the
        summary, an exception the error."""
        with self.lock:
            self._fns[job["id"]] = fn
            if job["heavy"]:
                self._queue.append(job["id"])
        if job["heavy"]:
            self._pump()
        else:
            self._start(job)

    def _start(self, job: dict) -> None:
        fn = self._fns.get(job["id"])
        if fn is None:
            return

        def wrap():
            try:
                self.finish(job, summary=fn(job))
            except Exception as e:  # noqa: BLE001
                log.exception("job %s (%s) failed", job["id"], job["label"])
                self.log(job, traceback.format_exc(limit=4))
                self.finish(job, f"{type(e).__name__}: {e}")
        with self.lock:
            job["phase"] = "running"
            job["started_run"] = time.time()
        self._touch(job)
        threading.Thread(target=wrap, daemon=True, name=f"job-{job['id']}").start()

    def _pump(self) -> None:
        """Start the next queued heavy job when none is running."""
        with self.lock:
            if any(j["heavy"] and j["phase"] == "running" for j in self.items.values()):
                return
            while self._queue:
                jid = self._queue.popleft()
                job = self.items.get(jid)
                if job is not None and job["phase"] == "queued":
                    self._start(job)
                    return

    def cancel(self, jid: str) -> dict:
        with self.lock:
            job = self.items.get(jid)
            if job is None:
                raise HTTPException(404, "Travail inconnu")
            if job["phase"] == "queued":                   # never started: leave the queue
                try:
                    self._queue.remove(jid)
                except ValueError:
                    pass
                self.cancels[jid].set()
                job["phase"] = "queued"
        if job["phase"] == "queued":
            self.finish(job)
            return {"ok": True}
        with self.lock:
            if job["phase"] == "running" and job["cancellable"]:
                self.cancels[jid].set()
                job["cancelling"] = True
        self._touch(job)
        return {"ok": True}

    def move_to_front(self, jid: str) -> dict:
        with self.lock:
            if jid not in self._queue:
                raise HTTPException(409, "Ce travail n'est plus en attente")
            self._queue.remove(jid)
            self._queue.appendleft(jid)
        self._touch()
        return {"ok": True}

    def remove(self, jid: str) -> dict:
        with self.lock:
            job = self.items.get(jid)
            if job is None:
                raise HTTPException(404, "Travail inconnu")
            if job["phase"] not in FINAL:
                raise HTTPException(409, "Ce travail n'est pas terminé")
            self.items.pop(jid)
            self.cancels.pop(jid, None)
            self._dirty.discard(jid)
            self._pending.pop(jid, None)
            self._db.execute("DELETE FROM jobs WHERE id=?", (jid,))
            self._db.execute("DELETE FROM results WHERE job=?", (jid,))
            self._db.commit()
        self._touch()
        return {"ok": True}

    def clear_finished(self) -> dict:
        with self.lock:
            ids = [j["id"] for j in self.items.values() if j["phase"] in FINAL]
        for jid in ids:
            self.remove(jid)
        return {"removed": len(ids)}

    def shutdown(self) -> None:
        """Flush, and mark what is still running as interrupted (called when the process is about to exit)."""
        with self.lock:
            for job in self.items.values():
                if job["phase"] in ("queued", "running"):
                    job.update(phase="interrupted", ended=time.time(), cancellable=False,
                               error="omni a été fermé pendant ce travail : « Reprendre » relance ce qui reste")
                    self._dirty.add(job["id"])
        self._save_dirty()

    # ------------------------------------------------------------------------------------------ reading
    def _view(self, job: dict) -> dict:
        v = {k: val for k, val in job.items() if k not in ("results", "log", "_seq", "_dedupe", "request", "started_run")}
        v["last"] = job["log"][-1] if job["log"] else ""
        v["failed"] = job["counts"].get("FAILED", 0)
        v["has_request"] = bool(job.get("request"))
        if job["phase"] == "queued":
            order = [j for j in self._queue if j in self.items]
            v["position"] = order.index(job["id"]) + 1 if job["id"] in order else 0
        return v

    def get(self, jid: str, tail: int = 0, log_lines: int = 60) -> dict:
        with self.lock:
            job = self.items.get(jid)
            if job is None:
                raise HTTPException(404, "Travail inconnu")
            v = self._view(job)
            v["log"] = list(job["log"][-log_lines:])
            v["results_total"] = sum(job["counts"].values())
            v["results"] = list(job["results"][-tail:]) if tail else []
            return json.loads(json.dumps(v, default=str))

    def results(self, jid: str, offset: int = 0, limit: int = 100, status: str = "", cause: str = "") -> dict:
        """One page of a job's results, from the database (flushed first so a running job's are included)."""
        with self.lock:
            if jid not in self.items:
                raise HTTPException(404, "Travail inconnu")
            self._save_dirty()
            args: list = [jid]
            sql = "FROM results WHERE job=?"
            if status:
                sql += " AND status=?"
                args.append(status)
            if cause:                                          # the cause is derived: filter after reading
                rows = [json.loads(r[0]) for r in self._db.execute(f"SELECT data {sql} ORDER BY seq", args)]
                rows = [r for r in rows if cause_of(r) == cause]
                return {"total": len(rows), "items": rows[offset:offset + limit]}
            total = self._db.execute(f"SELECT COUNT(*) {sql}", args).fetchone()[0]
            rows = self._db.execute(f"SELECT data {sql} ORDER BY seq LIMIT ? OFFSET ?", [*args, limit, offset]).fetchall()
            return {"total": total, "items": [json.loads(r[0]) for r in rows]}

    def report(self, jid: str, status: str = "FAILED") -> list[dict]:
        """The results of ``status`` grouped by cause: ``[{cause, count, examples: [key...]}]``, biggest first."""
        with self.lock:
            if jid not in self.items:
                raise HTTPException(404, "Travail inconnu")
            self._save_dirty()
            rows = self._db.execute("SELECT data FROM results WHERE job=? AND status=? ORDER BY seq", (jid, status)).fetchall()
        groups: dict[str, dict] = {}
        for (data,) in rows:
            r = json.loads(data)
            g = groups.setdefault(cause_of(r), {"cause": cause_of(r), "count": 0, "examples": [], "sample": ""})
            g["count"] += 1
            if len(g["examples"]) < 5:
                g["examples"].append(r.get("key", ""))
            if not g["sample"]:
                g["sample"] = ((r.get("errors") or [""])[0] if isinstance(r.get("errors"), list) else "")[:300]
        return sorted(groups.values(), key=lambda g: -g["count"])

    def keys_of(self, jid: str, status: str = "", cause: str = "") -> list[str]:
        out, offset = [], 0
        while True:
            page = self.results(jid, offset, 5000, status, cause)
            out += [r.get("key", "") for r in page["items"]]
            offset += 5000
            if offset >= page["total"]:
                return out

    def running(self, kind: str = "", source: str = "") -> list[dict]:
        with self.lock:
            return [j for j in self.items.values() if j["phase"] in ("running", "queued")
                    and (not kind or j["kind"] == kind) and (not source or j["source"] == source)]

    def busy(self) -> dict | None:
        """The heavy job in progress, if any (new heavy jobs queue behind it)."""
        with self.lock:
            return next((j for j in self.items.values() if j["heavy"] and j["phase"] == "running"), None)

    def recent(self, limit: int = 40, offset: int = 0) -> list[dict]:
        with self.lock:
            rows = sorted(self.items.values(), key=lambda j: -j["started"])[offset:offset + limit]
            return [self._view(j) for j in rows]

    # ------------------------------------------------------------------------------------------ retry
    def retry(self, jid: str, scope: str = "failed", cause: str = "") -> dict:
        """Start a new job from an earlier one: its failed items (``failed``, optionally one ``cause``), the items it
        never reached (``remaining``), or the same request again (``all``)."""
        with self.lock:
            job = self.items.get(jid)
            if job is None:
                raise HTTPException(404, "Travail inconnu")
            req = job.get("request")
        if not req or req.get("op") not in self.starters:
            raise HTTPException(400, "Ce travail ne peut pas être relancé")
        body = dict(req["body"])
        field = req.get("field")                          # the list of items inside the body (keys, ids)
        if field and scope != "all":
            original = list(body.get(field) or [])
            seen = set(self.keys_of(jid))
            if scope == "failed":
                bad = set(self.keys_of(jid, "FAILED", cause))
                body[field] = [k for k in original if k in bad]
            else:
                body[field] = [k for k in original if k not in seen]
            if not body[field]:
                raise HTTPException(409, "Rien à relancer")
        return self.starters[req["op"]](req["sid"], body)
