---
name: ai-coding-harness
version: 2.2.0
description: AI Coding Agent 工程化约束框架 + Codex Hook 强制层。用于长程/生产任务、文件修改审计、断点恢复、"让 AI 不遗忘并严格执行规范"。安装时自动注册 Codex hooks 做运行时硬拦截。
entry: install.sh
tags: [coding-agent, harness, compliance, traceability, safety, codex-hooks]
language: zh-CN
author: harness-team
---

# AI Coding Harness

对 AI Coding Agent 的工程化约束框架；v2.1 附带 Codex Hook 强制层（运行时硬拦截，非提示词软约束）；v2.2 新增疑似密钥关键词检测（命中后需用户二次确认）。

## When to Use
触发条件（任一）：
- 用户要求工程化约束 / 保证 agent 质量 / 断点恢复 / 合规审查 / 让 AI 不遗忘规范 / 强制执行规范
- 提到 Harness、harness、AI coding 安全
- 长程、多步骤、生产相关任务；需要审计与回溯文件修改

## Golden Rules（12 条，最高优先级）
1. 行动前先规划：L1+ 操作前生成 plan.md 且用户签字 APPROVED
2. 改动前先快照：每 Step 建 checkpoint
3. 写文件先合规：经 compliance_check.py 审查
4. 结论必有证据：禁"我猜测/可能是/应该是"
5. 模糊必咨询：命中 8 类模糊点暂停询问
6. 错误先排序：全量扫描/打分/关联分析
7. 超阈必回退：3 失败 / 30min / 10 文件 / 500 LOC / 5 次介入
8. 终止必恢复：用户 ABORT 后停手 + 回 baseline
9. 会话必续接：新会话先读 state.json + worklog.md
10. 日志必完整：每 Step 追加 worklog 条目
11. 用户反馈必固化：原话存 user_feedback.jsonl + 复述确认后才执行
12. 不准直接改文件：只用 harness_write.py / harness_delete.py / harness_move.py

风险分级：L0 只读=自动 | L1 可逆=自动+日志 | L2 中风险=暂停确认 | L3 不可逆=暂停+二次确认；无法判定按高一级。

## 合规审查 / Compliance（v2.2）
所有写入经 `harness_write.py` → `compliance_check.py` 审查，分三类：
- **确定性密钥指纹**（`sk-…` / `AKIA…` / `-----BEGIN … PRIVATE KEY-----` / `gh*_…`）：L3 硬阻断，需用户 `harness_grant_override.py --rule compliance` 令牌方可写入。
- **疑似密钥关键词**（`apikey` / `api_key` / `secret` / `access_token` / `password` / `passwd` / `credential` / `private_key` 等，含 camelCase/snake_case）：**不硬阻断**，返回 `confirmation_required` + `requires_confirmation`，提示用户二次确认。用户运行
  `python .harness/scripts/harness_grant_override.py --target <file> --rule secret_keyword --reason "确认非真实密钥"`
  生成一次性令牌，再用 `--confirm-secret-token <token>` 写入。LLM 无法自行确认或绕过。
- **其他**（越界路径 / 语法错误 / 不可逆模式）：按 L2/L3 处理，需对应 override 令牌。
`harness-managed` / `.harness/` / 常见配置文件豁免**软扫描**以避免自锁；确定性指纹仍全量拦截。
git `pre-commit` 使用 `compliance_check.py --hard-only`，因此已确认的关键词文件可正常提交。

## Quick Start
```bash
./install.sh <project> --objective "..." --scopes src/   # 装 .harness 模板 + 自动注册 Codex hooks
./install.sh <project> --copy-only                       # 只铺模板+hooks，不初始化任务
./codex/install_codex_hooks.sh <project>                 # 只装 hook 层
./install.sh <project> --no-codex-hooks                  # 不装 hook
```
未初始化时：`python .harness/scripts/harness_init.py --objective "..." --scopes src/`
Agent 启动时读 `docs/harness-quick-reference.md`。

## Codex Hook 强制层（v2.1）
动机：SKILL.md / system prompt 是软约束（长对话、compaction、新会话会遗忘）；hooks 是硬约束。

机制（对 Codex CLI 0.158 实测校准）：
- 开关 `[features] hooks = true`；配置 `.codex/hooks.json`（或 config.toml 的 `[hooks]` 表）
- 事件名 PascalCase：SessionStart / UserPromptSubmit / PreToolUse / PostToolUse / Stop / SubagentStop / PreCompact / SessionEnd / PermissionRequest
- handler `{"type":"command","command":"...","timeout":N}`；事件 JSON 走 stdin，输出 JSON 走 stdout
- 勿用网上 `on_tool_call` / `action` 写法——与实际 Codex 不符

5 个 hook 及强制规则：
- SessionStart → 注入 12 法则 + 风险矩阵 + state 摘要（防遗忘）
- UserPromptSubmit → 每轮精简提醒 + 用户原话归档 user_feedback.jsonl（规则 9/10/11）
- PreToolUse → deny 直写项目文件（apply_patch/Edit/Write、shell `>`/`>>`/`tee`/`sed -i`）、`git --no-verify`、`rm -rf` 根目录、`git push --force`（规则 3/7/12）
- PostToolUse → 跑 harness_verify_writes.py 并把失败回推上下文（规则 3/10）
- Stop → write_manifest 比 worklog 新则 block（规则 9/10）

放行：harness_write/delete/move.py、`.harness/**`、`.git/**`、`/tmp/**`、项目外路径、只读命令。
fail-open：hook 内部异常放行并记 `.harness/hook_errors.log`，绝不锁死会话；无 `.harness/` 的仓库全部 no-op。
信任：Codex 只对受信任项目加载 `.codex/`。首次运行选 "Trust this workspace"，或 `~/.codex/config.toml` 加 `[projects."<abs>"] trust_level="trusted"`，或用 `--scope global`。
逃生阀（仅用户可设，Agent 无法注入）：`HARNESS_HOOKS_DISABLE=1` 关闭全部 hook；`HARNESS_ALLOW_FORCE_PUSH=1` 允许 force push。

验证：
```bash
codex features list | grep -E '^hooks'            # 期望 hooks stable true
python3 .harness/codex_hooks/session_start.py </dev/null   # 输出含 additionalContext 的 JSON
```

## Key Paths
- `plan.md` 规划(含 Revision History)；`state.json` 状态机
- `worklog.md` / `worklog.jsonl` 日志；`user_feedback.jsonl` 反馈原话归档
- `.harness/write_manifest.jsonl` 修改审计；`.harness/hook_errors.log` hook 异常
- `.harness/scripts/`：harness_init / harness_state_inspector / harness_write / harness_delete / harness_move / harness_verify_writes / harness_doctor / compliance_check
- `.codex/hooks.json` hook 注册；`codex/hooks/*.py` hook 脚本
- 文档：`docs/harness-quick-reference.md`（精）、`harness-prompt-engineering.md`（全）、`harness-whitepaper.md`（深）、`codex/README.md`（hook 层）

## Post-Install Verify
```bash
python .harness/scripts/harness_doctor.py
python .harness/scripts/harness_state_inspector.py
echo "print('hello')" | python .harness/scripts/harness_write.py src/hello.py
```
