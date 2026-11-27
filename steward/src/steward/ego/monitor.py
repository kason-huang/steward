"""Robot-side observation of active jobs, independent of the model and perception worker."""

from __future__ import annotations

import collections
import itertools
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from steward.net import HttpError

if TYPE_CHECKING:
    from steward.ego.runtime import EgoRuntime
    from steward.ego.skills import Job


@dataclass(frozen=True)
class MonitorConfig:
    poll_s: float = 0.1
    feedback_timeout_s: float = 5.0
    job_timeout_s: float | None = None  # optional wall-clock budget for an outer skill
    watch_targets: bool = True  # ablation for the simulation benchmark

    def __post_init__(self) -> None:
        if self.poll_s <= 0 or self.feedback_timeout_s <= 0 or (
                self.job_timeout_s is not None and self.job_timeout_s <= 0):
            raise ValueError("monitor periods and deadlines must be positive")


class MotionMonitor:
    def __init__(self, runtime: EgoRuntime, config: MonitorConfig):
        self.runtime = runtime
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._events: collections.deque[dict] = collections.deque(maxlen=200)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ego-motion-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def events(self, since: int) -> dict:
        with self._lock:
            return {"boot": self.runtime.boot, "last": self._events[-1]["id"] if self._events else 0,
                    "events": [event for event in self._events if event["id"] > since]}

    def _record(self, job: Job, kind: str, stamp: float | None, detail: dict) -> None:
        with self._lock:
            self._events.append({"id": next(self._ids), "kind": kind, "job": job.id,
                                 "skill": job.skill, "stamp": stamp, "wallTime": time.time(),
                                 "taskId": job.task_id, "intentRevision": job.intent_revision, **detail})

    def finished(self, job: Job) -> None:
        self._record(job, "skill-finished", job.finished,
                     {"state": job.state, "notifyCompletion": job.notify_completion})

    def _run(self) -> None:
        active = None
        handled = None
        started = last_feedback = 0.0
        last_stamp = None
        carrying: set[str] = set()
        missing: set[str] = set()
        expected_grip_missing = False
        previous_change = None
        while not self._stop.wait(self.config.poll_s):
            job = self.runtime.executor.current
            if job is None or job.state != "running" or job is handled:
                continue
            now = time.monotonic()
            if job is not active:
                active, started, last_feedback = job, now, now
                last_stamp = None
                carrying.clear()
                missing.clear()
                expected_grip_missing = False
                previous_change = None
            lost_load = False
            try:
                frame = self.runtime.link.proprioception(timeout=min(1.0, self.config.feedback_timeout_s))
                stamp = frame["stamp"]
                if stamp != last_stamp:
                    if job.expects_grip:
                        absent = not frame["gripper"].get("holding")
                        lost_load = absent and expected_grip_missing
                        expected_grip_missing = absent
                    elif job.skill == "navigate":
                        current = {name for name, state in frame["grippers"].items() if state.get("holding")}
                        lost = carrying - current
                        lost_load = bool(missing & lost)
                        carrying.update(current)
                        missing = lost
                    else:
                        expected_grip_missing = False
                last_stamp = stamp
                last_feedback = time.monotonic()
            except (HttpError, OSError, KeyError, TypeError):
                pass
            now = time.monotonic()
            watched = job.watched_target if self.config.watch_targets else None
            change = self.runtime.target_change(job, watched, last_stamp) if watched is not None else None
            moved = (change is not None and previous_change is not None
                     and change["observedEntity"] == previous_change["observedEntity"]
                     and change["observationStamp"] > previous_change["observationStamp"])
            previous_change = change
            if now - last_feedback >= self.config.feedback_timeout_s:
                kind = "body-feedback-lost"
            elif lost_load:
                kind = "carried-object-lost"
            elif self.config.job_timeout_s is not None and now - started >= self.config.job_timeout_s:
                kind = "skill-deadline"
            elif moved:
                kind = "target-position-changed"
            else:
                continue
            target_watch = watched if kind == "target-position-changed" else None
            detail = change if target_watch is not None else {}
            try:
                hold = self.runtime.protect_job(job, target_watch, grip_loss=kind == "carried-object-lost")
            except (HttpError, OSError):
                # The S1 cancel flag is still set. Report that S0 protection could not be
                # confirmed. A review must keep motion disabled until reconciling the hold.
                self._record(job, kind, last_stamp, {"holdAccepted": False, **detail})
                handled = job
                continue
            if hold is not None:
                handled = job
                self._record(job, kind, last_stamp, {"holdAccepted": True, "motion": hold, **detail})
