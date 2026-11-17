#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ai-coding-harness v3 :: Codex hook 共享库。

只做四件事：
  1. 解析 hook payload、定位项目根；
  2. shell 命令词法切分与白名单分类；
  3. 防篡改锁单校验（fail-closed）；
  4. 授权票（pending ticket）与一次性许可的读写。
本模块不依赖项目内任何代码（防止被篡改后拖垮闸门）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 只读命令（任何参数都不能写文件系统 / 执行子进程的才放这里）
# --------------------------------------------------------------------------- #
SAFE_READ = {
    "ls", "cat", "head", "tail", "nl", "tac", "od",
    "wc", "stat", "file", "tree", "du", "df", "echo", "printf", "pwd",
    "which", "whoami", "id", "hostname", "date", "true", "false", "basename",
    "dirname", "realpath", "seq", "md5sum", "sha1sum", "sha256sum", "sha512sum",
    "sort", "cut", "comm", "join", "column", "rev", "fold", "tr",
    "printenv", "rg", "grep", "egrep", "fgrep", "diff", "cmp",
}
# 注意：env/less/more/uniq/xxd/xargs/tee/sed/awk 等可执行子进程或写文件的
# 命令绝不放进白名单。find / git 由专用分类器处理。

# 只读 git 子命令
SAFE_GIT_SUB = {
    "status", "diff", "log", "show", "rev-parse", "ls-files", "describe",
    "branch", "shortlog", "blame", "grep", "reflog", "worktree", "tag",
    "cat-file", "name-rev", "merge-base", "count-objects", "whatchanged",
    "remote", "config", "stash", "bisect",
}

# git 中会执行外部程序或写文件的全局参数
GIT_FORBIDDEN = {"-c", "--config-env", "--exec-path", "--output",
                 "--ext-diff", "--paginate", "--upload-pack"}

# find 中会执行 / 写文件的参数
FIND_DENY_FLAGS = ("-exec", "-execdir", "-ok", "-okdir", "-delete",
                   "-fprint", "-fprint0", "-fprintf", "-fls")

# 允许的前置环境变量（其余一律需要授权）
SAFE_ENV_EXACT = {
    "LANG", "LANGUAGE", "TZ", "TERM", "NO_COLOR", "CLICOLOR",
    "CLICOLOR_FORCE", "FORCE_COLOR", "COLUMNS", "LINES",
    "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE",
}
SAFE_ENV_PREFIX = ("LC_",)
SAFE_PAGER_VALUE = {"cat"}

WRITE_TOOLS = {
    "apply_patch", "edit", "write", "multiedit", "notebookedit",
    "str_replace_editor", "create_file",
}
SHELL_TOOLS = {
    "exec_command", "shell", "local_shell", "bash", "unified_exec",
    "run_command", "terminal", "execute",
}

# AI 起草区（项目根下的无监管 tmp 目录）。写工具对这里放行；对账/提交忽略。
# 单源常量，pre_tool_use.py 通过 import 复用。
SCRATCH_DIR = ".harness-tmp"

# --------------------------------------------------------------------------- #
# 黄金法则 / 风险矩阵（注入上下文，保证 agent 不遗忘规范）
# --------------------------------------------------------------------------- #
GOLDEN_RULES = """\
[AI Coding Harness v3 · 12 条黄金法则 · 最高优先级]
1.  行动前先规划 — 任何 L1+ 操作前必须 plan.md 经用户签字 APPROVED（harness approve）。
2.  改动前先快照 — 每个 Step 开始前确保有 checkpoint（harness baseline / step tag）。
3.  写文件先合规 — 项目文件唯一写入通道是 harness publish（审核+备份+manifest）。
4.  结论必有证据 — 禁用"我猜测/可能是/应该是"；验收必须 harness verify 固化证据。
    exit=0 只是必要条件：用 --out 断言产物新鲜、--require 断言路径激活、
    --forbid 断言无回退、--must-differ 做负对照。对"完全一致"保持敌意——
    一致可能是"根本没切换"的信号，不是强证据。
5.  模糊必咨询 — 命中模糊点必须暂停征求用户意见。
6.  错误先排序 — 全量扫描、按权重打分、关联分析、再修复。
7.  超阈必回退 — 3 次失败 / 30min / 10 文件 / 500 LOC / 5 次介入，任一触发即回退。
8.  终止必恢复 — 用户 ABORT 后立即停手 + 恢复到 baseline（harness abort）。
9.  会话必续接 — 新会话先读 state.json + worklog.jsonl，禁止凭空开始。
10. 日志必完整 — 每个 Step 在 worklog.jsonl 追加结构化条目（harness step done 自动）。
11. 用户反馈必固化 — 用户意见原话归档 user_feedback.jsonl，复述确认后才执行。
12. 不准直接改文件 — 禁止直接 Write/Edit/apply_patch 改项目文件；必须用 harness publish。

文件修改前请先运行：
    python .harness/scripts/harness status
"""

