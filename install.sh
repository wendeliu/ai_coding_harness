#!/usr/bin/env bash
# ai-coding-harness v3 — 安装器
#
# 用法:
#   ./install.sh <project> [options]
#
# 选项:
#   --objective "..."       任务目标（传给 harness init）
#   --scopes a/ b/          授权路径（传给 harness init）
#   --task-id TASK-XXX      自定义任务 ID
#   --copy-only             只铺 .harness/hooks/lock，不初始化任务
#   --upgrade               升级脚本并重算锁单（保留 state/manifest/worklog）
#   --no-codex-hooks        不注册 Codex hooks（CLI + pre-commit + Claude hooks 仍安装）
#   --no-claude-hooks       不注册 Claude Code hooks（CLI + pre-commit 仍安装）
#   --passphrase-file FILE  从文件读取授权口令（仅限自动化；人应使用 TTY）
#   -h, --help
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info(){ echo -e "${BLUE}[harness]${NC} $*"; }
log_ok(){ echo -e "${GREEN}[harness]${NC} $*"; }
log_warn(){ echo -e "${YELLOW}[harness]${NC} $*"; }
log_fail(){ echo -e "${RED}[harness]${NC} $*" >&2; }

# 幂等追加 ignore 条目（升级路径上旧 ignore 文件缺条目也要补）
ensure_ignore_line(){
  local file="$1" line="$2"
  [ -f "$file" ] || { printf '%s\n' "$line" > "$file"; return; }
  grep -qxF "$line" "$file" || printf '%s\n' "$line" >> "$file"
}

usage(){
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then usage; fi
TARGET="$1"; shift

OBJECTIVE=""; TASK_ID=""; PASSPHRASE_FILE=""
SCOPES=(); COPY_ONLY=false; UPGRADE=false
CODEX_HOOKS=true; CLAUDE_HOOKS=true
while [ $# -gt 0 ]; do
  case "$1" in
    --objective) OBJECTIVE="$2"; shift 2 ;;
    --scopes) shift; while [ $# -gt 0 ] && [[ "$1" != --* ]]; do SCOPES+=("$1"); shift; done ;;
    --task-id) TASK_ID="$2"; shift 2 ;;
    --copy-only) COPY_ONLY=true; shift ;;
    --upgrade) UPGRADE=true; shift ;;
    --no-codex-hooks) CODEX_HOOKS=false; shift ;;
    --no-claude-hooks) CLAUDE_HOOKS=false; shift ;;
    --passphrase-file) PASSPHRASE_FILE="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) log_fail "未知参数: $1"; exit 1 ;;
  esac
done

[ -d "$TARGET" ] || { log_fail "目标目录不存在: $TARGET"; exit 1; }
TARGET="$(cd "$TARGET" && pwd)"
H="$TARGET/.harness"
TPL="$SELF_DIR/template/.harness"
CODEX_SRC="$SELF_DIR/codex"

[ -f "$TPL/scripts/harness" ] || { log_fail "模板缺失: $TPL/scripts/harness"; exit 1; }
command -v "$PY" >/dev/null || { log_fail "缺少 python3"; exit 1; }
command -v git >/dev/null || { log_fail "缺少 git"; exit 1; }

log_info "project : $TARGET"
log_info "harness : $H"
[ "$UPGRADE" = true ] && log_info "mode    : upgrade（保留 state/manifest/worklog）"
echo ""

# ---- 1. git 仓库（必须是仓库根，否则对账/提交会污染父仓库） ----
if git -C "$TARGET" rev-parse --show-toplevel >/dev/null 2>&1; then
  REPO_TOP="$(git -C "$TARGET" rev-parse --show-toplevel)"
  if [ "$REPO_TOP" != "$TARGET" ]; then
    log_fail "目标位于现有仓库内但不是仓库根: $REPO_TOP"
    log_fail "请把 harness 安装到仓库根，或在 $TARGET 内 git init 一个独立仓库。"
    exit 1
  fi
else
  log_info "git init"
  git -C "$TARGET" init -q
fi

# ---- 2. 目录 / 备份 ----
mkdir -p "$H/scripts/git_hooks" "$H/pending" "$H/backups" \
  "$H/codex_hooks" "$H/claude_hooks"
STAMP="$(date +%Y%m%dT%H%M%S)"
if [ -f "$H/scripts/harness" ] && { [ "$UPGRADE" = true ] || [ -f "$H/state.json" ]; }; then
  BK="$H/backups/upgrade-$STAMP"
  mkdir -p "$BK"
  cp "$H/scripts/harness" "$BK/harness" 2>/dev/null || true
  cp "$H/scripts/git_hooks/pre-commit" "$BK/pre-commit" 2>/dev/null || true
  log_info "旧脚本已备份到 ${BK#$TARGET/}"
fi

# ---- 3. 铺 CLI 与 git hook ----
install -m 0755 "$TPL/scripts/harness" "$H/scripts/harness"
install -m 0755 "$TPL/scripts/git_hooks/pre-commit" "$H/scripts/git_hooks/pre-commit"
# v2.2 保留模块：状态机 / plan 生成 / replan / 校验 / 检查
for mod in harness_plan_generator.py harness_replan.py \
           harness_validate_state.py harness_state_inspector.py; do
  [ -f "$TPL/scripts/$mod" ] && install -m 0644 "$TPL/scripts/$mod" "$H/scripts/$mod"
