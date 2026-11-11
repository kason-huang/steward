"""Archive Step 7 results and render dual-camera videos without changing robot evidence."""

import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageDraw

from steward.ego.panda_policy import write_json

FINAL = ("N1-06", "N2-03", "C1-03", "F1-02")


def encode(job, destination, ffmpeg):
    destination.mkdir(exist_ok=True)
    frames = sorted((job / "external").glob("*.png"))
    if not frames:
        return
    for role in ("external", "wrist"):
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-framerate", "20", "-i",
                        str(job / role / "%04d.png"), "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(destination / f"{role}.mp4")], check=True)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(destination / "external.mp4"), "-i", str(destination / "wrist.mp4"),
                    "-filter_complex", "hstack=inputs=2", "-c:v", "libx264", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination / "dual.mp4")], check=True)
    selected = sorted(set(round(i * (len(frames) - 1) / 5) for i in range(6)))
    sheet = Image.new("RGB", (256 * len(selected), 280 * 2), "#10141c")
    draw = ImageDraw.Draw(sheet)
    for column, index in enumerate(selected):
        for row, role in enumerate(("external", "wrist")):
            frame = job / role / frames[index].name
            with Image.open(frame) as image:
                sheet.paste(image.convert("RGB"), (column * 256, row * 280 + 24))
            draw.text((column * 256 + 8, row * 280 + 6), f"{role} frame {index:04d}", fill="white")
    sheet.save(destination / "contact-sheet.jpg", quality=90)
    write_json(destination / "timing.json", {"frames": len(frames), "fps": 20,
               "durationS": len(frames) / 20, "clock": "simulation time; frozen inference waits are omitted",
               "sourceFrames": str(job), "observationTimestamps": str(job / "observations.jsonl")})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--ffmpeg", required=True)
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    raw = destination / "raw-evidence"
    if not raw.exists():
        raw.symlink_to(source, target_is_directory=True)
    attempts = []
    for folder in sorted(source.iterdir()):
        if not folder.is_dir() or not re.fullmatch(r"(?:N[12]|C1|F1|G1)-\d+|api-real-\d+", folder.name):
            continue
        result_path = folder / "result.json"
        if not result_path.exists() and not (source / (folder.name + ".log")).exists():
            continue
        archive = destination / "attempts" / folder.name
        shutil.copytree(folder, archive, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("external", "wrist", "sensors", "__pycache__"))
        for name in (folder.name + ".log", folder.name + "-preflight.json", "policy-" + folder.name + ".log"):
            if (source / name).exists():
                shutil.copy2(source / name, archive / name)
        result = json.loads(result_path.read_text()) if result_path.exists() else {
            "pass": False, "reason": "Verification harness did not produce a result; see preserved execution log"}
        if not result_path.exists():
            write_json(archive / "result.json", result)
        job = result.get("job", {})
        jobs = list(folder.glob("runtime/ego-panda/policy-jobs/panda-job-*"))
        if len(jobs) == 1:
            encode(jobs[0], archive / "videos", args.ffmpeg)
            write_json(archive / "raw-source.json", {"directory": str(folder), "jobDirectory": str(jobs[0]),
                       "rawRgb": [str(jobs[0] / role) for role in ("external", "wrist")],
                       "rawRgbd": str(jobs[0] / "sensors")})
        attempts.append({"attempt": folder.name, "selected": folder.name in FINAL,
                         "pass": result["pass"], "state": job.get("state"), "goalStatus": job.get("goalStatus"),
                         "stopStatus": job.get("stopStatus"), "actionsApplied": job.get("actionsApplied"),
                         "reasonCode": job.get("reasonCode", result.get("reason")),
                         "worldSuccess": result.get("world", {}).get("success"),
                         "result": f"attempts/{folder.name}/result.json",
                         "video": f"attempts/{folder.name}/videos/dual.mp4" if jobs else None})
    selected = [row for row in attempts if row["selected"]]
    write_json(destination / "batch-results.json", {"scenario": "libero-object-alphabet-soup-panda", "initialState": 0,
               "formalCases": list(FINAL), "allFormalPassed": len(selected) == 4 and all(row["pass"] for row in selected),
               "scope": "Functional integration and stopping, not a benchmark success-rate estimate", "attempts": attempts})
    checks = destination / "checks"
    checks.mkdir(exist_ok=True)
    for path in source.glob("*.log"):
        if any(word in path.name for word in ("test", "regression", "profile", "focused")):
            shutil.copy2(path, checks / path.name)
    config = destination / "configuration"
    config.mkdir(exist_ok=True)
    for name in ("policy.toml", "batch-plan.json", "ego-composed.yml"):
        if (source / name).exists():
            shutil.copy2(source / name, config / name)
    cards = []
    for row in selected:
        label = {"N1": "聊天发起并保存能力", "N2": "获取能力后嵌套执行", "C1": "动作执行中停止", "F1": "策略服务断连"}[row["attempt"][:2]]
        url = "attempts/" + row["attempt"]
        cards.append(f'<article><h2>{row["attempt"]} · {label}</h2><p class="ok">PASS · '
                     f'{row["state"]} / {row["goalStatus"]} / {row["stopStatus"]}</p>'
                     f'<p>{row["actionsApplied"]} 条完成动作 · {row["reasonCode"]}</p>'
                     f'<video controls preload="metadata" src="{url}/videos/dual.mp4"></video>'
                     f'<p><a href="{url}/videos/external.mp4">外部视角</a> · <a href="{url}/videos/wrist.mp4">腕部视角</a> · '
                     f'<a href="{url}/result.json">完整结果</a> · <a href="{url}/chat-transcript.json">聊天与工具记录</a></p></article>')
    rows = "".join(f'<tr><td>{r["attempt"]}</td><td>{r["pass"]}</td><td>{html.escape(str(r["reasonCode"]))}</td>'
                   f'<td><a href="{r["result"]}">结果</a></td></tr>' for r in attempts)
    (destination / "index.html").write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Step 7 · Panda 策略集成实测</title>
