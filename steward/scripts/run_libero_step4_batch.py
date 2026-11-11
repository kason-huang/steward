"""Run the frozen Step 4 batch in both simulators, preserving failed trials."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess

CASES = [(f"normal-{index}", index) for index in range(5)] + [
    (f"{case}-0", 0) for case in ("empty", "offset", "weak", "rim", "outside")
]


def run_command(command, cwd, log):
    env = os.environ | {"MUJOCO_GL": "egl", "OPENBLAS_NUM_THREADS": "1"}
    with log.open("wb") as stream:
        return subprocess.run(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT).returncode


def trial(repo, root, protocol, out, ffmpeg):
    out.mkdir()
    digest = hashlib.sha256(protocol.read_bytes()).hexdigest()
    native = run_command(
        ["bash", "../pi05-step1/client.sh", str(repo / "steward/scripts/record_libero_step4.py"),
         str(out / "libero"), "--protocol", str(protocol), "--video"],
        repo, out / "libero.log",
    )
    result = {"trial": out.name, "protocol_sha256": digest, "libero_exit": native}
    if native:
        result["infrastructure_failure"] = "原始 LIBERO 执行错误，见 libero.log"
    elif (out / "libero/protocol.json").read_bytes() != protocol.read_bytes():
        result["infrastructure_failure"] = "原始 LIBERO 重放输出的动作表与冻结版本不同"
    else:
        wsim = run_command(
            [".venv/bin/python", "scripts/verify_libero_step4.py", str(root), str(out / "libero"),
             str(out / "wsim"), "--ffmpeg", str(ffmpeg)],
            repo / "steward", out / "wsim.log",
        )
        result["wsim_exit"] = wsim
        if wsim:
            result["infrastructure_failure"] = "WSim 重放错误，见 wsim.log"
    for sim in ("libero", "wsim"):
        summary = out / sim / f"{sim}-summary.json"
        if summary.exists():
            result[sim] = json.loads(summary.read_text())
    (out / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(out.name, {sim: result.get(sim, {}).get("pass") for sim in ("libero", "wsim")},
          result.get("infrastructure_failure", ""), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("protocols", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    root, protocols, out, ffmpeg = (path.resolve() for path in (args.root, args.protocols, args.out, args.ffmpeg))
    for name, index in CASES:
        record = json.loads((protocols / f"{name}.json").read_text())
        assert record["initial_index"] == index and record.get("case", "normal") == name.rsplit("-", 1)[0]
    out.mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(trial, repo, root, protocols / f"{name}.json", out / f"{name}-r{repeat}", ffmpeg)
            for name, _ in CASES for repeat in (1, 2)
        ]
        rows = [future.result() for future in futures]
    (out / "batch.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
