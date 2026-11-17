#!/usr/bin/env bash
# ai-coding-harness v3 · Codex hooks 注册器
#
# 用法: ./install_codex_hooks.sh <project> [--dry-run]
#
# 行为:
#   1. 复制 codex/hooks/*.py -> <project>/.harness/codex_hooks/
#   2. 生成/合并 <project>/.codex/hooks.json（绝对路径；替换旧的 harness 组）
#   3. 打开 <project>/.codex/config.toml 的 [features] hooks = true
#   4. 若 CLI 已就位，重算 .harness/lock.json
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

HOOKS_DST="$TARGET/.harness/codex_hooks"
CODEX_DIR="$TARGET/.codex"
HOOKS_JSON="$CODEX_DIR/hooks.json"
CONFIG_TOML="$CODEX_DIR/config.toml"

if [ "$DRY" = true ]; then
  echo "[dry-run] copy $SELF_DIR/hooks/*.py -> $HOOKS_DST"
  echo "[dry-run] render $SELF_DIR/hooks.json.tmpl -> $HOOKS_JSON (HARNESS_HOOKS_DIR=$HOOKS_DST)"
  echo "[dry-run] ensure [features] hooks=true in $CONFIG_TOML"
  exit 0
fi

mkdir -p "$HOOKS_DST" "$CODEX_DIR"
for f in "$SELF_DIR/hooks/"*.py; do
  [ -f "$f" ] || continue
  install -m 0755 "$f" "$HOOKS_DST/$(basename "$f")"
done

"$PY" - "$SELF_DIR/hooks.json.tmpl" "$HOOKS_JSON" "$HOOKS_DST" <<'PY'
import json
import os
import sys

tmpl, out, hooks_dir = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(tmpl, encoding="utf-8").read().replace("__HARNESS_HOOKS_DIR__", hooks_dir)
ours = json.loads(text)

existing = {}
if os.path.isfile(out):
    try:
        existing = json.load(open(out, encoding="utf-8"))
    except Exception:
        existing = {}
if not isinstance(existing, dict):
    existing = {}

def is_ours(group):
    return ".harness/codex_hooks/" in json.dumps(group, ensure_ascii=False)

hooks = existing.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
for event, groups in ours["hooks"].items():
    kept = [g for g in hooks.get(event, []) if not is_ours(g)]
    hooks[event] = kept + groups
existing["hooks"] = hooks
existing.setdefault("description", ours["description"])
with open(out, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(existing, ensure_ascii=False, indent=2) + "\n")
print("  [+] wrote", out)
PY

"$PY" - "$CONFIG_TOML" <<'PY'
import os
import re
import sys

path = sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
text = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
lines = text.splitlines()
out, in_features, done = [], False, False
for ln in lines:
    stripped = ln.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        if in_features and not done:
            out.append("hooks = true")
            done = True
        in_features = (stripped == "[features]")
        out.append(ln)
        continue
    if in_features and re.match(r"\s*hooks\s*=", ln):
        out.append("hooks = true")
        done = True
        continue
    out.append(ln)
if in_features and not done:
    out.append("hooks = true")
    done = True
if not done:
    if out and out[-1].strip() != "":
        out.append("")
    out.extend(["[features]", "hooks = true"])
with open(path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out).rstrip("\n") + "\n")
print("  [+] ensured [features] hooks=true in", path)
PY

# hooks.json 变了 -> 锁单必须重算
if [ -f "$TARGET/.harness/scripts/harness" ]; then
  ( cd "$TARGET" && "$PY" .harness/scripts/harness _write-lock )
fi

echo "  [+] codex hooks -> $HOOKS_DST"
