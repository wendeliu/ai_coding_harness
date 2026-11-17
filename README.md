# AI Coding Harness

AI Coding Harness manages the process of AI work in code repositories. It works with Claude Code and Codex to bring task plans, file modifications, test records, and Git commits into a single workflow. You can let AI carry out coding work continuously, while you participate when approving plans, authorizing additional commands, and merging code.

A task starts from `plan.md`. AI breaks work into steps, and after you review and approve the plan, it modifies and verifies step by step. Every file publication leaves a record, and every completed step has a corresponding commit and tag. When reviewing a change later, you can follow the file records to find the task, reason for the change, and test results at that time.

**uncertainty make great solution, certainty make great project**

This project’s design philosophy is responsible programming, in contrast to the route of letting AI work as autonomously as possible without human intervention. The project intends for humans to participate in code generation as much as possible, taking on review and quality accountability. Because LLM AI cannot be held accountable for consequences and feels no fear of the harm it may cause, until it is capable of assuming responsibility, only the AI user can practice an ascetic discipline and answer for AI’s actions. As a result, when using it you will find that this harness includes countless points of human intervention and can even feel cumbersome. That is deliberate: it helps people understand what AI is doing and endorse the consequences.

Under such constraints, AI will exhibit rebellious behavior and attempt to bypass process restrictions. Flash-level models in particular, trained on the goal of completing tasks independently, will spend a lot of time studying how to bypass harness control. Therefore, pay special attention to such situations and interrupt pointless token consumption in time.

In addition, the project also optimizes the stability of AI development for ultra-long-horizon tasks to some extent, to reduce development divergence before and after long-horizon tasks and the difficulty of task recovery.

This project summarizes AI's rebellious behavior into several threat models. For details, refer to the project documentation.

## Install into a project

Local requirements are Bash, Python 3, and Git. The harness Python scripts use the standard library. The target directory should be the root of the project's Git repository; if the directory is not yet under Git, the installer will create a repository.

Run in this project's directory:

```bash
./install.sh /path/to/your-project \
  --objective "Add format validation for config reading" \
  --scopes src/ tests/
```

`--objective` is the goal for this round of work, and `--scopes` lists paths allowed to be modified. On first installation, you will be asked in the terminal to set an authorization passphrase, which will be used later when approving plans and authorizing operations.

The installer will place `.harness/` and `.harness-tmp/` in the target project, register a Git pre-commit, and configure hooks for Claude Code and Codex. Existing non-harness hook configurations will be retained; the original Git pre-commit will be backed up first and then replaced by the harness version.

By default, installation also initializes a task and creates an initial commit for the current workspace. When starting from a protected branch such as `master` or `main`, the harness will automatically switch to a newly created `harness/<task_id>` development branch; if already on another branch, the current branch is used. Before installation, you can tidy up the workspace so that this initial commit corresponds to a clear starting point.

When using only one coding tool, you can omit configuration for the other:

```bash
# Use Claude Code
./install.sh /path/to/your-project --no-codex-hooks

# Use Codex
./install.sh /path/to/your-project --no-claude-hooks
```

If you want to install first and start the task later, add `--copy-only`, and then run in the target project:

```bash
python3 .harness/scripts/harness init \
  --objective "Add format validation for config reading" \
  --scopes src/ tests/
```

When starting the coding tool in the target project for the first time, you need to set the workspace as a trusted project so that the project's hooks are loaded. After installation, you can check the configuration and current task:

```bash
cd /path/to/your-project
python3 .harness/scripts/harness doctor
python3 .harness/scripts/harness status
```

## Complete a round of work

The following uses modifying config-reading logic as an example. Commands are run in the target project root; replace file names and test commands with those of the actual project.

### Write and approve a plan

AI first drafts a plan in `.harness-tmp/plan.md`, stating the goal, steps, files involved, and verification method, and then publishes it to the project root:

```bash
python3 .harness/scripts/harness publish plan.md \
  --from .harness-tmp/plan.md
```

After you review `plan.md`, enter the passphrase in your own terminal to approve it:

```bash
python3 .harness/scripts/harness approve
```

The approval record stores a content digest of this plan. If the plan is later republished, it must be approved again before execution continues.

### Modify files step by step

After AI starts a step, it prepares files in `.harness-tmp/` and writes them into the project through `publish`:

```bash
python3 .harness/scripts/harness step start 1 \
  --title "Add config format validation and tests"

python3 .harness/scripts/harness publish src/config.py \
  --from .harness-tmp/config.py \
  --note "Check field types when reading config"

python3 .harness/scripts/harness publish tests/test_config.py \
  --from .harness-tmp/test_config.py \
  --note "Add tests for missing fields and type errors"
```

