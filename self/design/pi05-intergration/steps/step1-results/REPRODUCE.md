# π0.5 Step 1 复现手册

本次验证使用 OpenPI 官方 `pi05_libero` 权重，在原始 LIBERO 环境运行。正式批次固定任务 ID 0、任务排序 0、种子 7、初态 0–4。每回合预热 10 步，最多执行 280 个策略控制步，墙钟上限 600 秒。历史结果与证据见 [验收报告](README.md)。

## 在 4090-2 上直接复跑

在本机终端连接：

```bash
ssh rtx-4090-2
```

以下命令在远端执行。两套 Python 环境、权重和脚本已就绪。运行期间保持 SSH 会话打开。

```bash
cd /home/huawei/hqk/workspace/pi05-step1
nvidia-smi
ss -ltn '( sport = :18005 )'
sha256sum -c evidence/frozen-batch.sha256
sha256sum -c evidence/checkpoint.sha256 evidence/tokenizer.sha256

set -o pipefail
log="evidence/reproduce-$(date -u +%Y%m%dT%H%M%SZ).log"
bash run-validation.sh 2>&1 | tee "$log"
```

`18005` 是策略服务的本机端口，启动前应为空。总入口按顺序执行 EGL 预检、10 个官方任务各 1 回合、字母汤罐资格回合。资格回合通过后，脚本会重启策略服务，按冻结配置运行正式批次，结束时关闭策略进程。无需启动 Steward 或建立 SSH 转发。

运行结束后选择本次批次目录，查看成功率并审计原始记录：

```bash
ls -1dt runs/*-batch
batch="$(ls -1dt runs/*-batch | sed -n '1p')"
python3 -m json.tool "$batch/summary.json"
bash client.sh "$PWD/audit-attempts.py" "$PWD/$batch"
```

若同时有人运行评测，请把 `batch` 显式设成本次目录。每次运行在 `runs/<UTC-ID>/` 保留预检、策略日志、试跑录像及资格回合，正式批次在 `runs/<UTC-ID>-batch/`。历史批次目录不会被覆盖。当前验收的运行 ID 为 `20261110T021656Z`。

## 只跑一个目标回合

在远端终端 A 启动策略服务：

```bash
cd /home/huawei/hqk/workspace/pi05-step1
bash server.sh
```

在远端终端 B 发起目标回合：

```bash
cd /home/huawei/hqk/workspace/pi05-step1
curl -fsS http://127.0.0.1:18005/healthz
run="manual-$(date -u +%Y%m%dT%H%M%SZ)"
bash client.sh "$PWD/evaluate.py" \
  --task pick_up_the_alphabet_soup_and_place_it_in_the_basket \
  --task-id 0 --task-order-index 0 --init-index 0 --seed 7 \
  --timeout 600 --attempt init-000 --output "$PWD/runs/$run"
python3 -m json.tool "runs/$run/init-000/result.json"
```

同一目录下的 `episode.mp4` 是录像。检查 `outcome=success`、`success_signal=true` 和 `termination=environment_done`。结束后在终端 A 按 Ctrl+C。只跑这一回合适合调试，正式批次使用上一节的一键入口。

## 在新机器上搭建

下面按已验证的 Linux x86_64、NVIDIA GPU、EGL 和双 Python 环境路线搭建。新机器需已有可用的 NVIDIA 驱动、EGL、Git、curl、uv、Python 3 及构建工具。策略使用 Python 3.11，仿真使用 Python 3.8。模型本身约 12.44 GB，另需为虚拟环境、下载缓存和录像留空间。

### 1. 传入脚本和校验清单

在本机进入此手册所在的 `step1-evidence` 目录。把 `NEW_GPU` 改为新机器的 SSH 别名：

```bash
host=NEW_GPU
ssh "$host" 'mkdir -p "$HOME/hqk/workspace/pi05-step1/evidence"'
tar -cf - server.sh client.sh serve.py evaluate.py preflight.py run-validation.sh \
  run-batch.sh audit-attempts.py setup-server-mirror.sh download-v2.py \
  evidence/frozen-batch.json evidence/frozen-batch.sha256 \
  evidence/checkpoint.sha256 evidence/tokenizer.sha256 \
  runs/20261110T021656Z/client-freeze.txt |
  ssh "$host" 'cd "$HOME/hqk/workspace/pi05-step1" && tar -xf -'
ssh "$host"
```

### 2. 固定源码并建立策略环境

以下命令都在新机器执行。建议在同一个 Bash 会话中按顺序执行。

```bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
root="$HOME/hqk/workspace"
data="$root/pi05-step1"
export GIT_LFS_SKIP_SMUDGE=1

nvidia-smi
git clone --no-checkout https://github.com/Physical-Intelligence/openpi.git "$root/openpi-step1"
cd "$root/openpi-step1"
git checkout --detach 215abfb217dbac7d5f1273282331b9b1866c0479
git submodule update --init --recursive
test "$(git -C third_party/libero rev-parse HEAD)" = f78abd68ee283de9f9be3c8f7e2a9ad60246e95c
{ git rev-parse HEAD; git submodule status --recursive; } | tee "$data/evidence/revisions.txt"

uv python install 3.11.18 3.8.20 3.12.16
cd "$data"
bash setup-server-mirror.sh 2>&1 | tee evidence/setup-server-mirror.log
```

策略安装脚本使用中科大 PyPI 镜像，保留 OpenPI 锁文件中的版本和包哈希。确认安装日志含 `CudaDevice`。

### 3. 安装仿真依赖和写入 LIBERO 配置

从验收时的实际依赖清单安装。原清单中两条本地 wheel 路径替换成同版本的 PyTorch 和 NumPy 引用，其余版本保持固定。

```bash
cd "$data"
uv venv --python 3.8.20 client-venv
sed -e 's|^numpy @ file:.*|numpy==1.22.4|' \
    -e 's|^torch @ file:.*|torch==1.11.0+cu113|' \
    runs/20261110T021656Z/client-freeze.txt > evidence/client-reproduce-requirements.txt
uv pip sync --python "$data/client-venv/bin/python" \
  --default-index https://mirrors.ustc.edu.cn/pypi/simple \
  --extra-index-url https://download.pytorch.org/whl/cu113 \
  --index-strategy unsafe-best-match evidence/client-reproduce-requirements.txt \
  2>&1 | tee evidence/setup-client.log
uv pip freeze --python "$data/client-venv/bin/python" > evidence/client-freeze.txt

libero="$root/openpi-step1/third_party/libero/libero/libero"
mkdir -p libero-config
cat > libero-config/config.yaml <<YAML
benchmark_root: $libero
bddl_files: $libero/bddl_files
init_states: $libero/init_files
datasets: $root/openpi-step1/third_party/libero/libero/datasets
assets: $libero/assets
YAML
```

### 4. 下载并验证模型

保留历史 checkpoint 清单供比对。下载器对 GCS 对象做 CRC32C 校验，并可从已有分片续传。

```bash
cd "$data"
cp evidence/checkpoint.sha256 evidence/checkpoint.baseline.sha256
uv venv --python 3.12.16 checksum-venv
uv pip install --python checksum-venv/bin/python google-crc32c==1.9.0
checksum-venv/bin/python -u download-v2.py 2>&1 | tee evidence/download.log
diff -u evidence/checkpoint.baseline.sha256 evidence/checkpoint.sha256

mkdir -p openpi-cache/big_vision
curl -fLsS --retry 3 \
  https://storage.googleapis.com/big_vision/paligemma_tokenizer.model \
  -o openpi-cache/big_vision/paligemma_tokenizer.model
sha256sum -c evidence/checkpoint.baseline.sha256 evidence/tokenizer.sha256
```

### 5. 预检并跑正式验证

```bash
cd "$data"
bash client.sh "$PWD/preflight.py" "$PWD/evidence/preflight-$(date -u +%Y%m%dT%H%M%SZ)"
bash run-validation.sh 2>&1 | tee "evidence/reproduce-$(date -u +%Y%m%dT%H%M%SZ).log"
```

预检应生成两路相机样图并解析出任务 ID 0。正式验证完成后，按第一节查看并审计本次批次结果。复跑可能受 GPU 和软件环境差异影响，应以新批次的实际结果为准。

## 出错时看哪里

| 阶段 | 日志或检查 |
| --- | --- |
| 安装或下载 | `evidence/setup-server-mirror.log`、`evidence/setup-client.log`、`evidence/download.log` |
| EGL 或资产路径 | `runs/<UTC-ID>/preflight.log`，核对 `libero-config/config.yaml`、NVIDIA 驱动与 GPU 访问 |
| 模型加载或端口 | `runs/<UTC-ID>/smoke-policy.log`、`batch-policy.log`，用 `ss -ltn '( sport = :18005 )'` 查监听 |
| 单回合失败 | 对应目录的 `result.json`、`exception.txt`（如有）、`trajectory.jsonl` 与录像 |
| 批次统计 | `runs/<UTC-ID>-batch/summary.json` 和 `audit-attempts.py` 的输出 |
