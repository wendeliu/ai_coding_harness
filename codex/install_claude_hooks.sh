#!/usr/bin/env bash
# ai-coding-harness v3 · Claude Code hooks 注册器
#
# 用法: ./install_claude_hooks.sh <project> [--dry-run]
#
# 行为:
#   1. 复制 codex/hooks/*.py -> <project>/.harness/claude_hooks/（hook 脚本单源复用，
#      Claude Code 与 Codex 的 stdin/stdout JSON 协议逐字节一致）
#   2. 生成/合并 <project>/.claude/settings.json 的顶层 `hooks` 键
#      （绝对路径；替换旧的 harness 组，保留用户既有非 harness 配置）
#   3. 若 CLI 已就位，重算 .harness/lock.json
#
# 说明: Claude Code 在 <project>/.claude/settings.json 声明 hooks，命令以项目根为 cwd
#      运行；PreToolUse/PostToolUse 用工具名 matcher，SessionStart/UserPromptSubmit/Stop
#      用 "*" 通配。
set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

[ $# -ge 1 ] || { echo "用法: $0 <project> [--dry-run]" >&2; exit 1; }
TARGET="$1"; shift
DRY=false
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=true; shift ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done
[ -d "$TARGET" ] || { echo "目标目录不存在: $TARGET" >&2; exit 1; }
TARGET="$(cd "$TARGET" && pwd)"

HOOKS_DST="$TARGET/.harness/claude_hooks"
CLAUDE_DIR="$TARGET/.claude"
SETTINGS_JSON="$CLAUDE_DIR/settings.json"

if [ "$DRY" = true ]; then
  echo "[dry-run] copy $SELF_DIR/hooks/*.py -> $HOOKS_DST"
  echo "[dry-run] write .claude/settings.json hooks -> $SETTINGS_JSON"
  exit 0
fi

mkdir -p "$HOOKS_DST" "$CLAUDE_DIR"
for f in "$SELF_DIR/hooks/"*.py; do
  [ -f "$f" ] || continue
  install -m 0755 "$f" "$HOOKS_DST/$(basename "$f")"
done

"$PY" - "$SETTINGS_JSON" "$HOOKS_DST" <<'PY'
import json
import os
import sys

base, hooks_dir = sys.argv[1], sys.argv[2]
py = os.environ.get("HARNESS_PY_SHEBANG", "python3")


def script(name, timeout=30):
    return [{"type": "command",
             "command": '%s "%s/%s"' % (py, hooks_dir, name),
             "timeout": timeout}]


MATCHER = "Bash|Write|Edit|NotebookEdit|MultiEdit"
ours = {
    "description": "ai-coding-harness v3 · 运行时硬闸门 + 规范注入 + log/plan 维护 (Claude Code)",
    "hooks": {
        "SessionStart": [{"matcher": "*", "hooks": script("session_start.py")}],
        "UserPromptSubmit": [{"matcher": "*", "hooks": script("user_prompt_submit.py")}],
        "PreToolUse": [{"matcher": MATCHER, "hooks": script("pre_tool_use.py")}],
        "PostToolUse": [{"matcher": MATCHER, "hooks": script("post_tool_use.py")}],
        "Stop": [{"matcher": "*", "hooks": script("stop.py")}],
    },
}

existing = {}
if os.path.isfile(base):
    try:
        with open(base, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            existing = loaded
    except Exception:
        existing = {}


def is_ours(group):
    return ".harness/claude_hooks/" in json.dumps(group, ensure_ascii=False)


def merge_hook_group(existing, ours):
    if not isinstance(existing, dict):
        existing = {}
    hooks = existing.get("hooks") if isinstance(existing.get("hooks"), dict) else {}
    for event, groups in ours["hooks"].items():
        kept = [g for g in hooks.get(event, []) if not is_ours(g)]
        hooks[event] = kept + groups
    existing["hooks"] = hooks
    return existing


existing = merge_hook_group(existing, ours)
if "description" not in existing:
    existing["description"] = ours["description"]
with open(base, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(existing, ensure_ascii=False, indent=2) + "\n")
print("  [+] wrote", base)
PY

# settings.json 变了 -> 锁单必须重算（claude_hooks/*.py 与 .claude/settings.json 都在锁单内）
if [ -f "$TARGET/.harness/scripts/harness" ]; then
  ( cd "$TARGET" && "$PY" .harness/scripts/harness _write-lock )
fi

echo "  [+] claude hooks -> $HOOKS_DST"