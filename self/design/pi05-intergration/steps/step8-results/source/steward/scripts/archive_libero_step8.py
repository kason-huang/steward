"""Build the initial-state-0 Step 8 report, preserving all smoke attempts and failures."""

import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess

from run_libero_step8 import FFMPEG


def write(path, value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+"\n")


def video(source, destination):
    destination.mkdir(parents=True,exist_ok=True)
    native=source/"external-wrist.mp4"
    if native.exists():
        shutil.copy2(native,destination/"dual.mp4")
    else:
        jobs=list(source.glob("runtime/ego-panda/policy-jobs/panda-job-*"))
        if len(jobs)!=1:
            return False
        subprocess.run([FFMPEG,"-hide_banner","-loglevel","error","-y","-framerate","20","-i",
            str(jobs[0]/"external/%04d.png"),"-framerate","20","-i",str(jobs[0]/"wrist/%04d.png"),
            "-filter_complex","hstack=inputs=2","-c:v","libx264","-crf","20","-pix_fmt","yuv420p",
            "-movflags","+faststart",str(destination/"dual.mp4")],check=True)
    for role,x in (("external",0),("wrist",256)):
        subprocess.run([FFMPEG,"-hide_banner","-loglevel","error","-y","-i",str(destination/"dual.mp4"),
            "-vf",f"crop=256:256:{x}:0","-c:v","libx264","-crf","20","-pix_fmt","yuv420p",
            "-movflags","+faststart",str(destination/(role+".mp4"))],check=True)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source",type=Path)
    parser.add_argument("out",type=Path)
    args=parser.parse_args()
    source=args.source.resolve();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    if not (out/"raw-evidence").exists():
        (out/"raw-evidence").symlink_to(source,target_is_directory=True)
    rows=[]
    for run in sorted(source.glob("smoke-*")):
        if not run.is_dir():continue
        metrics=run/"metrics.json"
        if not metrics.exists():continue
        dest=out/"attempts"/run.name
        dest.mkdir(parents=True,exist_ok=True)
        for name in ("metrics.json","episodes.json","commands.jsonl","after-review-completion.json"):
            if (run/name).exists():shutil.copy2(run/name,dest/name)
        shutil.copytree(run/"frozen",dest/"frozen",dirs_exist_ok=True)
        for environment in ("libero","wsim"):
            for suffix in (".log",):
                if (run.parent/(run.name+suffix)).exists():shutil.copy2(run.parent/(run.name+suffix),dest/(run.name+suffix))
            shutil.copytree(run/environment,dest/environment,dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("sensors","external","wrist","robot-home","host"))
            shutil.copytree(run/(environment+"-service"),dest/(environment+"-service"),dirs_exist_ok=True)
            shutil.copy2(run/(environment+".log"),dest/(environment+".log"))
            video(run/environment,dest/environment/"videos")
        m=json.loads(metrics.read_text())
        native_result=json.loads((run/"libero/result.json").read_text())
        wsim_result=json.loads((run/"wsim/result.json").read_text())
        row={"attempt":run.name,"flowPassed":m["flowPassed"],
             "liberoPhysical":bool(m["libero"]["physicalSuccesses"]),
             "wsimPhysical":bool(m["wsim"]["physicalSuccesses"]),
             "wsimFunctional":bool(m["wsim"]["functionalSuccesses"]),
             "completionChecked":m["completionCheck"]["pass"],"source":str(run),
             "liberoActions":native_result["actionsApplied"],
             "wsimActions":wsim_result["job"]["actionsApplied"],
             "liberoFailure":None if native_result["world"]["success"] else
                 "; ".join([label for field,label in (("grasp","未抓起"),("carry","未夹持搬运"),
                            ("release","未释放")) if not native_result["world"].get(field)])+
                 "; 终态物理未通过；结束原因："+str(native_result.get("reason"))}
        rows.append(row)
    write(out/"attempts.json",rows)
    checks=out/"checks";checks.mkdir(exist_ok=True)
    for p in source.glob("*.log"):
        if p.name.startswith(("unit","regression","native-probe")):shutil.copy2(p,checks/p.name)
    cards=[]
    for row in rows:
        n=row["attempt"]
        status="通过" if row["flowPassed"] else "失败已保留"
        failure=f'<p class="failure">{html.escape(row["liberoFailure"])}</p>' if row["liberoFailure"] else ""
        cards.append(f'<article><h2>{n} · {status}</h2><p>原始 / WSim 动作数：{row["liberoActions"]} / {row["wsimActions"]}。'
                     f'原始物理：{row["liberoPhysical"]}；WSim 物理 / 链路：{row["wsimPhysical"]} / {row["wsimFunctional"]}。</p>'+failure+
            ''.join(f'<h3>{label}</h3><video controls preload="metadata" src="attempts/{n}/{env}/videos/dual.mp4"></video>'
                    for env,label in (("libero","原始 LIBERO"),("wsim","WSim · 真实 Ego Harness")))+
            f'<p><a href="attempts/{n}/metrics.json">指标</a> · <a href="attempts/{n}/wsim/completion-check.json">完成证据核对</a></p></article>')
    (out/"index.html").write_text('''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Step 8 初态 0 单例验证</title><style>body{max-width:1100px;margin:auto;padding:28px;font:16px/1.6 system-ui;background:#101821;color:#e4ebf5}a{color:#8bc5ff}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:20px}article{padding:20px;background:#1b2a3a;border:1px solid #354b61;border-radius:12px}video{width:100%;background:#000}h3{font-size:17px}.failure{color:#ffbf95}@media(max-width:600px){main{display:block}article{margin:18px 0}}</style><h1>Step 8 · 初态 0 端到端验证</h1><p>字母汤罐入篮 · 抓取 / 搬运 / 容器放置 · 真实 pi05_libero · 能力 v2</p><p>Prompt: <code>pick up the alphabet soup and place it in the basket</code></p><p>最终 smoke-03：原始参考和 WSim 真实能力链路均通过。WSim 的 Job succeeded、目标 achieved、停止 confirmed；完成判断经独立物理轨迹核对。</p><p>各次试运行与复核单独列出，包含真实失败。视频左外部、右腕部，按仿真时间播放，推理等待冻结不计入。结果不能代表固定批次成功率。</p><p><a href="README.md">报告与复现命令</a> · <a href="attempts.json">全部尝试</a> · <a href="SHA256SUMS">校验清单</a></p><main>'''+''.join(cards)+"</main></html>\n")
    (out/".gitattributes").write_text("# Preserve exact raw experiment outputs.\n*.log binary\n*.patch binary\n")
    sums=[hashlib.sha256(p.read_bytes()).hexdigest()+"  "+str(p.relative_to(out)) for p in sorted(out.rglob("*"))
          if p.is_file() and p.name!="SHA256SUMS" and "raw-evidence" not in p.parts]
    (out/"SHA256SUMS").write_text("\n".join(sums)+"\n")
    print(json.dumps({"attempts":rows,"out":str(out)},ensure_ascii=False),flush=True)


if __name__=="__main__":main()
