"""Long operations started from the interface (conversions, playermodels, sound export, indexing...): one table,
progress, logs, results, cancellation. Everything runs in background threads of the API process."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid

from fastapi import HTTPException


class Jobs:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()

    def create(self, kind: str, label: str, source: str, total: int = 0) -> dict:
        job = {"id": uuid.uuid4().hex[:8], "kind": kind, "label": label, "source": source, "phase": "running",
               "done": 0, "total": total, "results": [], "log": [], "error": "", "summary": None,
               "started": time.time(), "ended": None, "cancellable": True}
        with self.lock:
            self.items[job["id"]] = job
            self.cancels[job["id"]] = threading.Event()
        return job

    def cancel_event(self, job: dict) -> threading.Event:
        return self.cancels[job["id"]]

    def log(self, job: dict, message: str) -> None:
        with self.lock:
            job["log"].append(message)
            if len(job["log"]) > 400:
                del job["log"][:100]

    def count(self, job: dict, done: int, total: int | None = None) -> None:
        with self.lock:
            job["done"] = done
            if total is not None:
                job["total"] = total

    def result(self, job: dict, r: dict) -> None:
        with self.lock:
            job["done"] += 1
            job["results"].append(r)

    def finish(self, job: dict, error: str = "", summary=None) -> None:
        with self.lock:
            cancelled = self.cancels.get(job["id"], threading.Event()).is_set()
            job.update(phase="error" if error else "cancelled" if cancelled else "done", error=error, summary=summary,
                       ended=time.time(), cancellable=False)

    def run(self, job: dict, fn) -> None:
        """Run ``fn(job)`` in a thread; its return value is the summary, an exception the error."""
        def wrap():
            try:
                self.finish(job, summary=fn(job))
            except Exception as e:  # noqa: BLE001
                self.log(job, traceback.format_exc(limit=4))
                self.finish(job, f"{type(e).__name__}: {e}")
        threading.Thread(target=wrap, daemon=True).start()

    def cancel(self, jid: str) -> dict:
        with self.lock:
            if jid not in self.items:
                raise HTTPException(404, "unknown job")
            self.cancels[jid].set()
            return {"ok": True}

    def get(self, jid: str) -> dict:
        with self.lock:
            if jid not in self.items:
                raise HTTPException(404, "unknown job")
            return json.loads(json.dumps(self.items[jid], default=str))

    def running(self, kind: str = "", source: str = "") -> list[dict]:
        with self.lock:
            return [j for j in self.items.values() if j["phase"] == "running"
                    and (not kind or j["kind"] == kind) and (not source or j["source"] == source)]

    def recent(self, limit: int = 40) -> list[dict]:
        with self.lock:
            rows = sorted(self.items.values(), key=lambda j: -j["started"])[:limit]

            def outputs(j):
                return [r["model"] for r in j["results"] if r.get("model") and r.get("status") != "FAILED"]
            return [{k: (v if k not in ("results", "log") else None) for k, v in j.items()}
                    | {"last": j["log"][-1] if j["log"] else "", "failed": sum(r.get("status") == "FAILED" for r in j["results"]),
                       "models": len(outputs(j)), "model": (outputs(j) or [""])[-1]}
                    for j in rows]