done
[ -f "$TPL/state_machine.py" ] && install -m 0644 "$TPL/state_machine.py" "$H/state_machine.py"
[ -f "$H/.harnessignore" ] || install -m 0644 "$TPL/.harnessignore" "$H/.harnessignore"
[ -f "$H/.gitignore" ] || install -m 0644 "$TPL/.gitignore" "$H/.gitignore"
# AI 起草区 + ignore 条目（fresh 与 --upgrade 都跑；上面两行只在文件不存在时铺模板，
# 升级既有项目时不会补 .harness-tmp/ 条目，故此处无条件幂等追加）。
# 注意：.harness-tmp/ 在项目根，.harness/.gitignore 只作用域 .harness/ 子树，
# 故必须写【项目根】.gitignore 才能让 git add -A 跳过 scratch。
mkdir -p "$TARGET/.harness-tmp"
ensure_ignore_line "$TARGET/.gitignore" ".harness-tmp/"
ensure_ignore_line "$H/.harnessignore" ".harness-tmp/"
ensure_ignore_line "$H/.gitignore" ".harness-tmp/"
log_ok "CLI: .harness/scripts/harness"

# 安装 git pre-commit（使用 git 实际的 hooks 路径，兼容 worktree）
HOOKS_DIR="$(git -C "$TARGET" rev-parse --git-path hooks 2>/dev/null || echo "$TARGET/.git/hooks")"
case "$HOOKS_DIR" in /*) ;; *) HOOKS_DIR="$TARGET/$HOOKS_DIR" ;; esac
if [ -d "$HOOKS_DIR" ]; then
  if [ -f "$HOOKS_DIR/pre-commit" ] \
     && ! grep -q "ai-coding-harness" "$HOOKS_DIR/pre-commit" 2>/dev/null; then
    cp "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/pre-commit.bak.$STAMP"
    log_warn "已备份原有 pre-commit -> pre-commit.bak.$STAMP"
  fi
  install -m 0755 "$TPL/scripts/git_hooks/pre-commit" "$HOOKS_DIR/pre-commit"
  log_ok "git pre-commit: $HOOKS_DIR/pre-commit"
fi

# ---- 4. Codex hooks ----
if [ "$CODEX_HOOKS" = true ]; then
  rm -f "$H/codex_hooks/"*.py 2>/dev/null || true
  for f in "$CODEX_SRC/hooks/"*.py; do
    [ -f "$f" ] || continue
    install -m 0755 "$f" "$H/codex_hooks/$(basename "$f")"
  done
  log_ok "Codex hook 脚本: .harness/codex_hooks/"
  "$SELF_DIR/codex/install_codex_hooks.sh" "$TARGET" || {
    log_warn "hooks.json 注册失败，可稍后手动运行 codex/install_codex_hooks.sh"
  }
fi

# ---- 5. Claude Code hooks（与 Codex 共用同一批单源 hook 脚本）----
if [ "$CLAUDE_HOOKS" = true ]; then
  rm -f "$H/claude_hooks/"*.py 2>/dev/null || true
  for f in "$CODEX_SRC/hooks/"*.py; do
    [ -f "$f" ] || continue
    install -m 0755 "$f" "$H/claude_hooks/$(basename "$f")"
  done
  log_ok "Claude hook 脚本: .harness/claude_hooks/"
  "$SELF_DIR/codex/install_claude_hooks.sh" "$TARGET" || {
    log_warn "settings.json 注册失败，可稍后手动运行 codex/install_claude_hooks.sh"
  }
fi

# ---- 6. 口令 ----
PASS_ARGS=()
[ -n "$PASSPHRASE_FILE" ] && PASS_ARGS=(--passphrase-file "$PASSPHRASE_FILE")
( cd "$TARGET" && "$PY" "$H/scripts/harness" _ensure-passphrase "${PASS_ARGS[@]}" )

# ---- 7. 初始化 ----
if [ "$COPY_ONLY" = true ]; then
  log_info "--copy-only: 跳过 harness init"
elif [ "$UPGRADE" = true ]; then
  log_info "--upgrade: 保留既有任务状态，跳过 init"
else
  INIT_ARGS=(init)
  [ -n "$OBJECTIVE" ] && INIT_ARGS+=(--objective "$OBJECTIVE")
  [ -n "$TASK_ID" ] && INIT_ARGS+=(--task-id "$TASK_ID")
  [ ${#SCOPES[@]} -gt 0 ] && INIT_ARGS+=(--scopes "${SCOPES[@]}")
  ( cd "$TARGET" && "$PY" "$H/scripts/harness" "${INIT_ARGS[@]}" )
fi

# ---- 8. 写锁单（必须在所有受保护文件就位之后） ----
( cd "$TARGET" && "$PY" "$H/scripts/harness" _write-lock )

echo ""
log_ok "安装完成。"
echo ""
echo "下一步（用户在自己的终端）:"
echo "  cd $TARGET"
echo "  python .harness/scripts/harness status"
echo "  python .harness/scripts/harness dashboard     # http://127.0.0.1:8765/"
echo ""
echo "AI 侧工作流:"
echo "  # 在 .harness-tmp/ 起草，然后："
echo "  python .harness/scripts/harness publish plan.md --from .harness-tmp/plan.md"
echo "  # 用户：harness approve"
echo "  python .harness/scripts/harness step start 1 --title \"...\""
echo "  python .harness/scripts/harness publish src/x.py --from .harness-tmp/x.py"
echo "  python .harness/scripts/harness step done 1 -m \"做了什么/为什么\""
echo ""
log_warn "Claude Code 只对受信任项目加载 .claude/。首次运行请选择 Trust this workspace，"
log_warn "或在 ~/.claude/settings.json 的 permissions 中允许后重试。"
log_warn "Codex 只对受信任项目加载 .codex/。首次运行请选择 Trust this workspace，"
log_warn "或在 ~/.codex/config.toml 设置 [projects.\"$TARGET\"] trust_level = \"trusted\"。"