`.harness-tmp/` is a drafting directory and is not committed with steps. Formal files in the project are updated through `publish`; the harness checks file contents, records hashes, and associates publication records with the current step. When you directly use Write, Edit, or patch tools to modify project files, the hook will prompt you to use the publication process instead.

### Run tests and complete the step

Tests are executed through `verify`, which saves the command, output, exit code, and duration:

```bash
python3 .harness/scripts/harness verify --step 1 \
  --label unit -- 'python3 -m pytest tests/test_config.py'
```

After verification passes, the terminal outputs a confirmation token. AI reviews the result, checks what the test covers, and then uses this token to formally record the evidence into the current step:

```bash
python3 .harness/scripts/harness verify --step 1 \
  --commit --token '<token from verification output>' \
  --intent "Cover the config reading interface and check valid config, missing fields, and type errors"

python3 .harness/scripts/harness step done 1 \
  -m "Add field type validation and tests for invalid config"
```

When completing a step, the harness checks file records and verification evidence, creates a Git commit, and tags the step. If code is published again after testing, verification must be rerun. Complete the remaining steps in the same order.

### Complete the task and merge

After all steps are complete, AI submits task completion:

```bash
python3 .harness/scripts/harness complete
```

You review the changes and verification records, and issue a merge token in your own terminal:

```bash
python3 .harness/scripts/harness merge
```

After entering the passphrase, the resulting token can be given to AI or used by yourself:

```bash
python3 .harness/scripts/harness merge --token '<merge token>'
```

The harness switches to the protected branch corresponding to the task and merges the development branch with `--no-ff`. The merge token is valid for 24 hours, becomes invalid after one use, and the development branch is retained.

## Choose verification conditions by task

In addition to checking exit codes, `verify` can also check artifacts and logs. For example, for a report-generation task, you can require that this run updates the report file, outputs the number of processed records, and does not contain read-failure messages:

```bash
python3 .harness/scripts/harness verify --step 2 \
  --label report \
  --out 'build/report.json' \
  --require 'processed=[1-9][0-9]*' \
  --forbid 'read failed' \
  -- 'python3 tools/generate_report.py'
```

The log expressions here must match the program's actual output. `--out` checks whether the artifact was newly created or updated by this run; `--require` and `--forbid` check for content that should appear and should not appear in the output, respectively. When there is a control experiment, you can also use `--must-differ <label>` to compare the current artifact with the artifact of the specified verification record under the same step.

Verification commands should correspond to the functionality that this step truly delivers. Unit tests, API calls, and browser operations cover different things; you can clarify their respective purposes in the plan and record what this test checked in `--intent`.

## View progress and modification records

Task status, step evidence, and file history can all be viewed from the command line:

```bash
python3 .harness/scripts/harness status
python3 .harness/scripts/harness evidence 1
python3 .harness/scripts/harness log
python3 .harness/scripts/harness log why src/config.py
```

You can also open a local page to view them:

```bash
python3 .harness/scripts/harness dashboard
```

The default address is `http://127.0.0.1:8765/`.

## Operations that require your participation

Daily drafting, publishing, testing, and step commits are completed by AI. Approving plans, handling authorization for additional commands, registering manual changes, rolling back tasks, and issuing merge tokens are done by you in your own terminal by entering the passphrase.

When a shell command requires additional authorization, the hook provides an authorization ticket number. You can view the request and decide whether to allow it this time:

```bash
python3 .harness/scripts/harness confirm <authorization ticket number>
```

If you manually modify project files in an editor, you can register the path and reason for this modification so that it connects with subsequent task records:

```bash
python3 .harness/scripts/harness adopt \
  --path src/config.py --reason "Add config field descriptions"
```

When the task plan needs adjustment, you can use `replan` to insert, split, or reorder steps. For usage of various commands and exception handling, see the [Standard Process Manual](docs/harness-standard-manual.md).

## Upgrade an existing project

Run in this project's directory:

```bash
./install.sh /path/to/your-project --upgrade
```

Upgrading updates scripts and hooks, regenerates the lock manifest used to check protected files, and retains existing task status, publication records, and work logs.

## Project files

[install.sh](install.sh) is the local installation entry point. [template/](template/) stores the CLI, state machine, and configuration templates installed into the target project. [codex/](codex/) maintains hook scripts shared by Claude Code and Codex, as well as their respective registration scripts.

[SKILL.md](SKILL.md) is provided for the coding assistant to read and explains how it works in the target project. The [Standard Process Manual](docs/harness-standard-manual.md) contains full commands, state transitions, and exception handling. [CAPABILITY_LIMITS.md](CAPABILITY_LIMITS.md) introduces the implementation scope and details that need to be understood during maintenance.