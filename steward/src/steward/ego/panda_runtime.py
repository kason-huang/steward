"""Panda Ego Runtime. Observations enter through the robot port only."""

from __future__ import annotations

import base64
import copy
import json
import logging
from pathlib import Path
import threading
import time

import numpy as np

from steward.ego.panda_observation import PandaObservationRuntime, policy_inputs
from steward.ego.panda_perception import PandaEvidence
from steward.ego.panda_jobs import PandaJobs
from steward.ego.panda_policy import SKILL
from steward.ego.panda_pick import PICK, PickEvidence, pickable
from steward.math3d import matrix_from_quat, pose_compose
from steward.ego.server import ego_api
from steward.ego.view import EgoViewRenderer
from steward.glrender import encode_image
from steward.net import HttpError, JsonServer, NO_CONTENT
from steward.scene.schema import Entity, Est, Link, RigidTree, SceneState, exact, pose
from steward.viewloop import ViewLoop, ViewSettings, frame_response

_LOG = logging.getLogger(__name__)


class PandaEgoRuntime:
    def __init__(self, link, policy_config=None, out_dir=None, visual_bundle=None):
        self.link = link
        self.sensor = PandaObservationRuntime(link)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.latest = None
        self.inputs = []
        self.alerts = []
        self.alert_sequence = 0
        self.input_lock = threading.Lock()
        self.out_dir = Path(out_dir) if out_dir else None
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        self.evidence = PandaEvidence()
        self.visible_objects = []
        self.surface_key = None
        self.appearance_cache = {}
        self.pick_evidence = None
        self.pick_entity = None
        self.scene = SceneState("ego").to_dict()
        self.visual_prior = None
        if visual_bundle is not None:
            from steward.ego.panda_visual_model import PandaVisualModel
            self.visual_prior = PandaVisualModel(visual_bundle)
        renderer = EgoViewRenderer()
        self.view = ViewLoop("panda-ego", lambda settings: self._render(renderer, settings), renderer.close)
        self.executor = PandaJobs(self, policy_config, self.out_dir / "policy-jobs") if policy_config else None

    def start(self):
        self.view.start()
        self.thread = threading.Thread(target=self._run, name="panda-observations", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        try:
            if self.executor is not None:
                self.executor.close()
                if self.executor.thread is not None and self.executor.thread.is_alive():
                    raise RuntimeError('Panda policy worker did not stop')
        finally:
            try:
                if self.thread is not None:
                    self.thread.join(timeout=30)
                    if self.thread.is_alive():
                        raise RuntimeError('Panda observation worker did not stop')
            finally:
                self.view.stop(timeout=30)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.observe()
                self.observe_surfaces()
            except (OSError, HttpError, ValueError, KeyError) as exc:
                _LOG.warning("Panda observation unavailable: %s", exc)
            self.stop_event.wait(0.12)

    def observe(self, force=False):
        with self.lock:
            if self.stop_event.is_set() and not force:
                return self.latest
            observation = self.sensor.read(force=force)
            if observation is None:
                return self.latest
            tool = observation["tool"]
            encoders = observation["encoders"]
            width = abs(encoders["gripper0_finger_joint1"]["position"]
                        - encoders["gripper0_finger_joint2"]["position"])
            prefix = f"e:{observation['episodeId']}:"
            from steward.ego.panda_self_model import self_entity
            robot = self_entity(observation, prefix + "panda")
            robot.state_vars["fingerWidth"] = exact(width)
            if self.latest is None or self.latest["episodeId"] != observation["episodeId"]:
                self.evidence = PandaEvidence()
                self.visible_objects = []
                self.pick_evidence = None
                self.pick_entity = None
            can, basket, grasp, placement, reason = self.evidence.update(observation, width)
            entities = [robot]
            for identifier, category, point, size in (
                (prefix + "can", "alphabet_soup", can, [.062, .062, .075]),
                (prefix + "basket", "basket", [*basket[0], basket[1] - .065] if basket is not None else None,
                 [.15, .16, .13]),
            ):
                if point is not None:
                    entities.append(Entity(identifier, "object", exact(category),
                                           exact({"p": list(point), "q": [1, 0, 0, 0]}),
                                           RigidTree(exact("fixed"), [observed_box("observed", size)]),
                                           state_vars={"size": exact(size)}))
            entities.extend(self.visible_objects)
            pick_judgment = None
            if self.pick_evidence is not None:
                pick_judgment = self.pick_evidence.update(observation, width)
                selected = copy.deepcopy(self.pick_entity)
                selected['pose']['value']['p'] = self.pick_evidence.position.tolist()
                selected['pose']['src'] = 'fused' if pick_judgment['observedTargetPoints'] else 'predicted'
                if pick_judgment['observedTargetPoints']:
                    selected['stateVars']['observedAt'] = exact(observation['stamp']).to_dict()
                selected['stateVars']['tracking'] = exact(selected['pose']['src']).to_dict()
                self.pick_entity = selected
                entities = [e for e in entities if e.id != selected['id']]
                entities.append(Entity.from_dict(selected))
                grasp = pick_judgment['grasp']
            robot.state_vars.update({"grasp": exact(grasp), "placement": exact(placement)})
            scene = SceneState("ego", entities=entities, self_id=robot.id,
                               version=observation["step"], stamp=observation["stamp"])
            self.scene = scene.to_dict()
            self.latest = {"episodeId": observation["episodeId"], "step": observation["step"],
                           "calibrationId": observation["calibrationId"], "scene": self.scene,
                           "judgment": {"grasp": grasp, "placement": placement, "reason": reason,
                                        "target": prefix + "can" if can is not None else None,
                                        "basket": prefix + "basket" if basket is not None else None,
                                        "evidenceStep": observation["step"]}}
            if pick_judgment is not None:
                self.latest['judgment'] = pick_judgment
            self.link.telemetry(self.scene | {"episodeId": observation["episodeId"]})
            return self.latest

    def observe_surfaces(self):
        """Refresh display surfaces on the background observer, outside the policy lock."""
        with self.lock:
            observation = self.sensor.observation
            if observation is None:
                return
            key = observation['episodeId'], observation['step']
            if key == self.surface_key:
                return
            known = [e for e in self.scene['entities'] if e['role'] == 'object'
                     and e['category']['value'] in ('alphabet_soup', 'basket')]
            if self.pick_evidence is not None:
                known.append({'pose': {'value': pose(self.pick_evidence.position.tolist())},
                              'stateVars': {'size': {'value': (self.pick_evidence.size + .03).tolist()}}})
            if self.evidence.last_can is not None:
                known.append({'pose': {'value': pose(self.evidence.last_can.tolist())},
                              'stateVars': {'size': {'value': [.10, .10, .12]}}})
            # A partially occluded held target must not be reclassified as a new
            # package merely because its blue label is hidden by the fingers.
            if self.evidence.grasp == 'confirmed' and self.evidence.release is None:
                center = np.asarray(observation['tool']['p']) - [0, 0, .04]
                known.append({'pose': {'value': pose(center.tolist())},
                              'stateVars': {'size': {'value': [.14, .14, .18]}}})
            previous = self.visible_objects
        from steward.ego.panda_surfaces import surface_entities
        visible = surface_entities(observation, f'e:{key[0]}:', known, previous)
        if self.visual_prior is not None:
            self.visual_prior.load()
            for entity in visible:
                match = self.visual_prior.matcher.match(entity.to_dict())
                if match is None:
                    continue
                adjustment = match['pose']
                entity.category = Est(match['category'], src='inferred')
                entity.pose = Est(pose_compose(entity.pose.value, adjustment), src='fused')
                for surface in entity.rigid.links[0].geometry.get('observed', []):
                    surface['triangles'] = ((np.asarray(surface['triangles']) - adjustment['p'])
                                            @ matrix_from_quat(adjustment['q'])).round(6).tolist()
                entity.state_vars['size'] = exact(self.visual_prior.matcher.templates[match['category']]['size'].tolist())
                entity.state_vars['recognitionScore'] = exact(match['score'])
                entity.state_vars['canonicalVisual'] = exact(True)
        with self.lock:
            if self.stop_event.is_set() or self.latest['episodeId'] != key[0]:
                return
            self.surface_key = key
            self.visible_objects = visible
            selected = next((e for e in self.scene['entities'] if self.pick_evidence is not None
                             and e['id'] == self.pick_evidence.target['id']), None)
            self.scene['entities'] = [e for e in self.scene['entities'] if ':seen-' not in e['id']]
            self.scene['entities'].extend(e.to_dict() for e in visible)
            if selected is not None:
                self.scene['entities'] = [e for e in self.scene['entities'] if e['id'] != selected['id']]
                self.scene['entities'].append(selected)
            self.link.telemetry(self.scene | {'episodeId': key[0]})

    def bind_pick(self, target):
        """Bind an immutable receipt target to observed pick evidence for this episode."""
        with self.lock:
            selected = next((e for e in self.scene['entities'] if e['id'] == target['id']), None)
            if selected is None:
                raise ValueError('Selected pick target is no longer tracked')
            entity = copy.deepcopy(selected)
            evidence = PickEvidence(target, self.sensor.observation)
            self.pick_entity, self.pick_evidence = entity, evidence

    def bind_legacy_task(self):
        with self.lock:
            self.pick_evidence = None
            self.pick_entity = None

    def policy_observation(self, force=False):
        with self.lock:
            self.observe(force=force)
            if self.latest is None:
                raise HttpError(409, "Panda has no synchronized observation")
            return self.sensor.observation, copy.deepcopy(self.latest["judgment"])

    def task_positions(self):
        with self.lock:
            if self.pick_evidence is not None:
                return self.pick_evidence.position.copy(), None
            if self.evidence.last_can is None or self.evidence.basket is None:
                raise ValueError("Task objects have insufficient visual evidence")
            return self.evidence.last_can.copy(), np.array([*self.evidence.basket[0], self.evidence.basket[1] - .065])

    def summary(self):
        with self.lock:
            self.observe()
            if self.latest is None:
                raise HttpError(409, "Panda has no observation yet")
            objects = []
            for entity in self.scene["entities"]:
                if entity["role"] == "robot":
                    continue
                category = entity["category"]["value"]
                size = entity["stateVars"]["size"]["value"]
                item = {"id": entity["id"], "category": category, "role": category,
                    "position": entity["pose"]["value"]["p"], "sizeM": size, "extentM": size,
                    "existence": 1.0, "lastSeenAgoS": max(0, self.scene['stamp'] -
                        entity.get('stateVars', {}).get('observedAt', {}).get('value', self.scene['stamp'])),
                    "on": (self.latest["judgment"]["basket"] if category == "alphabet_soup"
                           and self.latest["judgment"]["placement"] == "confirmed" else None),
                    "categories": {category: 1.0}, "uncertaintyM": .015,
                    'categorySource': entity['category'].get('src', 'observed'),
                    'recognitionScore': entity.get('stateVars', {}).get('recognitionScore', {}).get('value')}
                item['graspable'] = pickable(item)
                objects.append(item)
            job = self.executor.current.to_dict() if self.executor and self.executor.current else None
            width = abs(self.sensor.observation["encoders"]["gripper0_finger_joint1"]["position"]
                        - self.sensor.observation["encoders"]["gripper0_finger_joint2"]["position"])
            return {"episodeId": self.latest["episodeId"], "stamp": self.sensor.observation["stamp"],
                    "version": self.latest["step"], "body": "libero-panda", "self": {
                        "toolPose": self.sensor.observation["tool"], "grasp": self.latest["judgment"]["grasp"],
                        "holding": ((self.pick_evidence.target['id'] if self.pick_evidence is not None
                                     else f"e:{self.latest['episodeId']}:can") if self.latest["judgment"]["grasp"] == "confirmed"
                                    and width < .071 and (self.pick_evidence is not None or self.evidence.release is None) else None)},
                    "objects": objects, "judgment": copy.deepcopy(self.latest["judgment"]), "job": job,
                    "skills": [PICK, SKILL] if self.executor else []}

    def query(self, category=None, near=None, radius=None):
        objects = self.summary()["objects"]
        if near:
            anchor = next((obj for obj in objects if obj["id"] == near), None)
            if anchor is None:
                raise ValueError("Unknown Ego-State reference")
            objects = [obj for obj in objects if obj["id"] != near and np.linalg.norm(
                np.subtract(obj["position"], anchor["position"])) <= (1.0 if radius is None else radius)]
        category = category.lower().replace(' ', '_') if category else category
        return [obj for obj in objects if category is None or obj["category"] == category]

    def jobs(self):
        if self.executor is None:
            raise HttpError(409, "Panda policy is not configured")
        return self.executor

    def apply(self, request, operation):
        result = operation()
        record = request | {"episodeId": self.latest["episodeId"] if self.latest else None,
                            "wallMonotonic": time.monotonic()}
        with self.input_lock:
            self.inputs.append(record)
            if self.out_dir:
                with (self.out_dir / "api-inputs.jsonl").open("a") as stream:
                    stream.write(json.dumps(record) + "\n")
        return result

    def alert(self, message, source, level="error", goal="", step="", diagnosis=""):
        _LOG.warning("%s: %s", source, message)
        with self.input_lock:
            self.alert_sequence += 1
            record = {"id": self.alert_sequence, "t": self.latest["scene"]["stamp"] if self.latest else 0,
                      "source": source, "level": level, "message": message[:2000], "count": 1,
                      "acknowledged": False, "goal": goal[:2000], "step": step[:2000],
                      "diagnosis": diagnosis[:2000]}
            self.alerts.append(record)
            del self.alerts[:-50]
            return dict(record)

    def alerts_view(self, acknowledge=False, identifier=None):
        with self.input_lock:
            if acknowledge:
                for record in self.alerts:
                    if identifier is None or record["id"] == identifier:
                        record["acknowledged"] = True
            return {"alerts": copy.deepcopy(self.alerts),
                    "active": [dict(record) for record in self.alerts if not record["acknowledged"]]}

    def reflect(self, failure):
        """Report the current policy failure from robot-local evidence without proposing a retry."""
        job = self.summary()["job"]
        reason = job["reasonCode"] if job and job["state"] != "running" else "task_unconfirmed"
        return {"cause": {"key": reason, "label": reason,
                          "advice": "End dependent work; report the Job, goalStatus and stopStatus to the operator."},
                "sought": {"name": failure.get("name"), "size": failure.get("size")},
                "targets": [], "missing": [], "candidates": []}

    def plan_view(self):
        summary = self.summary()
        job = summary["job"]
        return {"plan": None, "skill": job if job and job["state"] == "running" else None,
                "activity": [], "activityLast": 0, "boot": str(id(self)), "t": summary["stamp"]}

    def preview(self):
        with self.lock:
            observation = self.sensor.observation
            if observation is None:
                raise HttpError(409, "Panda has no observation yet")
            try:
                inputs = policy_inputs(observation)
            except ValueError as exc:
                raise HttpError(409, str(exc)) from exc
            return {"episodeId": observation["episodeId"], "step": observation["step"],
                    "observation/state": inputs["observation/state"].tolist(),
                    **{key: base64.b64encode(encode_image(inputs[key])).decode("ascii")
                       for key in ("observation/image", "observation/wrist_image")}}

    def state(self):
        return self.latest

    def _render(self, renderer, settings):
        if self.visual_prior is not None:
            self.visual_prior.load()
        with self.lock:
            scene = copy.deepcopy(self.scene)
            observation = self.sensor.observation
        if self.visual_prior is not None and observation is not None:
            from steward.ego.panda_self_model import self_entity
            robot = self_entity(observation, scene["self"], self.visual_prior.robot).to_dict()
            active = {e['id'] for e in scene['entities']}
            self.appearance_cache = {k: v for k, v in self.appearance_cache.items() if k in active}
            for i,entity in enumerate(scene["entities"]):
                if entity["role"] == "robot":
                    scene["entities"][i] = robot
                elif entity["category"]["value"] in self.visual_prior.objects:
                    category = entity['category']['value']
                    canonical = entity.get('stateVars', {}).get('canonicalVisual', {}).get('value')
                    visuals = (self.visual_prior.matcher.templates[category]['visuals'] if canonical else
                               self.visual_prior.objects[category])
                    entity["body"]["links"][0]["geometry"]["visual"] = visuals
                    entity['body']['links'][0]['geometry']['visualKey'] = category
                    entity['body']['links'][0]['geometry'].pop('observed', None)
                elif entity['category']['value'] == 'unknown':
                    if entity['id'] not in self.appearance_cache:
                        match = self.visual_prior.matcher.match(entity)
                        if match is not None:
                            self.appearance_cache[entity['id']] = match
                    match = self.appearance_cache.get(entity['id'])
                    if match is not None:
                        geometry = entity['body']['links'][0]['geometry']
                        geometry['visual'] = match['visuals']
                        geometry['visualKey'] = match['category']
                        geometry.pop('observed', None)
        return renderer.render(scene, settings), "image/jpeg", {"esVersion": scene["version"], "t": scene["stamp"]}


def observed_box(name, size):
    return Link(name, name, {"collision": [
        {"kind": "primitive", "shape": "box", "size": size, "pose": pose()}
    ]})


def panda_ego_api(runtime, port, bind_wait_s):
    def frame(query, body):
        latest = runtime.view.latest(int(query["after"]) if "after" in query else None)
        status = runtime.view.status()
        if latest is not None:
            return frame_response(latest, status)
        if status["settings"] is None:
            raise HttpError(409, "the ego view is not configured")
        if status["error"]:
            raise HttpError(503, status["error"])
        return NO_CONTENT

    routes = {
        ("GET", "/ego/observation"): lambda q, b: runtime.preview(),
        ("GET", "/ego/state"): lambda q, b: runtime.state(),
        ("POST", "/ego/observe"): lambda q, b: runtime.observe(),
        ("GET", "/ego/view"): lambda q, b: runtime.view.status(),
        ("POST", "/ego/view"): lambda q, b: runtime.view.configure(ViewSettings.parse(b)),
        ("GET", "/ego/view/frame"): frame,
        ("GET", "/ego/summary"): lambda q, b: runtime.summary(),
        ("GET", "/ego/query"): lambda q, b: {"objects": runtime.query(q.get("category"), q.get("near"),
                                                   float(q["radius"]) if "radius" in q else None)},
        ("POST", "/ego/predict"): lambda q, b: runtime.jobs().predict(b["skill"], b.get("params", {})),
        ("GET", "/ego/receipt"): lambda q, b: runtime.jobs().receipt(q["id"]),
        ("GET", "/ego/control"): lambda q, b: runtime.jobs().control(),
        ("POST", "/ego/control"): lambda q, b: runtime.jobs().control(b.get("action"), b.get("owner")),
        ("POST", "/skill/run"): lambda q, b: runtime.jobs().run(b["receipt"], b.get("owner")),
        ("GET", "/skill/status"): lambda q, b: runtime.jobs().job(q["job"]),
        ("POST", "/skill/wait"): lambda q, b: runtime.jobs().wait(b["job"], float(b.get("timeoutS", 120))),
        ("POST", "/skill/stop"): lambda q, b: runtime.jobs().stop(),
        ("GET", "/ego/inputs"): lambda q, b: {"inputs": runtime.inputs},
        ("POST", "/ego/alert"): lambda q, b: runtime.alert(b.get("message", ""), b.get("source", "agent"),
                    b.get("level", "error"), b.get("goal", ""), b.get("step", ""), b.get("diagnosis", "")),
        ("GET", "/ego/alerts"): lambda q, b: runtime.alerts_view(),
        ("POST", "/ego/alerts/ack"): lambda q, b: runtime.alerts_view(True, b.get("id")),
        ("POST", "/ego/reflect"): lambda q, b: runtime.reflect(b),
        ("GET", "/ego/plan"): lambda q, b: runtime.plan_view(),
    }
    return ego_api(runtime, port, bind_wait_s, routes=routes)
