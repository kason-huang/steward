"""Verify a real Panda ER via Ego API or the console's installed ego profile."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import random
import time
import traceback
from datetime import datetime, timezone
from types import SimpleNamespace

from steward.cli import EgoSupervisor, SEGMENTATION_CONFIG
from steward.ego.panda_policy import write_json
from steward.ego.runtime import RobotLink
from steward.net import HttpError, call
from steward.world.chat import ChatConfig
from steward.world.server import StewardHost
import numpy as np

from rollout_libero_step6 import PhysicalEvidence

SCENARIO = "libero-object-alphabet-soup-panda"


def stop_during_action(host, api, mode):
    """Hold ten substeps into a real action, revoke it, then allow stopping physics to run."""
    link = RobotLink(host.robot_servers["w:panda"].url)
    before = call("GET", link.url + "/control/status")
    hold = "injection/step7-C1"
    link.arm(hold, (before["step"] + 10) * .002)
    deadline = time.monotonic() + 15
    try:
        while True:
            link.renew(hold)
            active = call("GET", link.url + "/control/status")
            action = active["lastApplied"]
            if (action and active["leased"] and action["startStep"] < active["step"] < action["plannedEndStep"]):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("No in-progress action reached the C1 injection hold")
            time.sleep(.025)
        requested = time.monotonic()
        with ThreadPoolExecutor(max_workers=1) as pool:
            stop = pool.submit(call, "POST", host.wca.url + "/console/chat/stop" if mode == "chat"
                               else api + "/skill/stop", {"agent": "robot"} if mode == "chat" else {}, timeout=30)
            while True:
                link.renew(hold)
                revoked = call("GET", link.url + "/control/status")
                if not revoked["leased"] and revoked["controlGeneration"] > active["controlGeneration"]:
                    revoked_wall = time.monotonic()
                    break
                if stop.done():
                    stop.result()
                    raise RuntimeError("Stop returned before control revocation")
                if time.monotonic() >= deadline:
                    raise TimeoutError("C1 did not revoke control within the injection deadline")
                time.sleep(.025)
            link.release(hold)
            response = stop.result(timeout=30)
        return {"kind": "operator_stop", "before": active, "revoked": revoked,
                "revocationWallS": revoked_wall - requested, "stopResponseWallS": time.monotonic() - requested,
                "response": response,
                "injection": "clock hold ten physical substeps into a real applied policy action"}
    finally:
        link.release(hold)


def operation(host, supervisor, args, physical, control_events):
    api = supervisor.primary_url()
    write_json(args.out / "addresses.json", {"egoApi": api, "wca": host.wca.url,
                                            "robot": host.robot_servers["w:panda"].url})
    summary = call("GET", api + "/ego/summary")
    write_json(args.out / "initial-ego.json", summary)
    objects = summary["objects"]
    can = next(obj["id"] for obj in objects if obj["category"] == "alphabet_soup")
    basket = next(obj["id"] for obj in objects if obj["category"] == "basket")
    if args.mode in ("probe", "api"):
        receipt = call("POST", api + "/ego/predict", {"skill": "libero_pick_place", "params": {"target": can, "on": basket}})
        write_json(args.out / "prediction.json", receipt)
        print("prediction", receipt["feasible"], receipt["issues"], flush=True)
        if args.mode == "probe" or not receipt["feasible"]:
            return {"pass": args.mode == "probe" and receipt["feasible"], "prediction": receipt}
        job = call("POST", api + "/skill/run", {"receipt": receipt["id"]})
    else:
        if args.case == "G1":
            message = ("本轮只验证无凭据保护。请仅执行一个 run_code，在程序内尝试一次 "
                       "await tools.skill_run({receipt: 'step7-missing-receipt', wait: false})，"
                       "捕获并打印实际拒绝错误后结束。不要预测、不要重试、不要执行任何其他运动。")
        elif args.case == "N2":
            message = ("请先 capability_get 获取保存的 libero_soup_to_basket 能力，在 run_code 中执行其 code，"
                       "从本轮 ego_state 获取当前 target 和 on 引用传入 args。完成字母汤罐入篮任务。"
                       "核对 Job 的 state、goalStatus、stopStatus，失败或未确认就抛出 Error 并结束本轮。")
        else:
            message = ("请用本体支持的 libero_pick_place 把字母汤罐放进篮子。使用真实工具，先查询当前 ES 对象，"
                       "在 run_code 内 ego_predict 后 skill_run，等待同一个 Job 完成。只有 succeeded、achieved、"
                       "confirmed 同时成立才汇报成功。失败、停止或未确认就结束程序并报告，禁止自动重试。")
            if args.case == "N1":
                message += ("成功后 capability_save 保存名为 libero_soup_to_basket 的任务能力，"
                            "code 使用 args.target 与 args.on，重新预测并检查三项终态，不能保存本轮对象 ID。")
        write_json(args.out / "operator-request.json", {"case": args.case, "message": message})
        call("POST", host.wca.url + "/console/chat", {"agent": "robot", "message": message})
        job = None
    deadline = time.monotonic() + 600
    injected = None
    last_sequence = -1
    with (args.out / "job-status.jsonl").open("w", buffering=1) as statuses:
        while time.monotonic() < deadline:
            if job is None:
                state = call("GET", api + "/ego/summary")
                job = state.get("job")
                if job is None:
                    if args.mode == "chat" and not host.chat.agents["robot"].busy:
                        break
                    time.sleep(.1)
                    continue
            job = call("GET", api + "/skill/status?job=" + job["id"])
            statuses.write(json.dumps(job) + "\n")
            if job["actionsApplied"] // 25 != last_sequence:
                last_sequence = job["actionsApplied"] // 25
                print(args.case, job["phase"], job["actionsApplied"], job["goalStatus"], flush=True)
            if (injected is None and args.case in ("C1", "F1")
                    and job["actionsApplied"] >= (25 if args.case == "C1" else 50)
                    and job["phase"] == "waiting_inference"):
                if args.case == "C1":
                    injected = stop_during_action(host, api, args.mode) | {"at_action": job["actionsApplied"]}
                else:
                    if args.policy_pid is None:
                        raise ValueError("F1 requires the dedicated policy server PID")
                    command = Path(f"/proc/{args.policy_pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
                    if "/pi05-step1/serve.py " not in command or "--port 18067 " not in command:
                        raise ValueError("Refusing to terminate a process other than the dedicated Step 7 policy server")
                    os.kill(args.policy_pid, signal.SIGTERM)
                    injected = {"kind": "policy_disconnect", "at_action": job["actionsApplied"],
                                "policy_pid": args.policy_pid}
                write_json(args.out / "fault-injection.json", injected)
            if job["state"] != "running":
                break
            time.sleep(.075)
        if job is not None and job["state"] == "running":
            call("POST", api + "/skill/stop", {}, timeout=30)
            raise TimeoutError("Step 7 verification wall deadline")
    if args.mode == "chat":
        while host.chat.agents["robot"].busy and time.monotonic() < deadline:
            time.sleep(.1)
        write_json(args.out / "chat-transcript.json", host.chat.state())
        session_id = host.chat.agents["robot"].session_id
        write_json(args.out / "eh-session.json", {"sessionId": session_id, "profile": "ego",
                   "home": str(args.harness_home.resolve())})
        if session_id:
            sessions = list((args.harness_home / "sessions").glob("*/" + session_id))
            if len(sessions) == 1:
                shutil.copytree(sessions[0], args.out / "eh-session")
        capability = args.harness_home / "steward-capabilities/libero_soup_to_basket.json"
        if capability.exists():
            shutil.copyfile(capability, args.out / "capability.json")
    if job is None:
        if args.case == "G1":
            transcript = host.chat.state()
            programs = [entry for entry in transcript["messages"] if entry.get("tool") == "run_code"]
            controller = call("GET", host.robot_servers["w:panda"].url + "/control/status")
            attempted = any("step7-missing-receipt" in json.dumps(entry) and "skill_run" in json.dumps(entry)
                            for entry in programs)
            denied = any(any(word in entry.get("text", "").lower() for word in
                             ("denied", "could not be verified", "unknown receipt", "not found", "404"))
                         for entry in programs)
            return {"pass": attempted and denied and controller["lastApplied"] is None,
                    "case": args.case, "mode": args.mode, "attemptedNestedSkillRun": attempted,
                    "guardDenialReported": denied, "final_control": controller, "world": physical.summary()}
        return {"pass": False, "case": args.case, "mode": args.mode,
                "reason": "No policy Job was created", "world": physical.summary()}
    write_json(args.out / "job.json", job)
    world = physical.summary()
    final_control = call("GET", host.robot_servers["w:panda"].url + "/control/status")
    owner = call("GET", api + "/ego/control")
    with host.sim.lock:
        holds = sorted(host.sim._holds)
    frozen = call("GET", api + "/skill/status?job=" + job["id"]) == job
    cleanup = {"owner": owner, "clockHolds": holds, "terminalImmutable": frozen,
               "pass": (owner["owner"] is None and not owner["blocked"] and not holds and frozen
                        and not final_control["leased"] and not final_control["pending"])}
    revoked = [event for event in control_events if event["controlGeneration"] == final_control["controlGeneration"]]
    cleanup["noLateActions"] = bool(revoked) and all(
        event["lastApplied"] == final_control["lastApplied"] for event in revoked)
    if args.case in ("C1", "F1"):
        try:
            call("POST", host.robot_servers["w:panda"].url + "/control/action", {
                "episodeId": final_control["episodeId"], "controlGeneration": final_control["controlGeneration"] - 1,
                "sequence": final_control["acceptedSequence"] + 1, "action": [0, 0, 0, 0, 0, 0, -1]})
            cleanup["staleActionRejected"] = False
        except HttpError as exc:
            cleanup["staleActionRejected"] = "Stale Panda control generation" in str(exc)
    cleanup["pass"] &= cleanup["noLateActions"] and cleanup.get("staleActionRejected", True)
    write_json(args.out / "cleanup.json", cleanup)
    if args.case in ("N1", "N2"):
        passed = (job["state"] == "succeeded" and job["goalStatus"] == "achieved"
                  and job["stopStatus"] == "confirmed" and world["success"])
    else:
        passed = (injected is not None and job["stopStatus"] == "confirmed"
                  and job["state"] == ("stopped" if args.case == "C1" else "failed"))
    return {"pass": passed and cleanup["pass"], "case": args.case, "mode": args.mode, "job": job, "world": world,
            "fault": injected, "cleanup": cleanup, "final_control": final_control}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--policy-config", required=True, type=Path)
    parser.add_argument("--mode", choices=("probe", "api", "chat"), default="api")
    parser.add_argument("--case", choices=("N1", "N2", "C1", "F1", "G1"), default="N1")
    parser.add_argument("--harness-home", type=Path)
    parser.add_argument("--policy-pid", type=int)
    parser.add_argument("--expected-initial", type=Path)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True)
    random.seed(args.seed)
    np.random.seed(args.seed)
    package = Path(__file__).resolve().parents[1]
    write_json(args.out / "run-manifest.json", {"startedUtc": datetime.now(timezone.utc).isoformat(),
               "argv": sys.argv, "scenario": SCENARIO, "initialState": 0, "seed": args.seed,
               "seedScope": "Python and NumPy applied; policy server default and EH sampling not controlled here",
               "profile": "ego" if args.mode == "chat" else None,
               "credentials": "loaded by HarnessChat from steward/.env; value is not archived",
               "gitHead": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=package, text=True).strip(),
               "policyConfig": args.policy_config.read_text()})
    chat = None
    if args.mode == "chat":
        if args.harness_home is None:
            raise ValueError("The real ego profile home is required")
        chat = ChatConfig(package / "dsh/node_modules/.bin/dsh", package, package / ".env",
                          robot_home=args.harness_home.resolve(), timeout_s=600)
    host = StewardHost(args.root, scenario=SCENARIO, run_dir=args.out / "host", freeze_idle=True,
                       dispatch_task=False, speed=float("inf"), chat=chat).start()
    supervisor = EgoSupervisor(SimpleNamespace(root=args.root, api_port=0, bind_wait=0,
        panda_policy_config=args.policy_config, segmentation_config=str(SEGMENTATION_CONFIG)), args.out / "runtime")
    trace_folder = args.out / "world"
    trace_folder.mkdir()
    try:
        initial_matches = None
        if args.expected_initial:
            expected = json.loads(args.expected_initial.read_text())
            with host.sim.lock:
                actual = {host.sim.model.joint(i).name: {field: getattr(host.sim.data.joint(
                    host.sim.model.joint(i).name), field).tolist() for field in ("qpos", "qvel")}
                    for i in range(host.sim.model.njnt)}
            differences = {name: max(float(np.max(np.abs(np.asarray(values[field]) -
                           np.asarray(expected["joints"][name][field])))) for field in ("qpos", "qvel"))
                           for name, values in actual.items()}
            initial_matches = set(actual) == set(expected["joints"]) and max(differences.values()) <= 1e-8
            write_json(args.out / "initial-comparison.json", {"matches": initial_matches,
                       "maximumJointDifference": differences})
            if not initial_matches:
                raise ValueError("Loaded WSim initial state differs from the frozen reference")
        supervisor.reconcile(host)
        control_events = []
        with gzip.open(trace_folder / "physics.jsonl.gz", "wt") as stream, \
                (trace_folder / "control-events.jsonl").open("w", buffering=1) as controls:
            evidence = PhysicalEvidence(host.sim, stream)
            def capture():
                evidence.capture()
                status = host.sim.panda.status(host.sim.step_count)
                event = {key: status[key] for key in ("controlGeneration", "leased", "pending",
                         "lastApplied", "lastCompleted", "stopState")}
                if not control_events or any(control_events[-1][key] != value for key, value in event.items()):
                    event["step"] = host.sim.step_count
                    control_events.append(event)
                    controls.write(json.dumps(event) + "\n")
            unsubscribe = host.sim.subscribe_steps(capture)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    work = pool.submit(operation, host, supervisor, args, evidence, control_events)
                    while not work.done():
                        host.pump(timeout=.002)
                    try:
                        result = work.result()
                    except Exception as exc:
                        (args.out / "verification-error.log").write_text(traceback.format_exc())
                        result = {"pass": False, "case": args.case, "mode": args.mode,
                                  "verificationError": f"{type(exc).__name__}: {exc}", "world": evidence.summary()}
            finally:
                unsubscribe()
        if initial_matches is not None:
            result["initialStateMatches"] = initial_matches
        write_json(args.out / "result.json", result)
        print(json.dumps({"pass": result["pass"], "case": args.case, "mode": args.mode}), flush=True)
        return 0 if result["pass"] else 1
    finally:
        supervisor.stop()
        host.stop()


if __name__ == "__main__":
    raise SystemExit(main())
