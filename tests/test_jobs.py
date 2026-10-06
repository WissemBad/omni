"""The job engine: a queue with one heavy job at a time, no duplicates, a history that survives a restart, results
paged from the database, failures grouped by cause, retry."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi import HTTPException

from omni.ui.jobs import Jobs, cause_of


def until(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def jobs(tmp_path):
    j = Jobs(tmp_path / "jobs.sqlite")
    yield j
    j.shutdown()


def test_heavy_jobs_run_one_at_a_time_in_order(jobs):
    gate, order = threading.Event(), []

    def slow(job):
        order.append("first")
        gate.wait(5)
        return {"ok": 1}

    def quick(job):
        order.append("second")
    a = jobs.create("props", "A", "s", request={"op": "x", "n": 1})
    b = jobs.create("props", "B", "s", request={"op": "x", "n": 2})
    jobs.run(a, slow)
    jobs.run(b, quick)
    assert until(lambda: a["phase"] == "running")
    assert b["phase"] == "queued" and jobs.get(b["id"])["position"] == 1
    assert order == ["first"]
    gate.set()
    assert until(lambda: b["phase"] == "done")
    assert order == ["first", "second"] and a["phase"] == "done"


def test_light_jobs_do_not_wait_for_the_queue(jobs):
    gate = threading.Event()
    heavy = jobs.create("props", "heavy", "s", request={"n": 1})
    jobs.run(heavy, lambda job: gate.wait(5))
    assert until(lambda: heavy["phase"] == "running")
    light = jobs.create("character", "one playermodel", "s", 1)
    jobs.run(light, lambda job: "built")
    assert until(lambda: light["phase"] == "done")
    gate.set()


def test_identical_job_is_refused_while_queued_or_running(jobs):
    gate = threading.Event()
    a = jobs.create("props", "100 props", "s", request={"keys": ["A"]})
    jobs.run(a, lambda job: gate.wait(5))
    with pytest.raises(HTTPException) as e:
        jobs.create("props", "100 props", "s", request={"keys": ["A"]})
    assert e.value.status_code == 409
    jobs.create("props", "other", "s", request={"keys": ["B"]})          # a different request is queued
    gate.set()


def test_cancel_a_queued_job_never_runs_it(jobs):
    gate, ran = threading.Event(), []
    a = jobs.create("sounds", "A", "s", request={"n": 1})
    b = jobs.create("sounds", "B", "s", request={"n": 2})
    jobs.run(a, lambda job: gate.wait(5))
    jobs.run(b, lambda job: ran.append(1))
    assert until(lambda: a["phase"] == "running")
    jobs.cancel(b["id"])
    assert b["phase"] == "cancelled"
    gate.set()
    assert until(lambda: a["phase"] == "done")
    time.sleep(0.1)
    assert ran == []


def test_cancel_running_sets_the_event_and_marks_cancelling(jobs):
    seen = threading.Event()

    def work(job):
        ev = jobs.cancel_event(job)
        seen.set()
        ev.wait(5)
        return {}
    a = jobs.create("props", "A", "s", request={"n": 1})
    jobs.run(a, work)
    seen.wait(5)
    jobs.cancel(a["id"])
    assert jobs.get(a["id"])["cancelling"] is True
    assert until(lambda: a["phase"] == "cancelled")


def test_results_are_paged_counted_and_survive_a_restart(tmp_path):
    db = tmp_path / "jobs.sqlite"
    first = Jobs(db)
    job = first.create("props", "3 props", "s", 3, request={"op": "p", "sid": "s", "body": {"keys": ["A", "B", "C"]}, "field": "keys"})
    first.run(job, lambda j: [first.result(j, {"key": k, "status": st, "model": "m/" + k if st == "OK" else "", "errors": ["boom"] if st == "FAILED" else []})
                              for k, st in (("A", "OK"), ("B", "FAILED"), ("C", "OK"))] and {"n": 3})
    assert until(lambda: job["phase"] == "done")
    page = first.results(job["id"], 1, 1)
    assert page["total"] == 3 and page["items"][0]["key"] == "B"
    assert first.results(job["id"], status="FAILED")["total"] == 1
    assert first.get(job["id"])["counts"] == {"OK": 2, "FAILED": 1} and first.get(job["id"])["models"] == 2
    first.shutdown()

    second = Jobs(db)                                   # a new process, same file
    got = second.get(job["id"])
    assert got["phase"] == "done" and got["summary"] == {"n": 3}
    assert second.results(job["id"])["total"] == 3
    second.shutdown()


def test_a_job_cut_short_is_interrupted_after_restart(tmp_path):
    db = tmp_path / "jobs.sqlite"
    first = Jobs(db)
    gate = threading.Event()
    job = first.create("props", "long", "s", request={"n": 1})
    first.run(job, lambda j: gate.wait(5))
    assert until(lambda: job["phase"] == "running")
    first._save_dirty()                                 # what the flusher does within a second
    gate.set()
    second = Jobs(db)                                   # the process died before the job finished
    row = next(j for j in second.recent() if j["id"] == job["id"])
    assert row["phase"] in ("interrupted", "done")
    second.shutdown()
    first.shutdown()


def test_failures_are_grouped_by_cause_and_retried(jobs):
    started = []
    jobs.starters["props"] = lambda sid, body: started.append((sid, body)) or {"job": "new"}
    job = jobs.create("props", "5 props", "s", 5, request={"op": "props", "sid": "s", "field": "keys",
                                                           "body": {"keys": list("ABCDE"), "physics": True}})
    errors = {"B": "studiomdl timed out after 60s", "C": "studiomdl timed out after 130s", "D": "ERROR: EXCEPTION_ACCESS_VIOLATION"}

    def run(j):
        for k in "ABCDE":
            if k in errors:
                jobs.result(j, {"key": k, "status": "FAILED", "errors": [errors[k]]})
            elif k == "E":
                break
            else:
                jobs.result(j, {"key": k, "status": "OK"})
    jobs.run(job, run)
    assert until(lambda: job["phase"] == "done")
    report = jobs.report(job["id"])
    assert [(g["cause"].split(" :")[0], g["count"]) for g in report] == [("studiomdl", 2), ("studiomdl", 1)]
    assert report[0]["count"] == 2 and set(report[0]["examples"]) == {"B", "C"}

    assert jobs.retry(job["id"], "failed") == {"job": "new"}
    assert started[-1][1]["keys"] == ["B", "C", "D"] and started[-1][1]["physics"] is True
    jobs.retry(job["id"], "failed", report[1]["cause"])
    assert started[-1][1]["keys"] == ["D"]
    jobs.retry(job["id"], "remaining")
    assert started[-1][1]["keys"] == ["E"]                # never reached
    with pytest.raises(HTTPException):
        jobs.retry(job["id"], "failed", "no such cause")


def test_version_moves_and_wait_returns(jobs):
    v = jobs.version
    t = threading.Timer(0.1, lambda: jobs.create("props", "x", "s", request={"n": 9}))
    t.start()
    assert jobs.wait(v, 3.0) > v


def test_remove_only_finished_jobs(jobs):
    gate = threading.Event()
    a = jobs.create("props", "A", "s", request={"n": 1})
    jobs.run(a, lambda j: gate.wait(5))
    assert until(lambda: a["phase"] == "running")
    with pytest.raises(HTTPException):
        jobs.remove(a["id"])
    gate.set()
    assert until(lambda: a["phase"] == "done")
    jobs.remove(a["id"])
    with pytest.raises(HTTPException):
        jobs.get(a["id"])


@pytest.mark.parametrize("error,cause", [
    ("studiomdl timed out after 77s", "délai dépassé"),
    ("PermissionError: [WinError 32] used by another process 'C:\\x'", "verrouillé"),
    ("something odd 12345 happened at 0xDEADBEEF", "something odd # happened at #"),
])
def test_cause_names(error, cause):
    assert cause in cause_of({"errors": [error]})


def test_view_carries_the_stage_and_a_remaining_time(jobs):
    job = jobs.create("maintenance", "x", "s", 10, heavy=False)
    st = jobs.stager(job, 2)
    st("Premier")
    jobs.plan(job, {str(i): 100.0 - i for i in range(10)}, 2)
    for i in range(3):
        jobs.result(job, {"key": str(i), "status": "OK", "seconds": 1.0})
        time.sleep(0.05)
    v = jobs.get(job["id"])
    assert v["stage"] == {"label": "Premier", "index": 1, "of": 2, "done": 0, "total": 0}
    assert "_eta" not in v and "_rate" not in v and v["eta_at"] > 0
    count = st("Second", 50)
    count(5, 50)
    time.sleep(0.25)
    count(10, 50)
    v = jobs.get(job["id"])
    assert v["stage"]["label"] == "Second" and v["stage"]["done"] == 10 and v["stage"]["total"] == 50
    assert v["eta"] is not None and v["eta"] >= 0                   # the step's own speed, not the job's
    jobs.finish(job)
    assert "eta" not in jobs.get(job["id"])                          # nothing left to estimate once it is over


def test_results_without_a_plan_still_estimate(jobs):
    job = jobs.create("maintenance", "y", "s", 4, heavy=False)
    for i in range(2):
        jobs.result(job, {"key": str(i), "status": "OK"})
        time.sleep(0.05)
    assert jobs.get(job["id"])["eta"] is not None
