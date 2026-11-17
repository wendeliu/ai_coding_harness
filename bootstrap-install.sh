#!/usr/bin/env bash
# ai-coding-harness — 网络安装引导（curl | bash 入口）
#
# 用法:

#   bash install.sh <project> [options]
#
# 作用:
#   引导脚本只做三件事：检测环境、git clone 仓库到临时目录、调用仓库内的
#   ai-coding-harness/install.sh（真正的模板安装器）并透传全部参数。
#   安装完成后清理临时目录。本脚本不直接接触目标项目的 .harness/。
#
# 透传选项（由仓库内 install.sh 解释）:
#   --objective "..."       任务目标
#   --scopes a/ b/          授权路径
#   --task-id TASK-XXX      自定义任务 ID
#   --copy-only             只铺 .harness/hooks/lock，不初始化任务
#   --upgrade               升级脚本并重算锁单（保留 state/manifest/worklog）
#   --no-codex-hooks        不注册 Codex hooks
#   --passphrase-file FILE  从文件读取授权口令（仅限自动化）
#   -h, --help
#
# 环境变量:
#   HARNESS_REPO   覆盖默认 git 仓库地址
#   HARNESS_REF    覆盖默认分支/标签
#   PYTHON         覆盖 python3 可执行文件名
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info(){ echo -e "${BLUE}[harness]${NC} $*"; }
log_ok(){ echo -e "${GREEN}[harness]${NC} $*"; }
log_warn(){ echo -e "${YELLOW}[harness]${NC} $*"; }
log_fail(){ echo -e "${RED}[harness]${NC} $*" >&2; }

REPO="${HARNESS_REPO:-https://github.com/wendeliu/ai_coding_harness.git}"
REF="${HARNESS_REF:-main}"
PY="${PYTHON:-python3}"

usage(){
  # curl|bash 管道模式下 $0 不是脚本文件，sed 读不到；回退到内置帮助。
  if [ -r "$0" ]; then
    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  else
    cat <<'EOF'
ai-coding-harness — 网络安装引导（curl | bash 入口）

用法:

  bash install.sh <project> [options]

作用:
  引导脚本只做三件事：检测环境、git clone 仓库到临时目录、调用仓库内的
  ai-coding-harness/install.sh（真正的模板安装器）并透传全部参数。
  安装完成后清理临时目录。本脚本不直接接触目标项目的 .harness/。

透传选项（由仓库内 install.sh 解释）:
  --objective "..."       任务目标
  --scopes a/ b/          授权路径
  --task-id TASK-XXX      自定义任务 ID
  --copy-only             只铺 .harness/hooks/lock，不初始化任务
  --upgrade               升级脚本并重算锁单（保留 state/manifest/worklog）
  --no-codex-hooks        不注册 Codex hooks
  --passphrase-file FILE  从文件读取授权口令（仅限自动化）
  -h, --help

环境变量:
  HARNESS_REPO   覆盖默认 git 仓库地址
  HARNESS_REF    覆盖默认分支/标签
  PYTHON         覆盖 python3 可执行文件名
EOF
  fi
  exit 0
}

# ---- 参数：首参为项目目录，其余透传 ----
if [ $# -lt 1 ] || [ "$1" = "-h" ] || [ "$1" = "--help" ]; then usage; fi
TARGET="$1"; shift
PASSTHRU=("$@")

# ---- 环境检测 ----
command -v git >/dev/null || { log_fail "缺少 git，请先安装：apt install git / yum install git"; exit 1; }
command -v "$PY" >/dev/null || { log_fail "缺少 python3，请先安装：apt install python3 / yum install python3"; exit 1; }
[ -d "$TARGET" ] || { log_fail "目标目录不存在: $TARGET"; exit 1; }
TARGET="$(cd "$TARGET" && pwd)"

log_info "repo    : $REPO (@$REF)"
log_info "project : $TARGET"
[ "${#PASSTHRU[@]}" -gt 0 ] && log_info "opts    : ${PASSTHRU[*]}"
echo ""

# ---- clone 到临时目录 ----
TMPDIR="$(mktemp -d -t harness-install.XXXXXX)"
cleanup(){ rm -rf "$TMPDIR"; }
trap cleanup EXIT
chmod 700 "$TMPDIR"

log_info "clone 仓库到临时目录..."
if ! git clone -q --depth 1 --branch "$REF" "$REPO" "$TMPDIR/repo"; then
  # 某些 Gitea 版本对 --branch + --depth 1 组合支持不佳，回退全量 clone
  log_warn "浅克隆失败，回退到普通 clone..."
  if ! git clone -q "$REPO" "$TMPDIR/repo"; then
    log_fail "git clone 失败: $REPO"
    log_fail "请确认网络可达，或用 HARNESS_REPO=... 覆盖地址。"
    exit 1
  fi
  git -C "$TMPDIR/repo" checkout -q "$REF" 2>/dev/null || true
fi

INSTALLER="$TMPDIR/repo/ai-coding-harness/install.sh"
[ -f "$INSTALLER" ] || { log_fail "仓库内未找到 ai-coding-harness/install.sh — 仓库结构异常"; exit 1; }
[ -f "$TMPDIR/repo/ai-coding-harness/template/.harness/scripts/harness" ] \
  || { log_fail "仓库内未找到 harness 模板 — 仓库结构异常"; exit 1; }

log_ok "仓库就绪，调用本地安装器"
echo ""

# ---- 调用仓库内 install.sh，透传全部参数 ----
# install.sh 的 SELF_DIR 推导依赖 BASH_SOURCE，必须用绝对路径调用
bash "$INSTALLER" "$TARGET" "${PASSTHRU[@]}"

# ---- 清理由 trap 处理 ----
log_ok "安装流程结束。"
