from pathlib import Path
import gzip
import hashlib
import json
import re
import subprocess

import mujoco
import numpy as np

from steward.world.chat import load_env_file
from steward.world.sensor_cameras import CameraRenderer
from steward.math3d import matrix_from_quat

repo = Path.cwd().parent
out = repo / "self/design/pi05-intergration/steps/step7-results"
ffmpeg = "/home/huawei/hqk/workspace/pi05-step1/server-mirror-venv/lib/python3.11/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2"
formal = ["N1-06", "N2-03", "C1-03", "F1-02"]
results = {n: json.loads((out / "attempts" / n / "result.json").read_text()) for n in formal}
assert all(r["pass"] and r["cleanup"]["pass"] for r in results.values())
caps = [json.loads((out / "attempts" / n / "capability.json").read_text()) for n in formal[:2]]
assert caps[0]["version"] == caps[1]["version"] == 2 and caps[0]["code"] == caps[1]["code"]
videos = []
for n in formal:
    for role in ["external", "wrist", "dual"]:
        path = out / "attempts" / n / "videos" / (role + ".mp4")
        process = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True)
        assert process.returncode == 0, str(path)
        videos.append(str(path.relative_to(out)))

fixture = json.loads(Path("tests/fixtures/panda-step7/depth-edge.json").read_text())
model = mujoco.MjModel.from_xml_path(str(Path("var/step4/wsim-root/libero-step3/panda.xml").resolve()))
data = mujoco.MjData(model)
for name, value in fixture["encoders"].items():
    data.joint(name).qpos[:] = value["position"]
joint = model.joint(model.body("alphabet_soup_1_main").jntadr[0])
data.qpos[joint.qposadr[0]:joint.qposadr[0] + 7] = fixture["canPose"]
mujoco.mj_forward(model, data)
worker = CameraRenderer()
worker.snapshot_model(model)
depths = {}
try:
    for name, renderer in [("msaa", worker.renderer_for(256, 256)), ("noMsaa", worker.renderer_for(256, 256, samples=0))]:
        option = mujoco.MjvOption()
        option.geomgroup[0] = False
        renderer.update_scene(data, camera="robot0_eye_in_hand", scene_option=option)
        renderer.enable_depth_rendering()
        depths[name] = float(renderer.render()[192, 168])
finally:
    worker.close()
camera = fixture["camera"]
k = camera["K"]
ray = matrix_from_quat(camera["pose"]["q"]) @ np.array([(168-k[0][2])/k[0][0], -(192-k[1][2])/k[1][1], -1])
depths["ray"] = float(mujoco.mj_ray(model, data, np.array(camera["pose"]["p"]), ray, None, 1, -1, np.array([-1], np.int32)))
assert abs(depths["noMsaa"] - depths["ray"]) < .001 and abs(depths["msaa"]-depths["ray"]) > .08
(out / "checks/depth-msaa-diagnostic.json").write_text(json.dumps(depths, indent=2) + "\n")

node = Path("var/dsh-toolchain/node/bin/node").resolve()
node_source = r'''
const fs = require('node:fs'), path = require('node:path'), zlib = require('node:zlib');
const secrets = JSON.parse(fs.readFileSync(0,'utf8')).filter(s => s.length > 8);
const root = process.argv[1], hits = []; let scanned = 0, sessions = 0, guardProof = false;
function walk(dir) {
  for (const entry of fs.readdirSync(dir,{withFileTypes:true})) {
    const p=path.join(dir,entry.name);
    if(entry.isSymbolicLink()) continue;
    if(entry.isDirectory()) {walk(p); continue;}
    let data=fs.readFileSync(p); scanned++;
    if(p.endsWith('.zstd')) { data=zlib.zstdDecompressSync(data); sessions++; }
    if(secrets.some(s=>data.includes(Buffer.from(s)))) hits.push(path.relative(root,p));
    if(p.includes('G1-01') && p.endsWith('.zstd')) {
      const text=data.toString('utf8');
      guardProof=text.includes('step7-missing-receipt') && text.includes('could not be verified');
    }
  }
}
walk(root); process.stdout.write(JSON.stringify({scanned,sessions,hits,guardProof}));
'''
keys = [value for name, value in load_env_file(Path(".env")).items() if "KEY" in name.upper() and value]
scan = subprocess.run([str(node), "-e", node_source, str(out)], input=json.dumps(keys), text=True, capture_output=True)
assert scan.returncode == 0, "Archive session/secret audit failed"
scan = json.loads(scan.stdout)
assert not scan["hits"] and scan["guardProof"], scan

missing = []
for document in [out/"README.md", out/"index.html", repo/"self/design/pi05-intergration/steps/step7.md"]:
    text = document.read_text()
    refs = re.findall(r'\]\(([^)]+)\)', text) + re.findall(r'(?:href|src)="([^"]+)"', text)
    for ref in refs:
        if ref.startswith(("http", "#")): continue
        target = ref.split("#")[0]
        if target and not (document.parent/target).exists(): missing.append(str(document)+": "+ref)
assert not missing, missing
versions = json.loads((out/"code/versions.json").read_text())
assert all(hashlib.sha256((repo/name).read_bytes()).hexdigest()==digest for name,digest in versions["sourceSHA256"].items())
audit = {"formalCasesPassed": True, "sameCapabilityVersionAndCode": True, "videosDecoded": videos,
         "depthDiagnostic": depths, "rawSessionsDecoded": scan["sessions"], "credentialValueHits": 0,
         "guardDenialInRawSession": scan["guardProof"], "missingLinks": missing, "sourceHashesMatch": True}
(out/"checks/final-artifact-audit.json").write_text(json.dumps(audit,indent=2)+"\n")
sums = [hashlib.sha256(p.read_bytes()).hexdigest()+"  "+str(p.relative_to(out))
        for p in sorted(out.rglob("*")) if p.is_file() and p.name!="SHA256SUMS" and "raw-evidence" not in p.parts]
(out/"SHA256SUMS").write_text("\n".join(sums)+"\n")
print(json.dumps({"formalPassed":True,"decodedVideos":len(videos),"sessions":scan["sessions"],"secretHits":0,"links":"pass","sourceHashes":"pass","archiveFiles":len(sums)}))
