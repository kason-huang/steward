"""Run the frozen initial-state-0 native reference and real Ego Harness smoke test."""

import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import time

from steward.world.server import StewardHost
from steward.world.panda_sensors import calibration
from step8_metrics import episode, aggregate

FFMPEG = "/home/huawei/hqk/workspace/pi05-step1/server-mirror-venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"
SERVICE = Path("/home/huawei/hqk/workspace/pi05-step1")


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def command(argv, cwd, log, timeout, journal):
    started = time.monotonic()
    with log.open("wb") as stream:
        process = subprocess.Popen([str(v) for v in argv], cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                code = process.wait()
    record = {"argv": [str(v) for v in argv], "cwd": str(cwd), "exitCode": code,
              "timedOut": timed_out, "wallSeconds": time.monotonic()-started, "log": str(log)}
    with journal.open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    return record


def service(package, output, port, journal):
    try:
        with socket.create_connection(("127.0.0.1", port), .3):
            raise RuntimeError("Dedicated Step 8 port already in use; refusing to replace its process")
    except OSError:
        pass
    stream = (output / "policy-server.log").open("wb")
    try:
        process = subprocess.Popen([str(package / ".venv/bin/python"), str(package / "scripts/launch_pi05_libero.py"),
                                   "--port", str(port), "--evidence", str(output / "restore")], stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except BaseException:
        stream.close()
        raise
    try:
        write(output / "policy-process.json", {"pid": process.pid, "port": port, "start": "fresh pinned server"})
        deadline = time.monotonic()+90
        while True:
            if process.poll() is not None:
                raise RuntimeError("Policy server exited before listening")
            try:
                with socket.create_connection(("127.0.0.1",port), .3):
                    break
            except OSError:
                if time.monotonic()>deadline:
                    raise TimeoutError("Policy startup exceeded 90 s")
                time.sleep(.5)
        warmup = command(["bash", SERVICE / "client.sh", package / "scripts/probe_pi05_step6.py",
            SERVICE / "runs/20261110T021656Z/target-qualification/policy/000.npz", output / "preflight.json",
            "--port", port], package, output / "preflight.log", 60, journal)
        if warmup["exitCode"] or warmup["timedOut"]:
            raise RuntimeError("Single preflight failed; no output-based server selection is allowed")
        return process, stream
    except BaseException:
        stop_service(process, stream)
        raise


def stop_service(process, stream):
    try:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    finally:
        stream.close()


def completion_check(folder):
    from libero_step4_evidence import BASE, PADS, force
    jobs = list(folder.glob("runtime/ego-panda/policy-jobs/panda-job-*"))
    if len(jobs) != 1:
        return {"pass": False, "jobCount": len(jobs), "reason": "Expected exactly one policy Job"}
    history = [json.loads(line) for line in (jobs[0]/"observations.jsonl").read_text().splitlines()]
    with gzip.open(folder/"world/physics.jsonl.gz", "rt") as stream:
        physics = {row["step"]: row for row in map(json.loads,stream)}
    comparisons = []
    result = json.loads((folder/"result.json").read_text())
    grasp_step = next((row["step"] for row in history if row["judgment"]["grasp"]=="confirmed"),None)
    grasp = physics.get(grasp_step)
    grasp_supported = bool(grasp and grasp["can"][2]-result["world"]["initial_can"][2] >= .015
                           and not force(grasp,"floor") and all(force(grasp,pad)>=.1 for pad in PADS))
    for row in history:
        if row["judgment"]["placement"] != "confirmed":
            continue
        truth = physics.get(row["step"])
        released = result["world"].get("release")
        grounded = bool(truth and released and released["step"] <= row["step"] and truth["inside"]
                        and truth["bottom_force_n"] >= .05 and truth["tool_clearance_m"] >= .08
                        and all(force(truth,pad)<.05 for pad in PADS)
                        and not any(value>0 and BASE not in (a,b) for a,b,value,_ in truth["contacts"]))
        comparisons.append({"step":row["step"], "placement": "confirmed", "worldSupportsClaim": grounded})
    return {"pass": grasp_supported and bool(comparisons) and all(row["worldSupportsClaim"] for row in comparisons),
            "jobCount": 1, "placementClaims": comparisons,
            "firstGraspConfirmed": grasp_step, "worldSupportsGraspClaim": grasp_supported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--root", type=Path, default=Path("var/step4/wsim-root"))
    parser.add_argument("--port", type=int, default=18068)
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    repo = package.parent
    args.root = args.root.resolve()
    output = args.out.resolve()
    output.mkdir(parents=True)
    frozen = output/"frozen"
    frozen.mkdir()
    journal = output/"commands.jsonl"
    envpath = package/"var/dsh-toolchain"
    os.environ["PATH"] = str(envpath/"bin")+":"+str(envpath/"node/bin")+":"+os.environ["PATH"]
    host = StewardHost(args.root, scenario="libero-object-alphabet-soup-panda", freeze_idle=True,
                       dispatch_task=False, speed=float("inf")).start()
    try:
        initial = {"joints": {host.sim.model.joint(i).name: {field:getattr(host.sim.data.joint(
            host.sim.model.joint(i).name), field).tolist() for field in ("qpos","qvel")}
            for i in range(host.sim.model.njnt)}}
        write(frozen/"initial.json",initial)
        write(frozen/"calibration.json",calibration(host.sim.model))
    finally:
        host.stop()
    cap_source = repo/"self/design/pi05-intergration/steps/step7-results/attempts/N1-06/capability.json"
    cap = json.loads(cap_source.read_text())
    assert cap["version"] == 2
    write(frozen/"capability.json",cap)
    config = (package/"var/step7/policy.toml").read_text()
    import re
    config, count = re.subn(r"(?m)^port\s*=\s*\d+", "port = "+str(args.port), config)
    assert count==1
    (frozen/"policy.toml").write_text(config)
    source_home = package/"var/step7/dsh/robot-1"
    home = output/"robot-home"
    shutil.copytree(source_home/"profiles", home/"profiles", symlinks=True,
                    ignore=shutil.ignore_patterns("node_modules", ".plugin-manager"))
    for source in (source_home/"profiles").glob("*/node_modules"):
        target = home/"profiles"/source.parent.name/"node_modules"
        target.symlink_to(source.resolve(),target_is_directory=True)
    (home/"workspace").mkdir()
    (home/"steward-capabilities").mkdir()
    write(home/"steward-capabilities/libero_soup_to_basket.json",cap)
    slots = [{"runId":"libero-init0-0", "environment":"libero", "initialIndex":0,"seed":0},
             {"runId":"wsim-init0-0", "environment":"wsim", "initialIndex":0,"seed":0}]
    sources = [package/"scripts"/n for n in ("run_libero_step8.py","record_libero_step8.py",
        "libero_physical_evidence.py","step8_capture.py","step8_metrics.py","verify_libero_step7.py",
        "launch_pi05_libero.py","serve_pi05_libero.py","pi05_verified_restore.py")]
    sources += [package/"src/steward/ego"/n for n in ("panda_perception.py","panda_jobs.py","panda_prediction.py")]
    manifest = {"scope":"initial-state-0 end-to-end smoke test, not a success-rate benchmark", "slots":slots,
        "gitBase":subprocess.check_output(["git","rev-parse","HEAD"],cwd=repo,text=True).strip(),
        "branch":subprocess.check_output(["git","branch","--show-current"],cwd=repo,text=True).strip(),
        "codeSHA256":{str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "capabilityVersion":2,"capabilityCodeSHA256":hashlib.sha256(cap["code"].encode()).hexdigest(),
        "policyPort":args.port,"randomness":"Python/NumPy seed=0; pinned server defaults and EH API sampling not overridden"}
    write(frozen/"manifest.json",manifest)
    rows=[]
    for slot in slots:
        folder=output/slot["environment"]
        (output/(slot["environment"]+"-service")).mkdir()
        process,stream=service(package,output/(slot["environment"]+"-service"),args.port,journal)
        try:
            if slot["environment"]=="libero":
                argv=["bash", SERVICE/"client.sh", package/"scripts/record_libero_step8.py",folder,
                      "--calibration",frozen/"calibration.json","--expected-initial",frozen/"initial.json",
                      "--ffmpeg",FFMPEG,"--seed",0,"--port",args.port]
            else:
                argv=[package/".venv/bin/python","-u",package/"scripts/verify_libero_step7.py",args.root,folder,
                    "--policy-config",frozen/"policy.toml","--mode","chat","--case","N2",
                    "--harness-home",home,"--expected-initial",frozen/"initial.json","--seed",0]
            receipt=command(argv,package,output/(slot["environment"]+".log"),600,journal)
        finally:
            stop_service(process,stream)
        path=folder/"result.json"
        if not path.exists():
            result={"verificationError":"Child did not produce result", "command":receipt}
            write(folder/"result.json",result)
        else:
            result=json.loads(path.read_text())
        result["command"] = receipt
        write(folder/"execution-receipt.json",receipt)
        matches=True
        if slot["environment"]=="wsim":
            after=json.loads((home/"steward-capabilities/libero_soup_to_basket.json").read_text())
            matches=after["version"]==2 and after["code"]==cap["code"]
            write(folder/"completion-check.json",completion_check(folder))
        rows.append(episode(slot,result,capability_matches=matches))
        write(output/"episodes.json",rows)
        print(slot["environment"],rows[-1],flush=True)
    metrics={name:aggregate([s for s in slots if s["environment"]==name],
                           [r for r in rows if r["environment"]==name]) for name in ("libero","wsim")}
    metrics["completionCheck"]=json.loads((output/"wsim/completion-check.json").read_text())
    metrics["flowPassed"]=all(row["valid"] and row["physicalSuccess"] and row["commandCompleted"] for row in rows) and rows[1]["functionalSuccess"] and metrics["completionCheck"]["pass"]
    write(output/"metrics.json",metrics)
    print(json.dumps(metrics),flush=True)
    return 0 if metrics["flowPassed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