<style>body{background:#10141c;color:#e8edf6;font:16px/1.6 system-ui;max-width:1200px;margin:auto;padding:32px}h1{margin-bottom:8px}p{color:#b7c5d9}a{color:#80b8ff}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:24px}article{background:#192232;border:1px solid #324155;border-radius:12px;padding:22px}video{width:100%;background:#000;border-radius:6px}.ok{color:#80e5ad}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:10px;border-bottom:1px solid #324155}h2{font-size:20px}@media(max-width:550px){body{padding:16px}main{display:block}article{margin-bottom:20px}}</style>
<h1>Step 7 · Panda 策略 Job 与真实 Ego Harness</h1>
<p>场景：libero-object-alphabet-soup-panda · LIBERO 初态 0 · 真实 pi05_libero · 真实 ego profile</p>
<p>任务类别：抓取与放置。策略 prompt：pick up the alphabet soup and place it in the basket</p>
<p>左侧为外部视角，右侧为腕部视角。录像按仿真时间播放，推理等待期间仿真冻结。四例用于功能验收，正式成功率留待 Step 8。</p>
<p><a href="README.md">验收报告与复现命令</a> · <a href="batch-results.json">全部批次结果</a> · <a href="SHA256SUMS">校验清单</a></p><main>'''
        + "".join(cards) + '</main><h2>全部尝试，含原始失败</h2><table><tr><th>尝试</th><th>通过</th><th>结束原因</th><th>记录</th></tr>'
        + rows + '</table></html>\n')
    sums = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS" and "raw-evidence" not in path.parts:
            sums.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + str(path.relative_to(destination)))
    (destination / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(json.dumps({"attempts": len(attempts), "formal": len(selected), "allFormalPassed": len(selected) == 4
                     and all(row["pass"] for row in selected), "destination": str(destination)}))


if __name__ == "__main__":
    main()
