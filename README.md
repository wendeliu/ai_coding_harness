# AI Coding Harness

AI Coding Harness 用来管理 AI 在代码仓库中的工作过程。它与 Claude Code、Codex 配合使用，把任务计划、文件修改、测试记录和 Git 提交放在同一套流程里。你可以让 AI 连续完成编码工作，在批准计划、授权额外命令和合入代码时参与。

一个任务从 `plan.md` 开始。AI 把工作拆成若干步骤，你看过计划并批准后，它再逐步修改和验收。每次发布文件都会留下记录，每个完成的步骤都有对应的提交和标签。日后查看某处改动时，可以沿着文件记录找到当时的任务、修改原因和测试结果。

## 安装到项目

本地需要 Bash、Python 3 和 Git，harness 的 Python 脚本使用标准库。目标目录应当是项目的 Git 仓库根目录；如果目录尚未纳入 Git，安装器会创建仓库。

在本项目目录运行：

```bash
./install.sh /path/to/your-project \
  --objective "为配置读取增加格式校验" \
  --scopes src/ tests/
```

`--objective` 填写这轮任务的目标，`--scopes` 填写允许修改的路径。首次安装会请你在终端设置授权口令，后续批准计划和授权操作时会用到。

安装器会在目标项目中放入 `.harness/` 和 `.harness-tmp/`，注册 Git pre-commit，并配置 Claude Code 与 Codex 的 hooks。已有的非 harness hook 配置会保留；原有 Git pre-commit 会先备份，再由 harness 版本替换。

安装默认还会初始化任务，并为当前工作区创建一次初始提交。从 `master` 或 `main` 等保护分支开始时，harness 会自动切换到新建的 `harness/<task_id>` 开发分支；如果已经在其他分支上，则沿用当前分支。安装前可以先整理好工作区，让这次初始提交对应一个清楚的起点。

只使用其中一个编码工具时，可以省去另一个工具的配置：

```bash
# 使用 Claude Code
./install.sh /path/to/your-project --no-codex-hooks

# 使用 Codex
./install.sh /path/to/your-project --no-claude-hooks
```

如果想先安装、稍后再开始任务，可以加上 `--copy-only`，之后在目标项目中运行：

```bash
python3 .harness/scripts/harness init \
  --objective "为配置读取增加格式校验" \
  --scopes src/ tests/
```

首次在目标项目中启动编码工具时，需要将工作区设为受信任项目，以便加载项目里的 hooks。安装后可以检查配置和当前任务：

```bash
cd /path/to/your-project
python3 .harness/scripts/harness doctor
python3 .harness/scripts/harness status
```

## 完成一轮任务

下面以修改配置读取逻辑为例。命令都在目标项目根目录运行，文件名和测试命令按实际项目替换。

### 写计划并批准

AI 先在 `.harness-tmp/plan.md` 中起草计划，写明目标、步骤、涉及的文件和验收方式，再发布到项目根目录：

```bash
python3 .harness/scripts/harness publish plan.md \
  --from .harness-tmp/plan.md
```

你看过 `plan.md` 后，在自己的终端输入口令批准：

```bash
python3 .harness/scripts/harness approve
```

批准记录会保存这份计划的内容摘要。后续如果重新发布了计划，需要再次批准，然后继续执行。

### 按步骤修改文件

AI 开始一个步骤后，在 `.harness-tmp/` 中准备文件，通过 `publish` 写入项目：

```bash
python3 .harness/scripts/harness step start 1 \
  --title "增加配置格式校验和测试"

python3 .harness/scripts/harness publish src/config.py \
  --from .harness-tmp/config.py \
  --note "在读取配置时检查字段类型"

python3 .harness/scripts/harness publish tests/test_config.py \
  --from .harness-tmp/test_config.py \
  --note "补充字段缺失和类型错误的测试"
```

`.harness-tmp/` 是起草目录，不随步骤提交。项目中的正式文件通过 `publish` 更新，harness 会检查文件内容、记录哈希，并把发布记录关联到当前步骤。直接使用 Write、Edit 或补丁工具修改项目文件时，hook 会提示改走发布流程。

### 运行测试并完成步骤

测试交给 `verify` 执行，它会保存命令、输出、退出码和耗时：

```bash
python3 .harness/scripts/harness verify --step 1 \
  --label unit -- 'python3 -m pytest tests/test_config.py'
```

