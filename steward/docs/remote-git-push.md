# 4090-2 通过本机代理推送 GitHub

2026-11-12 已实测：4090-2 直连 GitHub 超时，本机通过 HTTP 代理 127.0.0.1:7892 可以访问。SSH 反向转发把该代理提供给服务器的回环端口 17892；服务器使用临时 Git 凭据 helper，从 `/home/huawei/hqk/workspace/.env` 读取 GitHub token，以 kason-huang 身份推送。最终远端提交核对为 b8db653ec8。HTTP/1.1 单独重试未解决本次连接问题。

## 1. 本机终端 A：开启代理转发

本机代理软件保持运行，HTTP 代理端口为 7892。先检查代理能访问 GitHub，再打开 SSH 隧道：

```sh
curl -sS -o /dev/null --connect-timeout 10 --max-time 20 \
  --proxy http://127.0.0.1:7892 \
  -w 'GitHub HTTP status: %{http_code}\n' https://github.com

ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -R 127.0.0.1:17892:127.0.0.1:7892 rtx-4090-2
```

第一个命令应返回 200。第二个命令运行时没有输出，保持终端 A 打开。这里需要 HTTP 代理端口；如果本机代理软件改了端口，将两处 7892 一起修改。

`-R` 的方向为：4090-2 的 127.0.0.1:17892 → SSH 隧道 → 本机 127.0.0.1:7892 → GitHub。与访问 Steward 时使用的 `-L 17400:127.0.0.1:7400` 是两条独立转发，可以同时运行。

## 2. 本机终端 B：登录服务器并检查

```sh
ssh rtx-4090-2
cd /home/huawei/hqk/workspace/steward

curl -sS -o /dev/null --connect-timeout 10 --max-time 20 \
  --proxy http://127.0.0.1:17892 \
  -w 'GitHub HTTP status: %{http_code}\n' https://github.com

git branch --show-current
git status --short
git log -1 --oneline
git remote -v
```

代理检查应返回 200，当前分支应为 feat-pi05-intergration，origin 为 `https://github.com/kason-huang/steward.git`。先完成本次改动的提交，再执行推送；未跟踪的实验数据不会被此脚本加入提交。

## 3. 服务器：试运行和推送

GitHub token 保存在 `/home/huawei/hqk/workspace/.env`。支持 `GITHUB_TOKEN=...`、带说明的 token 行或单独 token；文件中必须恰有一个不同的 GitHub token。脚本不通过 shell 执行该文件，也不会把其中的 DeepSeek key 当成 GitHub token。

```sh
# 只检查连接、认证和推送条件，不更新远端分支。
python3 steward/scripts/push_via_local_proxy.py \
  --branch feat-pi05-intergration --dry-run

# 正式普通推送，随后核对远端提交号。
python3 steward/scripts/push_via_local_proxy.py \
  --branch feat-pi05-intergration
```

成功后输出 `Verified origin/feat-pi05-intergration: <提交号>`。脚本只推送指定分支，沿用 Git 的普通推送与 hook 流程；远端有未合入的新提交时会拒绝推送，不自动覆盖远端历史。

脚本的临时 credential helper 只向 Git 的凭据管道返回用户名和 token，不保存凭据。helper 文件仅包含读取逻辑，退出时自动删除；代理和 helper 都由本次命令的 `git -c` 指定，不写入仓库或全局 Git 配置，TLS 校验保留。

其他参数可按需覆盖：

```sh
python3 steward/scripts/push_via_local_proxy.py \
  --branch feat-pi05-intergration \
  --proxy http://127.0.0.1:17892 \
  --token-file /home/huawei/hqk/workspace/.env \
  --username kason-huang
```

## 4. 核对并关闭转发

服务器上可再次独立核对：

```sh
git rev-parse HEAD
git -c http.proxy=http://127.0.0.1:17892 \
  ls-remote origin refs/heads/feat-pi05-intergration
```

两个提交号应一致。完成后，在本机终端 A 按 Ctrl+C 关闭反向转发。下次推送重新开启终端 A 的隧道；无需重复修改 Git 配置。

## 常见结果

| 现象 | 处理 |
| --- | --- |
| GnuTLS recv error (-110) 或连接超时 | 先验证本机 7892，再验证服务器 17892；本次实测直连失败而代理通畅 |
| remote port forwarding failed | 17892 被占用或转发被服务器拒绝；检查已有隧道，或同时更改 -R 左侧端口与脚本 --proxy 端口 |
| Failed to connect to 127.0.0.1:17892 | 本机终端 A 的 SSH 隧道未运行 |
| 本机代理无法连接 | 检查代理软件运行状态及 HTTP 代理实际端口 |
| could not read Username | 使用上述脚本进行临时认证；只加 http.proxy 不会配置 GitHub 登录 |
| 401/403 或 Authentication failed | 核对 token 有效期、仓库写权限及账号授权；用户名使用 kason-huang |
| token file must contain exactly one distinct GitHub token | 检查凭据文件位置和 token 数量；不要把 token 粘贴到聊天或日志 |
| non-fast-forward / fetch first | 先拉取并处理远端新提交，再普通推送 |
| hook 检查失败 | 修复检查报告的问题后再推送 |

## 本次验证

最初远端为 2fa2608747，通过此网络与临时认证方式推送三个已有提交后，远端更新到 b8db653ec8。可复用脚本另以真实凭据执行 --dry-run 验证，不发布本说明新增的提交。

针对性测试为 `cd steward && .venv/bin/python -m pytest tests/test_push_via_local_proxy.py -q`，5 passed。覆盖凭据解析、GitHub 域名限制、无凭据持久化、dry-run、推送后核对和失败时临时 helper 清理。真实 dry-run 返回 Everything up-to-date / Dry-run completed，远端分支未更新。
