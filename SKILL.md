---
name: ai-coding-harness
version: 3.1.0
description: A hard-constraint framework for AI coding in auto mode: shell allowlist, the sole project-file write channel harness publish, tamper-proof lock manifest, passphrase authorization, plan/worklog traceability, and dashboard. Constraints exist only in code gates; there are no prompt-based soft constraints.
entry: install.sh
tags: [coding-agent, harness, enforcement, audit, traceability, codex-hooks]
language: en
---

# AI Coding Harness v3

Constraints exist only in code gates. AI must obey the following rules, without exception.

## Hard constraints

1. **Shell allowlist**: Read-only commands are automatically allowed; all other commands (pytest/make/python/git write operations, etc.) are denied and require the user to approve them in the terminal. Do not attempt to bypass this.
2. **The sole write channel is `harness publish`**: Direct Write/Edit/apply_patch to project files is always denied. Draft in `.harness-tmp/` and then publish: `python .harness/scripts/harness publish <dest> --from .harness-tmp/<src>`.
3. **Do not touch `.harness/`**: Write tools always deny writes to `.harness/`; do not disable or modify hooks (tampering triggers a global lock).
4. **Authorization (approve/confirm/adopt/abort) requires the user to enter the passphrase in their own terminal**. AI cannot obtain it on its own; do not attempt to.
5. **Branch protection**: Direct commits to protected branches (default master/main; check with `harness branch`) are prohibited (pre-commit rejects them). A round of plan tasks can only be executed on the development branch `harness/<task_id>` automatically created by `harness init`; publish/step/approve are directly rejected on protected branches or mismatched development branches. Write operations such as git merge are outside the shell allowlist by default, so the agent cannot merge on its own.
6. **Merging requires a manually issued token**: After all plan steps are complete, AI runs `harness complete` to finish the task. The user runs `harness merge` (no arguments, enter the passphrase) in their own terminal to issue a one-time merge token (valid for 24h); after giving the token to AI, AI runs `harness merge --token <token>` to perform a `--no-ff` merge (the user may also run the same command themselves). The token becomes invalid once used; if the token is lost, rerun `harness merge` in the terminal to reissue it, and the old token is immediately invalidated.

## Workflow

```bash
# Draft plan to .harness-tmp/ and publish it, then wait for user approve signature
python .harness/scripts/harness publish plan.md --from .harness-tmp/plan.md
python .harness/scripts/harness step start <id> --title "..."
# Modify code in .harness-tmp/ and publish it
python .harness/scripts/harness publish src/x.py --from .harness-tmp/x.py
python .harness/scripts/harness verify --step <id> -- <verification command>
python .harness/scripts/harness step done <id> -m "what was done / why"
```

## Verification harness verify (AI self-report does not count; it must be executed by harness)

`step done` strictly requires that the step has evidence executed and passed by harness; for a pure planning step, use `step done --no-verify --reason "..."`.

exit=0 is only a necessary condition; if any of the four assertions fails, the result is FAIL:

- `--out <glob>`: The artifact must be newer than this run (newly created / mtime / hash change, any one of these); otherwise it is judged as a stale artifact masquerading as fresh.
- `--require <regex>`: The output must match path-activated positive evidence (e.g. `gpu_quads=[1-9]`).
- `--forbid <regex>`: The output must not contain fallback signals (e.g. `fallback|FORCE_SWRAST`); a match means failure.
- `--must-differ <label>`: The artifact must differ from the passing evidence for that label within the same step; being exactly identical means it is suspected that no switch occurred.

Evidence must be fresh: `step done` only accepts passing evidence later than the last publish/adopt of that step—after changing code, you must re-verify.

```bash
# Control group (disable target path)
harness verify --step 1 --label ctrl --out 'out.png' 'FORCE_SWRAST=1 ./render'
# Experimental group: fresh artifact + path activated + no fallback + differs from control group
harness verify --step 1 --label gpu --out 'out.png' \
  --require 'gpu_quads=[1-9]' --forbid 'fallback|FORCE_SWRAST' \
  --must-differ ctrl './render'
```

## Common commands

- `harness status` status summary; `harness evidence <step>` evidence chain
- `harness branch` view/change protected branches (`--add/--remove` requires the user passphrase)
- `harness complete` / `harness merge --token <t>` task completion and manual merge
- `harness log` / `harness log why <file>` change history and reasons
- `harness replan <op>` task tree re-orchestration (insert/reorder/split/merge/delete/update/list)
- `harness inspect` detailed inspection; `harness plan-gen <objective>` generate a plan skeleton
- `harness validate` validate state consistency; `harness abort` roll back to the last checkpoint (requires the user passphrase)

## Over-threshold and ABORTED terminal-state continuation (v3.1.1)

- **Single-step change ≤500 LOC** (over-threshold must roll back: 3 failures / 30min / 10 files / 500 LOC / 5 interventions). Over-threshold steps cannot be marked as verification-passed; the standard approach is to split them into substeps and verify each one, or streamline redundant changes. Large changes that cannot be split require user authorization for an exemption; do not force them through.
- **ABORTED is a terminal state**: In the `state_machine.py` transition table, `ABORTED: set()` has no outgoing edges; there is no transition such as ABORTED → IN_PROGRESS or any other active state. `harness status` showing IN_PROGRESS in its summary only means the snapshot has not been synchronized; the underlying state is authoritative.
- **Continuation must go through formal init**: After ABORTED, run `harness init --force` to reset the plan and create a new task_id (without `--force`, it will directly skip because state already exists). ABORTED terminates the process; it does not roll back published code—the code, git tags, and worklog of completed steps are all retained. Reference them in the new plan; do not redo them.
- **Inventory residual changes**: The first step of the new task is to run `git diff --stat` to inventory changes from unverified steps of the old task, decide item by item whether to keep / streamline / defer them, and record the reason for the old task's ABORTED in the plan, keeping the traceability chain complete.
- **Bypassing is prohibited**: Do not directly modify `.harness/state.json` to change ABORTED back to an active state—writes to `.harness/` are denied, and tampering triggers a global lock; `harness validate` will also reject illegal transitions.

## Red lines

- Do not use chmod/patches to disable or modify hooks; upgrade via `install.sh --upgrade`
- Do not bypass publish to directly write project files
- Verification uses only evidence from harness verify; no self-reporting or forgery