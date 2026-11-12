"""Episode-bound Panda task receipts, a single policy Job and confirmed stopping."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
import json
from pathlib import Path
import threading
import time
import uuid

import numpy as np

from steward.ego.panda_policy import SKILL, PolicyFailure, infer, write_json
from steward.ego.panda_pick import PICK, pick_prompt, pickable
from steward.ego.panda_prediction import PandaPrediction
from steward.ego.skills import Job
from steward.glrender import encode_image
from steward.net import HttpError, call


def credentials(status):
    return {key: status[key] for key in ("episodeId", "controlGeneration", "leaseId") if key in status}


@dataclass
class PandaJob(Job):
    episodeId: int = 0
    phase: str = "starting"
    goalStatus: str = "unconfirmed"
    stopStatus: str = "unknown"
    reasonCode: str = ""
    evidence: dict = field(default_factory=dict)
    actionsApplied: int = 0

    def to_dict(self):
        return super().to_dict() | {key: copy.deepcopy(getattr(self, key)) for key in (
            "episodeId", "phase", "goalStatus", "stopStatus", "reasonCode", "evidence", "actionsApplied")}


class PandaJobs:
    """Own all policy control, receipts and cleanup for one robot-local runtime."""

    def __init__(self, runtime, config, out):
        self.runtime = runtime
        self.link = runtime.link
        self.config = config
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.receipts = {}
        self.deadlines = {}
        self.jobs = {}
        self.current = None
        self.owner = None
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.finished.set()
        self.thread = None
        self.lease = None
        self.revoked = False
        self.blocked = False
        self.hold = None
        self.policy_fault = None

    def control_call(self, operation, body=None):
        return call("GET" if operation == "status" else "POST", self.link.url + "/control/" + operation,
                    body, timeout=3)

    def status(self):
        return self.control_call("status")

    def control(self, action=None, owner=None):
        if action == "release":
            with self.lock:
                if owner != self.owner:
                    raise PermissionError("Panda control cannot be released by this request")
                running = self.current is not None and self.current.state == "running"
            if running:
                stopped = self.stop()
                if stopped["stopStatus"] != "confirmed":
                    return self.control() | {"stopStatus": "unknown"}
            with self.lock:
                if owner == self.owner:
                    self.owner = None
                return {"owner": self.owner, "blocked": self.blocked}
        with self.lock:
            if action == "acquire":
                if not isinstance(owner, str) or not owner:
                    raise ValueError("control requires an owner")
                if self.owner not in (None, owner) or self.current and self.current.state == "running":
                    raise PermissionError("Panda control is busy")
                self.owner = owner
            elif action is not None:
                raise ValueError("action must be acquire or release")
            return {"owner": self.owner, "blocked": self.blocked}

    def predict(self, skill, params):
        observation, judgment = self.runtime.policy_observation()
        state = self.runtime.summary()
        objects = state['objects']
        by_id = {obj["id"]: obj for obj in objects}
        issues = []
        target, basket = by_id.get(params.get("target")), by_id.get(params.get("on"))
        picking = skill == PICK
        if picking:
            if set(params) != {'target'}:
                issues.append('pick requires only the selected Ego-State target')
            if target is None or not pickable(target):
                issues.append('Pick needs a fresh graspable Ego-State object reference')
        else:
            if skill != SKILL or set(params) != {"target", "on"}:
                issues.append("Supported Panda skills are pick(target) and libero_pick_place(target, on)")
            if target is None or target["category"] != "alphabet_soup":
                issues.append("The soup can needs a current unambiguous Ego-State reference")
            if basket is None or basket["category"] != "basket":
                issues.append("The basket needs a current unambiguous Ego-State reference")
        previous_pick = self.runtime.pick_evidence
        width = abs(observation['encoders']['gripper0_finger_joint1']['position']
                    - observation['encoders']['gripper0_finger_joint2']['position'])
        if previous_pick is not None and previous_pick.ever_confirmed and width < .075:
            issues.append('A previously grasped object must be released or the episode reset before another task')
        if state['self']['holding'] is not None and (picking or state['self']['holding'] != params.get('target')):
            issues.append('The gripper already holds an object; release it or reset before a new pick')
        status = self.status()
        if self.policy_fault is not None and self.policy_fault['episodeId'] == observation['episodeId']:
            issues.append('A pick motion was rejected; reset the episode before further policy motion: '
                          + self.policy_fault['job'])
        if status["leased"] or self.blocked or self.current and self.current.state == "running":
            issues.append("Panda control is busy or stopping is unconfirmed")
        if status["reason"] != "reset" and status["stopState"] == "unknown":
            issues.append("The controller has not confirmed a stationary state")
        coverage = None
        if not issues:
            try:
                coverage = PandaPrediction(observation, target["position"],
                    None if picking else basket["position"],
                    target_size=target['sizeM'] if picking else None).check(
                    [[0, 0, 0, 0, 0, 0, -1]])
            except PolicyFailure as exc:
                issues.append(str(exc))
        risk = 1.0 if issues else min(.35, .02 / max(coverage["minimumObservedClearanceM"], .06))
        identifier = "panda-receipt-" + uuid.uuid4().hex
        receipt = {
            "id": identifier, "skill": skill, "params": copy.deepcopy(params), "episodeId": observation["episodeId"],
            "controlGeneration": status["controlGeneration"], "modelId": self.config.model_id,
            "calibrationId": observation["calibrationId"], "sourceStep": observation["step"],
            "expiresAt": observation["stamp"] + self.config.receipt_wall_s,
            "risk": risk, "maxRisk": .35, "feasible": not issues, "withinRiskLimit": not issues,
            "issues": issues, "used": False, "plan": {"target": params.get("target"), "support": params.get("on"),
                "coverage": coverage, "prefixSize": self.config.prefix_size, "maxActions": self.config.max_actions,
                "maxWallS": self.config.max_wall_s},
        }
        if picking and target is not None:
            receipt['plan']['object'] = copy.deepcopy(target)
            receipt['plan']['prompt'] = pick_prompt(target)
        with self.lock:
            self.receipts[identifier] = receipt
            self.deadlines[identifier] = time.monotonic() + self.config.receipt_wall_s
        return copy.deepcopy(receipt)

    def receipt(self, identifier):
        with self.lock:
            receipt = copy.deepcopy(self.receipts[identifier])
            status = self.status()
            receipt["now"] = status["step"] * .002
            receipt["expired"] = (time.monotonic() >= self.deadlines[identifier]
                                  or receipt["episodeId"] != status["episodeId"]
                                  or not receipt["used"] and receipt["controlGeneration"] != status["controlGeneration"])
            return receipt

    def run(self, identifier, owner=None):
        observation, _ = self.runtime.policy_observation()
        with self.lock:
            receipt = self.receipt(identifier)
            if self.policy_fault is not None and self.policy_fault['episodeId'] == observation['episodeId']:
                raise PermissionError('A pick motion was rejected; reset the episode before further policy motion')
            if self.owner is not None and owner != self.owner:
                raise PermissionError("Panda is controlled by another owner")
            if self.blocked or self.current and self.current.state == "running":
                raise PermissionError("Panda control is busy or stopping is unconfirmed")
            if receipt["used"] or receipt["expired"] or not receipt["withinRiskLimit"]:
                raise PermissionError("Receipt is infeasible, used, expired or outside its risk limit")
            if (receipt["calibrationId"] != observation["calibrationId"]
                    or receipt["sourceStep"] != observation["step"]):
                raise PermissionError("Receipt observation changed, predict again")
            job = PandaJob(id="panda-job-" + uuid.uuid4().hex, skill=receipt['skill'], receipt=identifier,
                           started=observation["stamp"], episodeId=observation["episodeId"])
            folder = self.out / job.id
            folder.mkdir()
            for name in ("external", "wrist"):
                (folder / name).mkdir()
            self.lease = credentials(self.control_call("acquire", credentials(self.status())
                                                      | {"ttlS": 30, "policy": "libero-task"}))
            self.current = job
            self.jobs[job.id] = job
            self.receipts[identifier].update(used=True, job=job.id)
            self.cancelled = threading.Event()
            self.finished = threading.Event()
            self.revoked = False
            self.hold = "policy/" + job.id
            try:
                if job.skill == PICK:
                    self.runtime.bind_pick(receipt['plan']['object'])
                else:
                    self.runtime.bind_legacy_task()
                self.link.arm(self.hold, observation["stamp"])
                self.thread = threading.Thread(target=self._execute, args=(job,), name=job.id, daemon=True)
                self.thread.start()
            except Exception as exc:
                self.cancelled.set()
                cleanup = {}
                try:
                    self._revoke()
                except Exception as revoke_error:
                    cleanup["stopError"] = str(revoke_error)
                try:
                    self.link.release(self.hold)
                except Exception as release_error:
                    cleanup["clockReleaseError"] = str(release_error)
                job.state = "failed"
                job.phase = "finished"
                job.reasonCode = "start_failed"
                job.message = str(exc)
                job.evidence = cleanup
                job.finished = observation["stamp"]
                self.blocked = True
                self.finished.set()
                write_json(folder / "job.json", job.to_dict())
                raise
            return job.to_dict()

    def job(self, identifier):
        with self.lock:
            return self.jobs[identifier].to_dict()

    def wait(self, identifier, timeout=120):
        if not 0 <= timeout <= 600:
            raise ValueError("wait timeout must be between 0 and 600 seconds")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = self.job(identifier)
            if result["state"] != "running":
                return result
            self.finished.wait(min(.05, max(0, deadline - time.monotonic())))
        return self.job(identifier)

    def _revoke(self):
        with self.lock:
            if self.lease is not None and not self.revoked:
                result = self.control_call("stop", self.lease)
                self.revoked = True
                return result
            return self.status()

    def stop(self):
        with self.lock:
            job = self.current
            if job is None or job.state != "running":
                try:
                    status = self.status()
                    if self.blocked and status["leased"] and self.lease is not None:
                        status = self._revoke()
                    confirmed = not status["leased"] and status["stopState"] == "stopped"
                    if self.blocked and not status["leased"] and not confirmed:
                        hold = "stop-recovery/" + uuid.uuid4().hex
                        self.link.arm(hold, status["step"] * .002)
                        deadline = time.monotonic() + self.config.stop_wall_s
                        try:
                            for _ in range(100):
                                if time.monotonic() >= deadline:
                                    break
                                self._wait_clock(status["step"] + 25, hold, deadline, stopping=True)
                                status = self.status()
                                if not status["leased"] and status["stopState"] == "stopped":
                                    confirmed = True
                                    break
                        finally:
                            self.link.release(hold)
                except Exception as exc:
                    self.blocked = True
                    return {"stopped": None, "stopStatus": "unknown", "message": str(exc)}
                self.blocked = not confirmed
                return {"stopped": None, "stopStatus": "confirmed" if confirmed else "unknown"}
            self.cancelled.set()
            try:
                self._revoke()
            except Exception:
                self.blocked = True
        result = self.wait(job.id, self.config.stop_wall_s)
        if result["state"] == "running":
            with self.lock:
                self.blocked = True
        return {"stopped": job.id, "stopStatus": result["stopStatus"], "job": result}

    def close(self):
        self.stop()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(timeout=self.config.stop_wall_s)

    def _keep_alive(self, job, done):
        while not done.wait(.35):
            try:
                self.link.renew(self.hold)
                with self.lock:
                    if not self.revoked and not self.cancelled.is_set():
                        self.control_call("renew", self.lease | {"ttlS": 30})
            except Exception as exc:
                with self.lock:
                    if not job.reasonCode:
                        job.reasonCode = "lease_lost"
                        job.message = str(exc)
                self.cancelled.set()
                return

    def _wait_clock(self, step, hold, deadline, *, stopping=False):
        """Wait within the caller's wall deadline even when simulation cannot advance."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PolicyFailure("stop_timeout" if stopping else "execution_timeout",
                                    "Panda clock wait exceeded its wall deadline")
            if not stopping and self.cancelled.is_set():
                raise PolicyFailure("cancelled", "Panda clock wait cancelled")
            timeout = min(.25, remaining)
            try:
                call("POST", self.link.url + "/clock/wait", {"hold": hold, "until": step * .002,
                     "timeoutS": timeout, "rank": 0, "pulls": True}, timeout=timeout + .5)
                return
            except HttpError as exc:
                if exc.status != 504:
                    raise

    def _advance(self, step, deadline, *, stopping=False):
        self._wait_clock(step, self.hold, deadline, stopping=stopping)
        status = self.status()
        if status["episodeId"] != self.current.episodeId or status["step"] != step:
            raise PolicyFailure("episode_changed", "Policy clock moved to another episode or step")
        return status

    def _record_observation(self, observation, folder, index):
        for role in ("external", "wrist"):
            (folder / role / f"{index:04d}.png").write_bytes(
                encode_image(observation["images"][role]))
        metadata = {key: observation[key] for key in (
            "episodeId", "step", "stamp", "encoders", "tool", "hand", "cameras", "calibrationId",
            "toolRig", "selfCollision", "selfVisual")}
        metadata["judgment"] = copy.deepcopy(self.runtime.latest["judgment"])
        sensors = folder / "sensors"
        sensors.mkdir(exist_ok=True)
        np.savez_compressed(sensors / f"{index:04d}.npz", metadata=json.dumps(metadata),
                            external=observation["images"]["external"], wrist=observation["images"]["wrist"],
                            external_depth=observation["depth"]["external"], wrist_depth=observation["depth"]["wrist"])
        with (folder / "observations.jsonl").open("a") as stream:
            stream.write(json.dumps({"frame": index, "step": observation["step"],
                                     "stamp": observation["stamp"], "judgment": metadata["judgment"]}) + "\n")

    def _execute(self, job):
        folder = self.out / job.id
        keeper_done = threading.Event()
        keeper = threading.Thread(target=self._keep_alive, args=(job, keeper_done), name="panda-lease", daemon=True)
        began = time.monotonic()
        reason = "execution_limit"
        try:
            keeper.start()
            write_json(folder / "receipt.json", self.receipts[job.receipt])
            observation, judgment = self.runtime.policy_observation(force=True)
            self._record_observation(observation, folder, 0)
            with (folder / "actions.jsonl").open("w", buffering=1) as log, \
                    (folder / "submissions.jsonl").open("w", buffering=1) as submissions:
                prefix_index = 0
                while job.actionsApplied < self.config.max_actions:
                    if self.cancelled.is_set():
                        raise PolicyFailure(job.reasonCode or "cancelled", job.message or "Operator stopped the task")
                    if time.monotonic() - began >= self.config.max_wall_s:
                        break
                    job.phase = "waiting_inference"
                    task = self.receipts[job.receipt]['plan']
                    options = ({'prompt': task['prompt'], 'target': task['object']} if job.skill == PICK else {})
                    actions, reply = infer(self.config, observation, folder / f"prefix-{prefix_index:03d}", self.cancelled, **options)
                    prefix = np.clip(actions[:min(self.config.prefix_size, self.config.max_actions - job.actionsApplied)], -1, 1)
                    objects = self.runtime.task_positions()
                    job.phase = "checking_actions"
                    size = task['object']['sizeM'] if job.skill == PICK else None
                    prediction = PandaPrediction(observation, *objects, target_size=size).check(prefix)
                    write_json(folder / f"prefix-{prefix_index:03d}" / "prediction.json", prediction)
                    source = observation
                    for raw, action in zip(actions, prefix):
                        if self.cancelled.is_set():
                            raise PolicyFailure(job.reasonCode or "cancelled", "Task cancelled before action")
                        if observation["episodeId"] != job.episodeId:
                            raise PolicyFailure("episode_changed", "Episode changed during policy execution")
                        PandaPrediction(observation, *self.runtime.task_positions(), target_size=size).check([action])
                        job.phase = "executing_actions"
                        with self.lock:
                            if self.cancelled.is_set() or self.revoked:
                                raise PolicyFailure("cancelled", "Control was revoked before submission")
                            start = self.control_call("action", self.lease | {
                                "sequence": job.actionsApplied, "action": action.tolist(),
                                "observationId": source["observationId"]})["step"]
                            submissions.write(json.dumps({"sequence": job.actionsApplied, "prefix": prefix_index,
                                "sourceStep": source["step"], "episodeId": job.episodeId,
                                "controlGeneration": self.lease["controlGeneration"], "startStep": start,
                                "plannedEndStep": start + 25, "raw_action": raw.tolist(),
                                "applied_action": action.tolist()}) + "\n")
                        status = self._advance(start + 25, began + self.config.max_wall_s)
                        completed = status["lastCompleted"]
                        expected = {"sequence": job.actionsApplied, "controlGeneration": self.lease["controlGeneration"],
                                    "startStep": start, "plannedEndStep": start + 25, "endStep": start + 25}
                        if completed != expected:
                            write_json(folder / "interrupted-action.json", {"expected": expected, "control": status})
                            raise PolicyFailure("cancelled" if self.cancelled.is_set() else "action_not_applied",
                                                "Action completion receipt was interrupted")
                        log.write(json.dumps({"sequence": job.actionsApplied, "prefix": prefix_index,
                            "sourceStep": source["step"], "episodeId": job.episodeId, "raw_action": raw.tolist(),
                            "applied_action": action.tolist(), "receipt": completed,
                            "prediction": prediction, "site_position": status["sitePosition"]}) + "\n")
                        job.actionsApplied += 1
                        observation, judgment = self.runtime.policy_observation(force=True)
                        self._record_observation(observation, folder, job.actionsApplied)
                        write_json(folder / "latest-judgment.json", judgment)
                    prefix_index += 1
                    if judgment["grasp"] == "confirmed" and (job.skill == PICK or judgment["placement"] == "confirmed"):
                        job.goalStatus = "achieved"
                        job.evidence = judgment
                        reason = "goal_achieved"
                        break
                if job.goalStatus != "achieved":
                    if job.skill == PICK or judgment["placement"] == "failed":
                        job.goalStatus = "not_achieved"
                    if job.skill == PICK:
                        job.evidence = judgment
        except PolicyFailure as exc:
            with self.lock:
                reason = job.reasonCode or exc.code
                if not job.reasonCode:
                    job.message = str(exc)
        except Exception as exc:
            reason = "execution_error"
            job.message = f"{type(exc).__name__}: {exc}"
        finally:
            job.phase = "stopping"
            try:
                self._revoke()
                start = self.status()["step"]
                stop_deadline = time.monotonic() + self.config.stop_wall_s
                status = self.status()
                for index in range(100):
                    if time.monotonic() >= stop_deadline:
                        break
                    status = self._advance(start + (index + 1) * 25, stop_deadline, stopping=True)
                    observed, judgment = self.runtime.policy_observation(force=True)
                    self._record_observation(observed, folder, job.actionsApplied + index + 1)
                    if index >= 19 and status["stopState"] == "stopped":
                        break
                job.stopStatus = "confirmed" if not status["leased"] and status["stopState"] == "stopped" else "unknown"
                write_json(folder / "stop.json", status)
                if job.skill == PICK and job.goalStatus == 'achieved' and judgment['grasp'] != 'confirmed':
                    job.goalStatus = 'unconfirmed'
                    job.evidence = judgment
                    reason = 'grasp_not_retained'
            except Exception as exc:
                job.stopStatus = "unknown"
                job.evidence["stopError"] = str(exc)
            try:
                self.link.release(self.hold)
            except Exception as exc:
                job.stopStatus = "unknown"
                job.evidence["clockReleaseError"] = str(exc)
            keeper_done.set()
            if keeper.ident is not None:
                keeper.join(timeout=4)
            if keeper.is_alive():
                job.stopStatus = "unknown"
                job.evidence["leaseCleanupError"] = "Lease worker did not finish"
            with self.lock:
                self.blocked = self.blocked or job.stopStatus != "confirmed"
                job.reasonCode = reason
                if job.skill == PICK and reason == 'prediction_rejected':
                    self.policy_fault = {'episodeId': job.episodeId, 'job': job.id, 'reasonCode': reason}
                job.state = ("succeeded" if reason == "goal_achieved" and not self.blocked else
                             "stopped" if reason == "cancelled" and not self.blocked else "failed")
                job.phase = "finished"
                job.finished = self.runtime.sensor.observation["stamp"]
                if not job.message:
                    job.message = reason
                write_json(folder / "job.json", job.to_dict())
                self.finished.set()
