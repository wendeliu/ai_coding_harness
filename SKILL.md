---
name: ai-coding-harness
version: 3.1.0
description: 面向 auto 模式的 AI 编码硬约束框架：shell 白名单、项目文件唯一写入通道 harness publish、防篡改锁单、口令授权、plan/worklog 溯源与 dashboard。约束只在代码闸门，无提示词软约束。
entry: install.sh
tags: [coding-agent, harness, enforcement, audit, traceability, codex-hooks]
language: zh-CN
---

# AI Coding Harness v3

约束只存在于代码闸门。AI 必须遵守以下规则，无例外。

## 硬约束

1. **shell 白名单**：只读命令自动放行；其他命令（pytest/make/python/git 写操作等）一律 deny，需用户在终端批准。不要尝试绕过。
2. **写文件唯一通道 `harness publish`**：直接 Write/Edit/apply_patch 项目文件一律 deny。在 `.harness-tmp/` 起草后发布：`python .harness/scripts/harness publish <dest> --from .harness-tmp/<src>`。
3. **不得碰 `.harness/`**：写工具对 `.harness/` 一律 deny；不得关闭/修改 hook（篡改即全局锁定）。
4. **授权（approve/confirm/adopt/abort）需用户在自己终端输口令**，AI 无法自行获取，不要尝试。
5. **分支保护**：保护分支（默认 master/main，`harness branch` 查看）禁止直接提交（pre-commit 拒绝）。一轮 plan 任务只能在 `harness init` 自动创建的开发分支 `harness/<task_id>` 上执行；publish/step/approve 在保护分支或不匹配的开发分支上直接拒绝。git merge 等写操作默认在 shell 白名单外，agent 无法自行合入。
6. **合入需人工签发 token**：plan 全部 step 完成后 AI 运行 `harness complete` 结项。用户在自己终端运行 `harness merge`（无参数，输口令）签发一次性 merge token（24h 有效）；把 token 交给 AI 后，AI 运行 `harness merge --token <token>` 执行 `--no-ff` 合入（用户自己执行同一命令也行）。token 用掉即失效；token 丢失可在终端重新运行 `harness merge` 重新签发，旧 token 随即作废。

## 工作流

```bash
# 起草 plan 到 .harness-tmp/ 并发布，等用户 approve 签字
python .harness/scripts/harness publish plan.md --from .harness-tmp/plan.md
python .harness/scripts/harness step start <id> --title "..."
# 在 .harness-tmp/ 改代码并发布
python .harness/scripts/harness publish src/x.py --from .harness-tmp/x.py
python .harness/scripts/harness verify --step <id> -- <验收命令>
python .harness/scripts/harness step done <id> -m "做了什么/为什么"
```

## 验收 harness verify（AI 自述不算，必须 harness 执行）

`step done` 强制要求该 step 有 harness 执行且通过的证据；纯规划 step 用 `step done --no-verify --reason "..."`。

exit=0 只是必要条件，四道断言任一不过即 FAIL：

- `--out <glob>`：产物必须新于本次运行（新建/mtime/hash 变化其一），否则判陈旧产物冒充。
- `--require <regex>`：输出必须命中路径激活正证据（如 `gpu_quads=[1-9]`）。
- `--forbid <regex>`：输出不得含回退信号（如 `fallback|FORCE_SWRAST`），命中即失败。
- `--must-differ <label>`：产物必须与同 step 内该 label 的通过证据有差异，完全一致=疑似没切换。

证据必须新鲜：`step done` 只接受晚于该 step 最后一次 publish/adopt 的通过证据——改了代码必须重新 verify。

```bash
# 对照组（禁用目标路径）
harness verify --step 1 --label ctrl --out 'out.png' 'FORCE_SWRAST=1 ./render'
# 实验组：产物新鲜 + 路径激活 + 无回退 + 与对照组有差异
harness verify --step 1 --label gpu --out 'out.png' \
  --require 'gpu_quads=[1-9]' --forbid 'fallback|FORCE_SWRAST' \
  --must-differ ctrl './render'
```

## 常用命令

- `harness status` 状态摘要；`harness evidence <step>` 证据链
- `harness branch` 查看/变更保护分支（--add/--remove 需用户口令）
- `harness complete` / `harness merge --token <t>` 结项与人工合入
- `harness log` / `harness log why <file>` 改动历史与原因
- `harness replan <op>` 任务树重编排（insert/reorder/split/merge/delete/update/list）
- `harness inspect` 详细检查；`harness plan-gen <objective>` 生成 plan 骨架
- `harness validate` 校验 state 一致性；`harness abort` 回退到上个 checkpoint（需用户口令）

## 超阈与 ABORTED 终态接续（v3.1.1）

- **单步改动 ≤500 行 LOC**（超阈必回退：3 失败 / 30min / 10 文件 / 500 LOC / 5 次介入）。超阈步骤不可标记验收通过；标准做法是拆子步骤逐个验收，或精简冗余改动。无法拆分的大改动需用户授权豁免，不得硬刚。
- **ABORTED 是终态**：`state_machine.py` 转移表中 `ABORTED: set()` 无出边，不存在 ABORTED → IN_PROGRESS 等任何活动态转移。`harness status` 摘要显示 IN_PROGRESS 只是快照未同步，以底层状态为准。
- **接续必须走正式 init**：ABORTED 后运行 `harness init --force` 重置计划、新建 task_id（不带 `--force` 会因已有 state 直接跳过）。ABORTED 终止的是流程，不回滚已 publish 的代码——已完成 step 的代码、git tag、worklog 全部保留，新 plan 中引用即可，不得重做。
- **盘点残留改动**：新任务第一步 `git diff --stat` 盘点旧任务未验收 step 的改动，逐项决定保留 / 精简 / 顺延，并在 plan 中记录旧任务 ABORTED 原因，保持溯源链完整。
- **禁止绕过**：不得直接改 `.harness/state.json` 把 ABORTED 改回活动态——`.harness/` 写入被 deny，篡改即全局锁定；`harness validate` 也会拒绝非法转移。

## 红线

- 不得用 chmod/补丁关闭或修改 hook；升级走 `install.sh --upgrade`
- 不得绕过 publish 直接写项目文件
- 验收只用 harness verify 的证据，不得自述或伪造
