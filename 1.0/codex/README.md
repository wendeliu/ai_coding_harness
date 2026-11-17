# Codex Hook 强制层 / Codex Hook Enforcement Layer

> 用 Codex 原生 **Hooks** 机制，把 Harness 规范从“写在提示词里、可能被忘记”
> 升级为“由运行时机械拦截、无法绕过”。

## 1. 为什么需要 Hook

`SKILL.md` / system prompt 里的规则是**软约束**：长对话、compaction、子代理、
新会话都可能让模型“忘记”。Codex Hook 是**硬约束**：它在生命周期事件触发时运行
外部命令，并能 **注入上下文 / 阻止工具调用 / 阻止结束回合**。

- **不遗忘**：`SessionStart` + `UserPromptSubmit` 每次把规范重新注入上下文。
- **严格执行**：`PreToolUse` 直接 deny 绕过 harness 的写操作与危险命令。
- **闭环校验**：`PostToolUse` 跑写入校验；`Stop` 检查日志完整性。

## 2. 机制（已对 Codex CLI 0.158 实测验证）

Codex 的 Hook 与 Claude Code 兼容：

- 开关：`config.toml` 中 `[features] hooks = true`（`codex features list` 显示 `hooks stable`）。
- 配置：`.codex/hooks.json`（Claude/Codex 兼容格式），或 `config.toml` 的 `[hooks]` 表。
- 事件（枚举名，PascalCase）：
  `PreToolUse` `PermissionRequest` `PostToolUse` `PreCompact` `PostCompact`
  `SessionStart` `SessionEnd` `UserPromptSubmit` `SubagentStart` `SubagentStop` `Stop` `Interrupt`
- handler：`{"type":"command","command":"...","timeout":N}`（也支持 `mcp_tool`）。
- 回调协议：事件 JSON 经 **stdin** 传入；hook 用 **stdout** 输出 JSON
  （`hookSpecificOutput.additionalContext` / `permissionDecision=deny` / `decision=block`）。

> ⚠️ 注意：网上部分教程（如 runoob 的 `on_tool_call` / `action` / `hooks.json` 顶层数组）
> 与实际 Codex 二进制不符。本目录采用**实测验证**的格式。

## 3. 本目录提供的 5 个 Hook

| 事件 | 脚本 | 作用 | 强制规则 |
|---|---|---|---|
| `SessionStart` | `hooks/session_start.py` | 注入 12 条黄金法则 + 风险矩阵 + 当前状态 | 全部（防遗忘） |
| `UserPromptSubmit` | `hooks/user_prompt_submit.py` | 每轮提醒 + 用户原话归档 | 9/10/11 |
| `PreToolUse` | `hooks/pre_tool_use.py` | **deny** 直写项目文件 / `git --no-verify` / `rm -rf` / force-push | 3/7/12 |
| `PostToolUse` | `hooks/post_tool_use.py` | 跑 `harness_verify_writes.py` 并回推校验结果 | 3/10 |
| `Stop` | `hooks/stop.py` | 提交/收尾前校验 worklog 与写入一致 | 9/10 |

放行时 hook **静默**（exit 0，无输出）；命中时输出结构化 JSON。

## 4. 安装

```bash
# 推荐：装到目标项目（随项目走）
./install_codex_hooks.sh /path/to/project

# 装到用户级（对所有仓库生效；对无 .harness 的仓库自动 no-op）
./install_codex_hooks.sh /path/to/project --scope global

# 预演，不落盘
./install_codex_hooks.sh /path/to/project --dry-run
```

安装器会：
1. 复制 hook 脚本到 `<project>/.harness/codex_hooks/`；
2. 生成 `<project>/.codex/hooks.json`（写入绝对路径，并深合并已有 hook）；
3. 在 `<project>/.codex/config.toml` 打开 `[features] hooks = true`（幂等合并）。

验证：

```bash
codex features list | grep -E '^hooks'          # 应显示 enabled
python3 .harness/codex_hooks/session_start.py </dev/null   # 输出含 additionalContext 的 JSON
```

## 4.5 信任要求（重要）

Codex 只对 **受信任项目** 加载项目级 `.codex/` 配置。安装到项目后，请任选其一：

- 首次在该目录运行 `codex` 时选择 **Trust this workspace**；
- 在 `~/.codex/config.toml` 写入：
  ```toml
  [projects."/abs/path/to/project"]
  trust_level = "trusted"
  ```
- 或改用全局安装 `--scope global`（hook 脚本对无 `.harness/` 的仓库自动 no-op）。

## 5. 逃生阀（仅用户可设）

Hook 进程读取的是 **Codex 启动时的环境变量**，Agent 无法在自己的命令里注入：

```bash
export HARNESS_HOOKS_DISABLE=1       # 临时关闭全部 harness hook
export HARNESS_ALLOW_FORCE_PUSH=1    # 允许 git push --force
```

## 6. 健壮性

- 任何脚本内部异常都 **fail-open**（放行）并写入 `.harness/hook_errors.log`，
  绝不把会话锁死。
- 无 `.harness/` 的仓库：所有 hook 自动 no-op（因此可安全全局安装）。
- `.harness/` 与 `.git/` 路径豁免直写拦截，避免自锁死。