RISK_MATRIX = """\
[风险分级] L0 只读=自动 | L1 可逆=自动+日志 | L2 中风险=暂停确认 | L3 不可逆=暂停+二次确认
无法分级时，默认按更高一级处理。
"""


# --------------------------------------------------------------------------- #
# payload / 路径
# --------------------------------------------------------------------------- #
def load_payload() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _walk_up(start: Path, marker: str) -> Optional[Path]:
    cur = start
    for _ in range(12):
        if (cur / marker).is_file():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def project_root(payload: dict) -> Optional[Path]:
    cands = []
    for key in ("cwd", "working_directory", "workdir", "project_dir"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            cands.append(Path(v))
    # Claude Code：Bash 的工作目录在 tool_input.cwd（顶层 cwd 仅 SessionStart 有）。
    tin = payload.get("tool_input")
    if isinstance(tin, dict):
        v = tin.get("cwd")
        if isinstance(v, str) and v:
            cands.append(Path(v))
    for env in ("CODEX_PROJECT_DIR", "PWD"):
        v = os.environ.get(env)
        if v:
            cands.append(Path(v))
    cands.append(Path.cwd())
    for c in cands:
        try:
            root = _walk_up(c.resolve() if c.exists() else c, ".harness/state.json")
            if root:
                return root
        except Exception:
            continue
    return None


def harness_dir(root: Optional[Path]) -> Optional[Path]:
    if root is None:
        return None
    h = root / ".harness"
    return h if h.is_dir() else None


def read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_jsonl_tail(path: Path, n: int) -> list:
    out: List[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return out
    for line in lines[-n * 2:]:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out[-n:]


# --------------------------------------------------------------------------- #
# 防篡改锁单
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def verify_lock(root: Path, harness: Path) -> Tuple[bool, List[str]]:
    """返回 (是否完好, 异常文件列表)。锁单缺失也算破坏。"""
    lock = read_json(harness / "lock.json")
    entries = lock.get("files")
    if not entries:
        return False, ["lock.json missing/empty"]
    bad = []
    for rel, expect in entries.items():
        p = root / rel
        got = sha256_file(p)
        if got != expect:
            bad.append(rel)
    return (not bad), bad


# --------------------------------------------------------------------------- #
# shell 词法切分
# --------------------------------------------------------------------------- #
class ShellParseError(Exception):
    pass


def _matching(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """返回与 text[start] 处 open_ch 匹配的 close_ch 下标（不处理引号内的开括号，
    但会跳过引号内的 close）。失败抛 ShellParseError。"""
    depth = 1
    i = start + 1
    inq = None
    while i < len(text):
        ch = text[i]
        if inq:
            if ch == "\\" and inq == '"' and i + 1 < len(text):
                i += 2
                continue
            if ch == inq:
                inq = None
        elif ch in ("'", '"'):
            inq = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ShellParseError("unbalanced %s" % open_ch)


def _extract_substitutions(text: str, out: List[str]) -> str:
    """把 text 中的命令替换抽取到 out，返回替换为占位符的文本。
    处理 $(...)、反引号、<(...)、>(...)、${...} 内的嵌套替换。"""
    res = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            res.append(text[i:i + 2])
            i += 2
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            if i + 2 < n and text[i + 2] == "(":
                # $((算术)) —— 不执行命令
                j = text.find("))", i + 3)
                if j < 0:
                    raise ShellParseError("unbalanced $((")
                res.append("__ARITH__")
                i = j + 2
                continue
            j = _matching(text, i + 1, "(", ")")
            out.append(text[i + 2:j])
            res.append("__SUB__")
            i = j + 1
            continue
        if c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise ShellParseError("unbalanced backtick")
            out.append(text[i + 1:j])
            res.append("__SUB__")
            i = j + 1
            continue
        if c in "<>" and i + 1 < n and text[i + 1] == "(":
            j = _matching(text, i + 1, "(", ")")
            out.append(text[i + 2:j])
            res.append("__SUB__")
            i = j + 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "{":
            j = _matching(text, i + 1, "{", "}")
            inner = text[i + 2:j]
            # ${VAR:-$(cmd)} 这类嵌套
            _extract_substitutions(inner, out)
            res.append("__PARAM__")
            i = j + 1
            continue
        res.append(c)
        i += 1
    return "".join(res)


def _heredoc_ops(line: str) -> List[Tuple[bool, bool, str]]:
    """返回该行（引号外）的 heredoc 操作: [(strip_tabs, quoted, delimiter)]。"""
    ops: List[Tuple[bool, bool, str]] = []
    i = 0
    quote = None
    n = len(line)
    while i < n:
        c = line[i]
        if quote:
            if c == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if line.startswith("<<<", i):
            i += 3
            continue
        if line.startswith("<<", i):
            j = i + 2
            strip = False
            if j < n and line[j] == "-":
                strip = True
                j += 1
            while j < n and line[j] in " \t":
                j += 1
            quoted = False
            if j < n and line[j] in ("'", '"'):
                quoted = True
                q = line[j]
                k = line.find(q, j + 1)
                if k < 0:
                    i += 2
                    continue
                delim = line[j + 1:k]
                j = k + 1
            else:
                m = re.match(r"[A-Za-z0-9_]+", line[j:])
                if not m:
                    i += 2
                    continue
                delim = m.group(0)
                j += len(delim)
            ops.append((strip, quoted, delim))
            i = j
            continue
        i += 1
    return ops


def _strip_heredocs(text: str) -> Tuple[str, List[str]]:
    """移除 heredoc 正文（正文不是命令），并抽取未加引号正文中的命令替换。"""
    lines = text.splitlines(keepends=True)
    kept: List[str] = []
    subs: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        for strip, quoted, delim in _heredoc_ops(line):
            body: List[str] = []
            while i < len(lines):
                cand = lines[i]
                i += 1
                check = cand.rstrip("\r\n")
                if strip:
                    check = check.lstrip("\t")
                if check == delim:
                    break
                body.append(cand)
            if not quoted:
                _extract_substitutions("".join(body), subs)
    return "".join(kept), subs


def _scan_command(text: str):
    """把命令切成 (segments, redirect_targets, substitution_texts)。

    引号内的 $(...)/反引号同样抽取；heredoc 正文按数据（非命令）处理；
    重定向目标单独返回。
    """
    text, heredoc_subs = _strip_heredocs(text)
    segments: List[List[str]] = [[]]
    redirects: List[str] = []
    substitutions: List[str] = list(heredoc_subs)

    def flush():
        nonlocal cur
        if cur != "":
            segments[-1].append(cur)
            cur = ""

    def read_redirect(text: str, i: int) -> Tuple[str, int]:
        n = len(text)
        while i < n and text[i] in " \t":
            i += 1
        start = i
        if i < n and text[i] == "&":
            i += 1
            while i < n and text[i].isdigit():
                i += 1
        else:
            while i < n and text[i] not in " \t\r\n;&|<>":
                i += 1
        return text[start:i].strip("'\""), i

    i = 0
    n = len(text)
    cur = ""
    while i < n:
        c = text[i]
        if c == "\\":
            if i + 1 < n:
                cur += text[i + 1]
                i += 2
                continue
            i += 1
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise ShellParseError("unbalanced single quote")
            cur += text[i + 1:j].replace("__SUB__", "🄋")
            i = j + 1
            continue
        if c == '"':
            j = i + 1
            buf = ""
            while j < n:
                ch = text[j]
                if ch == "\\" and j + 1 < n:
                    buf += text[j + 1]
                    j += 2
                    continue
                if ch == '"':
                    break
                if ch == "`" or (ch == "$" and j + 1 < n and text[j + 1] in "({")\
                        or (ch in "<>" and j + 1 < n and text[j + 1] == "("):
                    # 抽取引号内的命令替换，保留一个不改变命令名的占位符
                    if ch == "$" and j + 1 < n and text[j + 1] == "{":
                        k = _matching(text, j + 1, "{", "}")
                        _extract_substitutions(text[j + 2:k], substitutions)
                        buf += "__PARAM__"
                        j = k + 1
                        continue
                    if ch == "$" and j + 1 < n and text[j + 1] == "(":
                        k = _matching(text, j + 1, "(", ")")
                        substitutions.append(text[j + 2:k])
                        buf += "__SUB__"
                        j = k + 1
                        continue
                    if ch == "`":
                        k = text.find("`", j + 1)
                        if k < 0:
                            raise ShellParseError("unbalanced backtick")
                        substitutions.append(text[j + 1:k])
                        buf += "__SUB__"
                        j = k + 1
                        continue
                    if ch in "<>" and j + 1 < n and text[j + 1] == "(":
                        k = _matching(text, j + 1, "(", ")")
                        substitutions.append(text[j + 2:k])
                        buf += "__SUB__"
                        j = k + 1
                        continue
                buf += ch
                j += 1
            if j >= n:
                raise ShellParseError("unbalanced double quote")
            cur += buf
            i = j + 1
            continue
        if c == "`":
            j = text.find("`", i + 1)
            if j < 0:
                raise ShellParseError("unbalanced backtick")
            substitutions.append(text[i + 1:j])
            cur += "__SUB__"
            i = j + 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "(":
            if i + 2 < n and text[i + 2] == "(":
                j = text.find("))", i + 3)
                if j < 0:
                    raise ShellParseError("unbalanced $((")
                cur += "__ARITH__"
                i = j + 2
                continue
            j = _matching(text, i + 1, "(", ")")
            substitutions.append(text[i + 2:j])
            cur += "__SUB__"
            i = j + 1
            continue
        if c == "$" and i + 1 < n and text[i + 1] == "{":
            j = _matching(text, i + 1, "{", "}")
            _extract_substitutions(text[i + 2:j], substitutions)
            cur += "__PARAM__"
            i = j + 1
            continue
        if c in "<>" and i + 1 < n and text[i + 1] == "(":
            j = _matching(text, i + 1, "(", ")")
            substitutions.append(text[i + 2:j])
            cur += "__SUB__"
            i = j + 1
            continue
        two = text[i:i + 2]
        if c in " \t\r":
            flush()
            i += 1
            continue
        if two in ("&&", "||", ">>", "&>"):
            flush()
            if two in (">>", "&>"):
                tgt, i = read_redirect(text, i + 2)
                redirects.append(tgt)
                continue
            segments.append([])
            i += 2
            continue
        if len(two) == 2 and (two == "2>" or (c.isdigit() and two[1] == ">")):
            flush()
            tgt, i = read_redirect(text, i + 2)
            redirects.append(tgt)
            continue
        if c in (";", "|", "&", "\n"):
            flush()
            segments.append([])
            i += 1
            continue
        if c == ">":
            flush()
            tgt, i = read_redirect(text, i + 1)
            redirects.append(tgt)
            continue
        if c == "<":
            flush()
            tgt, i = read_redirect(text, i + 1)
            # 输入重定向只读；heredoc 的 << 目标是分隔符，不是文件
            if tgt.startswith("<"):
                tgt, i = read_redirect(text, i)
            i = i
            continue
        cur += c
        i += 1
    flush()
    return [s for s in segments if s], redirects, substitutions


# --------------------------------------------------------------------------- #
# 白名单分类
# --------------------------------------------------------------------------- #
def _real(p: str, cwd: Path) -> str:
    q = Path(p)
    if not q.is_absolute():
        q = cwd / q
    try:
        return str(q.resolve())
    except Exception:
        return str(q)


def _is_gateway(argv: List[str], cwd: Path) -> bool:
    if not argv:
        return False
    base = os.path.basename(argv[0]).lower()
    if not (base == "python" or base.startswith("python3") or
            base.startswith("python2") or base.endswith("python")):
        return False
    args = argv[1:]
    while args and args[0] in ("-I", "-S", "-s", "-B", "-O", "-OO") \
            or (args and args[0].startswith("-X")):
        args = args[1:]
    if not args or args[0] in ("-c", "-m"):
        return False
    real = _real(args[0], cwd)
    return real.endswith("/.harness/scripts/harness")


def _split_env(argv: List[str]) -> Tuple[List[str], List[str]]:
    assigns: List[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if "=" in tok:
            name = tok.split("=", 1)[0]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                assigns.append(tok)
                i += 1
                continue
        break
    return assigns, argv[i:]


def _env_allowed(tok: str) -> bool:
    name, _, value = tok.partition("=")
    if name in ("PAGER", "GIT_PAGER"):
        return value.strip().strip("'\"") in SAFE_PAGER_VALUE
    if name in SAFE_ENV_EXACT:
        return True
    return any(name.startswith(p) for p in SAFE_ENV_PREFIX)


def _git_sub_and_rest(argv: List[str]) -> Tuple[str, List[str]]:
    i = 1
    n = len(argv)
    while i < n and argv[i].startswith("-"):
        if argv[i] in ("-C", "--git-dir", "--work-tree", "--namespace") \
                and i + 1 < n:
            i += 2
        else:
            i += 1
    if i >= n:
        return "", []
    return argv[i], argv[i + 1:]


def _classify_git(argv: List[str]) -> bool:
    for a in argv[1:]:
        if a in GIT_FORBIDDEN or any(
                a.startswith(f + "=") for f in
                ("--output", "--config-env", "--exec-path", "--upload-pack")):
            return False
    sub, rest = _git_sub_and_rest(argv)
    if not sub:
        return True
    if sub not in SAFE_GIT_SUB:
        return False
    pos = [a for a in rest if not a.startswith("-")]
    if sub == "branch":
        allowed = {"-v", "-vv", "-vvv", "-a", "-r", "--list", "--show-current",
                   "--no-color", "-i", "-q"}
        return all(a in allowed for a in rest)
    if sub == "tag":
        if not rest:
            return True
        if rest[0] in ("-l", "--list", "-n", "--contains", "--points-at",
                       "--sort", "--format"):
            return all(not a.startswith("-") or a in
                       ("-l", "--list", "-n") or "=" in a for a in rest)
        return False
    if sub == "config":
        reads = ("--get", "--get-all", "--get-regexp", "--list", "-l",
                 "--get-urlmatch")
        return any(a in reads or a.startswith("--get=") for a in rest)
    if sub == "remote":
        return not rest or rest[0] in ("-v", "--verbose", "show", "get-url")
    if sub == "stash":
        return not rest or rest[0] in ("list", "show")
    if sub == "reflog":
        return not rest or rest[0] in ("show",) or all(
            a.startswith("-") for a in rest)
    if sub == "worktree":
        return rest[:1] == ["list"]
    if sub == "bisect":
        return not rest or rest[0] in ("list", "view", "log", "replay")
    return True


def _classify_find(argv: List[str]) -> bool:
    for a in argv[1:]:
        for bad in FIND_DENY_FLAGS:
            if a == bad or a.startswith(bad + "="):
                return False
    return True


def _classify_safe_base(base: str, argv: List[str]) -> bool:
    args = argv[1:]
    if base == "sort":
        for a in args:
            if a in ("-o", "--output", "-T", "--temporary-directory",
                     "--compress-program", "--files0-from"):
                return False
            if a.startswith("--output=") or a.startswith("--compress-program=") \
                    or a.startswith("--temporary-directory="):
                return False
        return True
    if base == "rg":
        for a in args:
            if a in ("--pre", "-z", "--search-zip") or a.startswith("--pre=") \
                    or a.startswith("--pre-glob"):
                return False
        return True
    if base == "date":
        for a in args:
            if a in ("-s", "--set") or a.startswith("--set=") \
                    or (a.startswith("-s") and len(a) > 2):
                return False
        return True
    return True


def classify_argv(argv: List[str], root: Path,
                  allow: list) -> Tuple[str, str]:
    """返回 (decision, reason)。decision: allow / ticket。"""
    assigns, argv = _split_env(argv)
    for tok in assigns:
        if not _env_allowed(tok):
            return "ticket", "环境变量赋值可能改变命令行为: %s" % tok.split("=", 1)[0]
    if not argv:
        return "allow", ""
    cwd = root
    base = os.path.basename(argv[0]).lower()

    if _is_gateway(argv, cwd):
        return "allow", "harness gateway"

    # 用户许可表必须先于内置分类：git/find/sudo 等内置规则命中即返回，
    # 若许可表放在其后，人工票签发的 exact/bin 许可对这类命令永远无法
    # 命中（o/a/p 授权后每次重试仍被拦截）。许可均源自人工确认的票。
    for e in allow:
        if not isinstance(e, dict):
            continue
        if e.get("expires") and float(e["expires"]) < time.time():
            continue
        et = e.get("type")
        if et == "exact" and e.get("argv") == argv:
            return "allow", "approved exact" + (
                " (one-time)" if e.get("one_time") else "")
        if et == "bin":
            want = e.get("path")
            prog = argv[0]
            if not os.path.isabs(prog):
                # 裸程序名依赖 PATH 解析：_real 只按 cwd 拼接，cwd 下不存在
                # 该文件时 resolve 得到 <root>/<name>，永远匹配不上，
                # 导致 confirm 存的 which() realpath 条目失效。
                import shutil
                prog = shutil.which(prog) or prog
            if _real(prog, cwd) == want:
                return "allow", "approved bin"
            # which 失败（PATH 不含）时按裸名回退：条目本身即按该裸名授权的；
            # 仍要求目标二进制存在且可执行。
            if (not os.path.isabs(argv[0]) and os.sep not in argv[0]
                    and os.path.basename(argv[0]) == os.path.basename(want)
                    and os.path.isfile(want) and os.access(want, os.X_OK)):
                return "allow", "approved bin (basename)"
        if et == "interp_script" and "python" in base:
            if len(argv) >= 2 and not argv[1].startswith("-"):
                if (_real(argv[0], cwd) == e.get("interp")
                        and _real(argv[1], cwd) == e.get("script")):
                    return "allow", "approved script"

    if base in ("sudo", "doas"):
        return "ticket", "提权命令必须人工批准"
    if base == "git":
        return ("allow", "") if _classify_git(argv) else ("ticket",
                                                          "写操作/可执行外部程序的 git 调用需人工批准")
    if base == "find":
        return ("allow", "") if _classify_find(argv) else ("ticket",
                                                           "find 带执行/写入参数")
    if base in SAFE_READ:
        return ("allow", "") if _classify_safe_base(base, argv) else \
            ("ticket", "只读命令携带可能写文件/执行程序的参数")
    return "ticket", "未授权程序: %s" % base


def _redirect_ok(tgt: str, root: Path) -> bool:
    if not tgt or tgt in ("/dev/null", "/dev/stdout", "/dev/stderr"):
        return True
    if re.fullmatch(r"&\d+", tgt):
        return True
    if tgt.startswith("/tmp/") or tgt == "/tmp" \
            or tgt.startswith("/private/tmp/") \
            or tgt.startswith("/proc/self/fd/") \
            or tgt.startswith("/dev/fd/"):
        return True
    # .harness-tmp/ 起草区（相对或绝对）
    if tgt == SCRATCH_DIR or tgt.startswith(SCRATCH_DIR + "/"):
        return True
    try:
        root_real = os.path.realpath(str(root))
        scratch_real = os.path.realpath(str(Path(root_real) / SCRATCH_DIR))
        tgt_abs = tgt if os.path.isabs(tgt) else os.path.join(str(root), tgt)
        tgt_real = os.path.realpath(tgt_abs)
        if tgt_real == scratch_real or tgt_real.startswith(scratch_real + os.sep):
            return True
    except Exception:
        pass
    return False


def classify_command(command: str, root: Path, allow: list):
    """返回 (decision, reason, first_seg, redirects)。"""
    try:
        segs, redirects, subs = _scan_command(command)
    except ShellParseError as exc:
        return "ticket", "shell 解析失败(%s)，默认拒绝" % exc, [], []
    for sub in subs:
        d, r, _, _ = classify_command(sub, root, allow)
        if d != "allow":
            return ("ticket", "命令替换内含未授权操作: %s" % r,
                    segs[0] if segs else [], redirects)
    for tgt in redirects:
        if not _redirect_ok(tgt, root):
            return ("ticket", "重定向写非允许目标（仅 /tmp 与 .harness-tmp/）: %s" % tgt,
                    segs[0] if segs else [], redirects)
    for seg in segs:
        d, r = classify_argv(seg, root, allow)
        if d != "allow":
            return "ticket", r, seg, redirects
    return "allow", "", segs[0] if segs else [], redirects


def classify_argv_list(argv: List[str], root: Path,
                       allow: list) -> Tuple[str, str, List[str]]:
    """处理 tool_input.command 为 argv 数组的情况。"""
    if not argv:
        return "allow", "", []
    d, r = classify_argv(argv, root, allow)
    return d, r, argv


# --------------------------------------------------------------------------- #
# 授权票 / 许可
# --------------------------------------------------------------------------- #
def create_ticket(harness: Path, kind: str, payload: dict) -> str:
    pending = harness / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    tid = "tkt-" + datetime.now().strftime("%H%M%S") + "-" + secrets.token_hex(2)
    rec = {"id": tid, "kind": kind, "ts": datetime.now().isoformat(),
           "expires": time.time() + 1800}
    rec.update(payload)
    (pending / (tid + ".json")).write_text(
        json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return tid


def load_allowlist(harness: Path) -> list:
    data = read_json(harness / "allowlist.json")
    return data.get("entries", []) if isinstance(data.get("entries"), list) else []


def save_allowlist(harness: Path, entries: list) -> None:
    path = harness / "allowlist.json"
    tmp = path.with_name(path.name + ".tmp.%d" % os.getpid())
    tmp.write_text(json.dumps({"version": 1, "entries": entries},
                              ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(str(tmp), str(path))


def consume_one_time_argv(harness: Path, argv: List[str]) -> None:
    """argv 数组命令放行后，消费匹配的一次性许可。"""
    entries = load_allowlist(harness)
    changed = False
    remaining = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        expired = bool(e.get("expires")) and float(e["expires"]) < time.time()
        if e.get("one_time") and (expired or e.get("argv") == argv):
            changed = True
            continue
        remaining.append(e)
    if changed:
        try:
            save_allowlist(harness, remaining)
        except Exception:
            pass


def consume_one_time(harness: Path, command: str) -> None:
    """命令放行后，消费匹配的一次性许可。"""
    try:
        segs, _, _ = _scan_command(command)
    except Exception:
        return
    entries = load_allowlist(harness)
    changed = False
    remaining = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        expired = bool(e.get("expires")) and float(e["expires"]) < time.time()
        if e.get("one_time") and (expired or e.get("argv") in segs):
            changed = True
            continue
        remaining.append(e)
    if changed:
        try:
            save_allowlist(harness, remaining)
        except Exception:
            pass


def log_denial(harness: Path, record: dict) -> None:
    try:
        line = json.dumps(record, ensure_ascii=False)
        with (harness / "denials.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# hook 输出
# --------------------------------------------------------------------------- #
def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def emit_context(event_name: str, text: str) -> None:
    """注入额外上下文（SessionStart / UserPromptSubmit / PostToolUse）。"""
    emit({"hookSpecificOutput": {"hookEventName": event_name,
                                 "additionalContext": text}})


def emit_system_message(message: str, suppress_output: bool = False) -> None:
    emit({"systemMessage": message, "suppressOutput": suppress_output})


def emit_deny(event: str, reason: str) -> None:
    emit({"hookSpecificOutput": {"hookEventName": event,
                                 "permissionDecision": "deny",
                                 "permissionDecisionReason": reason}})


def emit_stop_block(reason: str) -> None:
    emit({"hookSpecificOutput": {"hookEventName": "Stop", "decision": "block",
                                 "reason": reason},
          "decision": "block", "reason": reason})


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def monotonic() -> float:
    import time as _t
    return _t.monotonic()


def state_snapshot(harness: Optional[Path]) -> dict:
    if harness is None:
        return {}
    return read_json(harness / "state.json")


def state_summary(harness: Optional[Path]) -> str:
    """一行式状态摘要，用于注入上下文。"""
    if harness is None:
        return "状态: 未检测到 .harness/（Harness 未初始化）"
    st = state_snapshot(harness)
    if not st:
        return "状态: .harness/ 已存在但 state.json 缺失或不可读"
    task = st.get("task_id") or "?"
    phase = st.get("phase") or st.get("status") or "?"
    step = (st.get("open_step") or {}).get("id") or st.get("current_step") or "-"
    approved = st.get("plan_approved")
    return "状态: task=%s phase=%s step=%s %s" % (
        task, phase, step,
        "plan=APPROVED" if approved else "plan=NOT_APPROVED")


def plan_exists(harness: Optional[Path]) -> bool:
    if harness is None:
        return False
    return (harness.parent / "plan.md").is_file()


def util_log_error(root: Optional[Path], message: str) -> None:
    """内部异常记录到 .harness/hook_errors.log（不抛出）。"""
    try:
        h = harness_dir(root) if root else None
        target = h or (root or Path.cwd())
        if target.name != ".harness":
            target = target / ".harness"
        target.mkdir(parents=True, exist_ok=True)
        with (target / "hook_errors.log").open("a", encoding="utf-8") as fh:
            fh.write("%s %s\n" % (now_iso(), message))
    except Exception:
        pass


def guarded(main_fn):
    """闸门 hook 异常策略：fail-closed。

    解析类异常 fail-open 会被攻击利用（喂坏 JSON 即放行），因此默认 deny。
    纯上下文注入型 hook（SessionStart/UserPromptSubmit/PostToolUse/Stop）应使用
    guarded_context，异常时 fail-open（不阻断会话）。
    """
    def wrapper():
        try:
            return main_fn() or 0
        except SystemExit:
            raise
        except Exception as exc:
            emit_deny("PreToolUse",
                      "harness hook 内部异常，fail-closed 拒绝: %s: %s\n"
                      "如确为误报，由用户在自己的终端设置 HARNESS_HOOKS_DISABLE=1。"
                      % (type(exc).__name__, exc))
            return 0
    return wrapper


def guarded_context(main_fn):
    """上下文注入型 hook 的异常策略：fail-open（记录但不阻断会话）。"""
    def wrapper():
        try:
            return main_fn() or 0
        except SystemExit:
            raise
        except Exception as exc:
            try:
                payload = load_payload()
                root = project_root(payload)
                util_log_error(root, "hook fail-open: %s: %s"
                               % (type(exc).__name__, exc))
            except Exception:
                pass
            return 0
    return wrapper
