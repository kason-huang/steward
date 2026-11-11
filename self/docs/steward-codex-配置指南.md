# Steward 使用 Codex 订阅作为主模型

本文说明如何让 Steward 的机器人和 World Harness 使用 ChatGPT 订阅认证，通过 `openai-codex` 调用 `gpt-6.1-sol`，并验证模型调用、工具执行和任务结束后的控制权释放。配置保存在本地 `steward/var/`，清理该目录或换机器后需要重新配置和授权。

- [准备环境](#准备环境)
- [配置三个 profile](#配置三个-profile)
- [通过设备码登录](#通过设备码登录)
- [重启和验证](#重启和验证)
- [排错和回滚](#排错和回滚)

## 准备环境

以下命令使用 macOS/Linux 的 Bash，从包含 `steward/` 和 `self/` 的仓库根目录执行。Steward 的 Python 环境、DSH 插件和 `ego`、`world`、`world-task` profile 需要已安装；安装入口见 [Steward README](../../steward/README.md)。本文使用的适配器来自 Steward 固定依赖的 `@deepseek-ai/dsh@0.2.1-rc.3`，模型需要存在于其安装的 pi-ai 模型目录中。

```bash
pwd
ls steward/var/dsh/robot-1/profiles/ego/cordis.patch.yml
ls steward/var/dsh/world/profiles/{world,world-task}/cordis.patch.yml
```

这里通过 Harness 自身的 OAuth 流程取得订阅凭据。Codex CLI 的 `codex login status` 显示已登录，不代表 Steward 已完成授权；无需复制 CLI 的令牌，也无需提供 `OPENAI_API_KEY`。

## 配置三个 profile

三个 profile 分别服务于机器人任务、World 网页和 World 单次任务。修改的是每个 profile 的 `cordis.patch.yml`；同目录的 `cordis.yml` 是组合入口，不应替换为完整配置。

| Profile | 配置文件 |
| --- | --- |
| `ego` | `steward/var/dsh/robot-1/profiles/ego/cordis.patch.yml` |
| `world` | `steward/var/dsh/world/profiles/world/cordis.patch.yml` |
| `world-task` | `steward/var/dsh/world/profiles/world-task/cordis.patch.yml` |

### 1. 备份现有配置

备份已存在时保留原件，不要用修改后的配置覆盖它。

```bash
for profile_dir in \
  steward/var/dsh/robot-1/profiles/ego \
  steward/var/dsh/world/profiles/world \
  steward/var/dsh/world/profiles/world-task
do
  if [ ! -e "$profile_dir/cordis.patch.before-codex.yml" ]; then
    cp "$profile_dir/cordis.patch.yml" "$profile_dir/cordis.patch.before-codex.yml"
  fi
done
```

### 2. 合并模型和认证设置

将以下条目合并到三个配置文件的顶层 YAML 数组。文件只有 `[]` 时，用这些条目替换它；文件已有条目时保留它们，对相同 `id` 合并字段，不要重复添加。已有其他 provider 时，也保留其配置。

```yaml
- id: agent-default-model
  config:
    provider: openai-codex
    model: gpt-6.1-sol
    reasoningEffort: xhigh
- id: llm-pi-ai
  config:
    providers:
      openai-codex:
        transport: sse
- id: credentials
  disabled: false
  config:
    path: /ABSOLUTE/REPO/steward/var/dsh/.codex-credentials.yaml
- id: authorization
  disabled: false
```

将 `/ABSOLUTE/REPO` 换成当前仓库的绝对路径。三个 profile 的 `credentials.config.path` 必须指向同一个文件，才能共用一次登录，并让刷新后的凭据由同一个存储管理。不要将访问令牌或刷新令牌写入 profile 配置。

`transport: sse` 用于单次任务结束后的正常退出。在本机验证中，默认传输方式下模型已返回、会话已记录 `turn/end`，但 Node 进程继续运行，控制台一直显示忙碌；明确使用 SSE 后，单次进程正常退出，控制台恢复空闲。只关闭凭据文件监听没有解决该现象。

### 3. 检查组合后的配置

`--dump-config` 只解析组合配置，不发起模型请求。分别检查输出中的 provider、model、SSE 设置和凭据路径；成功解析不等于认证或实际调用已经通过。

```bash
DSH_HOME="$PWD/steward/var/dsh/robot-1" \
  steward/dsh/node_modules/.bin/dsh --profile ego --dump-config

DSH_HOME="$PWD/steward/var/dsh/world" \
  steward/dsh/node_modules/.bin/dsh --profile world --dump-config

DSH_HOME="$PWD/steward/var/dsh/world" \
  steward/dsh/node_modules/.bin/dsh --profile world-task --dump-config
```

## 通过设备码登录

登录辅助插件只调用已安装的 Harness OAuth 流程，不接管机器人。下面的两个文件可由本文重新创建，无需依赖已有的 `var/` 文件。

### 1. 创建一次性登录入口

保存下面的代码为 `steward/var/dsh/codex-login.mjs`：

```javascript
/** Local, one-shot caller of the installed Harness OAuth flow. */
export function apply(ctx) {
  ctx.inject(['authorization', 'credentials'], child => {
    const controller = new AbortController();
    child.effect(() => () => controller.abort());
    void (async () => {
      for (let i = 0; !child.authorization.describe('llm-pi-ai/openai-codex'); i++) {
        if (i >= 100) throw new Error('Codex OAuth flow did not become available');
        await new Promise(resolve => setTimeout(resolve, 100));
        controller.signal.throwIfAborted();
      }
      return child.authorization.begin({
        key: 'llm-pi-ai/openai-codex',
        method: 'oauth',
        signal: controller.signal,
        interaction: {
          notify: notice => console.log('CODEX_LOGIN', JSON.stringify(notice)),
          prompt: async prompt => {
            if (prompt.kind === 'select'
              && prompt.options.some(option => option.id === 'device_code')) {
              return 'device_code';
            }
            throw new Error('Unexpected OAuth prompt: ' + prompt.message);
          },
        },
      });
    })().then(
      result => console.log('CODEX_LOGIN_RESULT', result.status),
      error => console.error('CODEX_LOGIN_FAILED', error.message),
    );
  });
}
```

保存下面的配置为 `steward/var/dsh/codex-login.patch.yml`。相对插件路径以这个 overlay 文件的位置解析，因此两个文件要放在同一目录。

```yaml
- id: headless-startup
  disabled: true
- id: headless-runner
  disabled: true
- insert:
    - id: steward-codex-login
      name: './codex-login.mjs'
```

### 2. 启动并完成授权

通过 `dsh` profile 启动登录入口，不要直接用 Node 运行插件文件。

```bash
DSH_HOME="$PWD/steward/var/dsh/robot-1" \
  steward/dsh/node_modules/.bin/dsh --profile ego \
  --patch "$PWD/steward/var/dsh/codex-login.patch.yml"
```

终端会输出 `CODEX_LOGIN`，其中包含设备登录 URL 和本次生成的 `code`。打开输出的 URL，用有 Codex 使用权限的 ChatGPT 账号登录，输入该设备码并完成授权；不要复用旧设备码。等待终端输出 `CODEX_LOGIN_RESULT authorized` 后，再按 Ctrl-C 停止一次性登录入口。

授权结果由 Harness 写入配置的 `.codex-credentials.yaml`，记录名为 `llm-pi-ai/openai-codex`，本机验证的文件权限为 `0600`。该文件包含访问令牌和刷新令牌，不要提交、截图或粘贴其内容；常规模型请求由适配器管理凭据刷新。

## 重启和验证

Steward 的控制台聊天每轮都会启动一个 `dsh --profile ... --json` 子进程，因此新的轮次会读取 profile 配置。已有的 Harness 进程需要结束后重启。重启 Steward 世界服务会重新加载场景，应先确认没有正在执行的任务，并记录当前场景、感知阶段和端口。

### 1. 记录当前状态并重启服务

```bash
curl -fsS http://127.0.0.1:7400/wca/status
curl -fsS http://127.0.0.1:7400/wca/phase
curl -fsS http://127.0.0.1:7400/console/chat
```

在原启动终端按 Ctrl-C 停止服务；后台服务则先确认对应 PID，再发送 SIGTERM。下面是 `phase0-chores / phase0` 的启动示例，其他任务按记录替换场景、阶段和端口。`--no-dispatch-task` 让服务启动后等待手动验证，不立即执行完整场景任务。

```bash
cd steward
.venv/bin/steward up \
  --scenario phase0-chores --phase phase0 \
  --misnames-config '' \
  --wca-port 7400 --robot-port 7410 --api-port 7420 \
  --no-dispatch-task
```

看到控制台地址和 Ego API 地址后，打开 `http://127.0.0.1:7400/console`。后面的命令在另一个终端执行，并回到仓库根目录。

### 2. 验证纯模型调用和正常退出

```bash
./steward/scripts/run-ego.sh \
  'Do not call any tools. Reply only STEWARD_CODEX_OK.'
```

成功标准是输出 `STEWARD_CODEX_OK`，且命令自行退出。此步骤不验证机器人技能。

### 3. 验证控制台工具调用

通过机器人聊天入口提交一个只读任务，验证控制台、模型、程序工具和 Ego API 的完整调用。

```bash
curl -fsS -X POST http://127.0.0.1:7400/console/chat \
  -H 'Content-Type: application/json' \
  -d '{"agent":"robot","message":"请只调用一次 ego_state，可通过 run_code 调用，简要汇报已知物体数量，最后输出 CODEX_TOOL_CHECK_OK。不要移动、抓取或修改场景。"}'

curl -fsS http://127.0.0.1:7400/console/chat
```

POST 返回 `accepted: true` 只表示任务被接收。随后在控制台或再次 GET 的结果中确认：`run_code` 调用了 `tools.ego_state({})` 且状态为 `completed`，最终回复包含 `CODEX_TOOL_CHECK_OK`，机器人入口恢复 `busy: false`，`control` 和 `wait` 都为 `null`。物体数量由当前场景决定，不应硬编码为某个数值。

### 4. 核实实际使用的模型

从 `/console/chat` 的 `agents.robot.sessionId` 取得本次会话 ID，在 `steward/var/dsh/robot-1/sessions/` 下找到对应目录。压缩会话文件可使用 `zstd -dc <实际文件路径>` 查看；在 `assistant/message` 事件的 `data.message.source` 中确认 `provider` 为 `openai-codex`、`model` 为 `gpt-6.1-sol`，并确认 `turn/end` 的 reason 为 `completed`。不要仅根据模型的自然语言自述判断调用来源。

即使 `DEEPSEEK_API_KEY` 无效，Codex 路由仍应通过上述验证。Steward 的启动脚本和控制台会从 `steward/.env` 加载变量，但 Codex 认证使用自己的 OAuth 记录。检查实际会话中的模型来源，才能确认任务没有走 DeepSeek；不要把某个 Key 仍存在误判为它被使用。

本机已验证纯模型调用、控制台 `ego_state` 工具调用、回合完成和机器人控制权释放。搬运、拼图等完整任务需要另行验收。

## 排错和回滚

按错误发生的位置检查配置、授权和任务结束状态，不要通过重复提交完整机器人任务来排查认证问题。

| 现象 | 检查与处理 |
| --- | --- |
| `Provider is not configured: openai-codex` | 检查 OAuth 是否输出 `authorized`、凭据路径是否一致，以及 `credentials` 是否启用；本机未登录时出现过此错误。 |
| 找不到 Codex OAuth flow | 检查 `authorization`、`credentials` 和 `llm-pi-ai` 是否加载；登录辅助插件会等待最多约 10 秒供 flow 注册。 |
| `UNKNOWN_MODEL` 或模型目录错误 | 检查当前安装的适配器模型目录；本文的模型名是可复用示例，不保证所有旧版本都包含它。 |
| 已收到回答，但控制台一直忙碌 | 检查 Codex route 是否设置 `transport: sse`，停止旧聊天轮次后重试，并确认 `busy` 恢复为 `false`。 |
| 配置输出正确，但调用仍失败 | `--dump-config` 不验证 OAuth、账号权限、额度或网络；以实际调用错误为准处理。 |

停止仍在运行的验证轮次可以使用以下入口；该操作会取消指定机器人聊天任务。

```bash
curl -fsS -X POST http://127.0.0.1:7400/console/chat/stop \
  -H 'Content-Type: application/json' -d '{"agent":"robot"}'
```

恢复修改前的配置时，将三个备份复制回原文件，再重启正在运行的 Harness。恢复 DeepSeek 配置后需要可用的 DeepSeek 凭据；故意改错的 Key 不会因回滚而恢复。

```bash
for profile_dir in \
  steward/var/dsh/robot-1/profiles/ego \
  steward/var/dsh/world/profiles/world \
  steward/var/dsh/world/profiles/world-task
do
  cp "$profile_dir/cordis.patch.before-codex.yml" "$profile_dir/cordis.patch.yml"
done
```

回滚 profile 不会自动删除 OAuth 凭据。操作前确认备份存在且包含期望的原配置；清理本地凭据文件前，确认没有其他 profile 继续共用它。