验收通过后，终端会输出一个确认 token。AI 查看结果，核对测试覆盖的内容，再用这个 token 将证据正式记入当前步骤：

```bash
python3 .harness/scripts/harness verify --step 1 \
  --commit --token '<验收输出中的 token>' \
  --intent "覆盖配置读取接口，检查合法配置、字段缺失和类型错误"

python3 .harness/scripts/harness step done 1 \
  -m "增加字段类型校验，并补充异常配置的测试"
```

完成步骤时，harness 会核对文件记录和验收证据，创建 Git 提交，并打上步骤标签。如果测试后又发布了代码，需要重新验收。其余步骤也按这个顺序完成。

### 结项与合入

全部步骤完成后，AI 提交结项：

```bash
python3 .harness/scripts/harness complete
```

你检查改动和验收记录，在自己的终端签发合入 token：

```bash
python3 .harness/scripts/harness merge
```

输入口令后得到的 token 可以交给 AI，也可以由你自己使用：

```bash
python3 .harness/scripts/harness merge --token '<合入 token>'
```

harness 会切换到任务对应的保护分支，用 `--no-ff` 合入开发分支。合入 token 有效期为 24 小时，使用一次后失效，开发分支会保留。

## 按任务选择验收条件

除了检查退出码，`verify` 还可以检查产物和日志。比如一个生成报告的任务，可以要求本次运行更新报告文件、输出处理记录数，并且没有出现读取失败的信息：

```bash
python3 .harness/scripts/harness verify --step 2 \
  --label report \
  --out 'build/report.json' \
  --require 'processed=[1-9][0-9]*' \
  --forbid 'read failed' \
  -- 'python3 tools/generate_report.py'
```

这里的日志表达式需要与程序实际输出一致。`--out` 检查产物是否由本次运行新建或更新，`--require` 和 `--forbid` 分别检查输出中应当出现和不应出现的内容。有对照实验时，还可以使用 `--must-differ <label>`，比较当前产物与同一步骤下指定验收记录的产物。

验收命令应当对应这一步真正要交付的功能。单元测试、接口调用和浏览器操作覆盖的内容不同，可以在计划中写清楚各自的用途，并在 `--intent` 中记录这次测试检查了什么。

## 查看进展和修改记录

任务状态、步骤证据和文件历史都可以从命令行查看：

```bash
python3 .harness/scripts/harness status
python3 .harness/scripts/harness evidence 1
python3 .harness/scripts/harness log
python3 .harness/scripts/harness log why src/config.py
```

也可以打开本地页面查看：

```bash
python3 .harness/scripts/harness dashboard
```

默认地址为 `http://127.0.0.1:8765/`。

## 需要你参与的操作

日常的起草、发布、测试和步骤提交由 AI 完成。批准计划、处理额外命令授权、登记手动改动、回退任务和签发合入 token，则由你在自己的终端输入口令。

当 shell 命令需要额外授权时，hook 会给出授权票编号。你可以查看请求，决定本次是否放行：

```bash
python3 .harness/scripts/harness confirm <授权票编号>
```

如果你在编辑器中手动修改了项目文件，可以登记这次修改的路径和原因，让它与后续的任务记录接上：

```bash
python3 .harness/scripts/harness adopt \
  --path src/config.py --reason "补充配置字段说明"
```

任务计划需要调整时，可以通过 `replan` 插入、拆分或重排步骤。各类命令的用法和异常处理方式见[标准流程手册](docs/harness-standard-manual.md)。

## 升级已有项目

在本项目目录中运行：

```bash
./install.sh /path/to/your-project --upgrade
```

升级会更新脚本和 hooks，重新生成用于检查受保护文件的锁单，并保留现有任务状态、发布记录和工作日志。

## 项目文件

[install.sh](install.sh) 是本地安装入口，[template/](template/) 存放安装到目标项目的 CLI、状态机和配置模板。[codex/](codex/) 中维护 Claude Code 与 Codex 共用的 hook 脚本，以及各自的注册脚本。

[SKILL.md](SKILL.md) 提供给编码助手读取，说明它在目标项目中的工作方式。[标准流程手册](docs/harness-standard-manual.md) 收录完整命令、状态转移和异常处理说明，[CAPABILITY_LIMITS.md](CAPABILITY_LIMITS.md) 介绍实现范围和维护时需要了解的细节。
