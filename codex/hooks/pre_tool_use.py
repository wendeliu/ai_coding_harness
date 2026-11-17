#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex PreToolUse hook :: 白名单硬闸门（v3）。

- 写工具对项目内受保护文件一律 deny（含 .harness/.codex/.git/真实源码）；
  .harness-tmp/ 起草区与 /tmp 及项目外路径放行。
- shell：词法切分后逐段白名单分类；未知命令生成授权票并 deny。
- 未知工具按 payload 形状兜底：带 command 当 shell，带 file_path/patch 当写工具。
- 防篡改锁单损坏：只放行只读命令，其余 deny。
"""
from __future__ import annotations
import datetime
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_hook_lib as hl  # noqa: E402

DISABLE = os.environ.get("HARNESS_HOOKS_DISABLE") == "1"

_CONFIRM_HINT = (
    "请用户在【自己的终端】运行以下命令批准（需要授权口令）：\n"
    "  python .harness/scripts/harness confirm {tid}\n"
    "批准后重试本次操作。AI 无法自行输入口令。"
)


def _first(inp: dict, *keys):
    for k in keys:
        v = inp.get(k)
        if v:
            return v
    return ""


def _is_command_key(inp: dict) -> bool:
    return bool(_first(inp, "command", "cmd", "cmd_text", "script"))


def extract_write_targets(inp: dict):
    targets = []
    for key in ("file_path", "path", "notebook_path", "filename", "target_file"):
        v = inp.get(key)
        if isinstance(v, str) and v:
            targets.append(v)
        elif isinstance(v, list):
            targets.extend(x for x in v if isinstance(x, str))
    patch_text = ""
    for key in ("input", "patch", "content", "diff"):
        v = inp.get(key)
        if isinstance(v, str) and ("***" in v or "+++" in v or "@@" in v):
            patch_text = v
            break
    if patch_text:
        for m in re.finditer(r"^\*\*\* (?:Update|Add|Delete) File:\s*(.+?)\s*$",
                             patch_text, re.M):
            targets.append(m.group(1).strip())
        for m in re.finditer(r"^\*\*\* Move to:\s*(.+?)\s*$", patch_text, re.M):
            targets.append(m.group(1).strip())
        for m in re.finditer(r"^\+\+\+ b/(.+?)\s*$", patch_text, re.M):
            targets.append(m.group(1).strip())
    seen, out = set(), []
    for t in targets:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def is_project_file(root, raw: str) -> bool:
    """受保护项目文件 → True（写工具应 deny）；起草区/项目外 → False（放行）。

    判定基于 realpath（解析符号链接到最终目标）：
      · 解析目标在 root 下且不在 .harness-tmp 下 → 受保护（True）。
      · 解析目标在 .harness-tmp 下 → 起草区，放行（False）。
      · 解析目标在项目外（/tmp 等） → 放行（False）。
    不变量：只有 realpath 落在 .harness-tmp 下才放行，故任何会改真实文件内容
    的写都解析到真实文件 → 被拒。scratch 内符号链接指向 src/ → 解析到 src/ → 拒；
    src/ 内符号链接指向 scratch → 解析到 scratch → 放行，但只改 scratch 内容
    （被对账忽略、临时），无真实文件被改，明确接受为无害。
    """
    if not raw:
        return False
    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = Path(root) / p
        p_real = os.path.realpath(str(p))
        root_real = os.path.realpath(str(root))
        scratch_real = os.path.realpath(str(Path(root_real) / hl.SCRATCH_DIR))
    except Exception:
        return True  # fail-closed
    try:
        # 项目外（/tmp 等）→ 不受保护
        if os.path.commonpath([p_real, root_real]) != root_real:
            return False
        # 起草区 → 不受保护
        if p_real == scratch_real or p_real.startswith(scratch_real + os.sep):
            return False
        # root 下但不在起草区 → 受保护（含 .harness/.codex/.git/真实源码）
        return True
    except Exception:
        return True


def write_tool_decision(root, tool_name, inp):
    targets = extract_write_targets(inp)
    project = [t for t in targets if is_project_file(root, t)]
    if project:
        return ("项目文件只能通过 harness 网关发布（含 .harness/.codex/.git 等受保护路径）。\n"
                "请在 .harness-tmp/ 起草后运行：\n"
                "  python .harness/scripts/harness publish <项目内路径> --from .harness-tmp/<文件>"
                "\n本次目标：" + ", ".join(project[:5]))
    if tool_name in hl.WRITE_TOOLS and not targets:
        return ("写工具未能解析出目标路径，按 fail-closed 拒绝。"
                "\n请在 .harness-tmp/ 起草后通过 harness publish 发布。")
    return None


def shell_decision(root, harness, inp):
    command = _first(inp, "command", "cmd", "cmd_text", "input", "script")
    if isinstance(command, list):
        argv = [str(x) for x in command]
        allow = hl.load_allowlist(harness)
        decision, reason, seg = hl.classify_argv_list(argv, root, allow)
        if decision == "allow":
            hl.consume_one_time_argv(harness, argv)
            return None
        tid = hl.create_ticket(harness, "command", {
            "command": argv, "segment": seg, "reason": reason,
            "cwd": str(root),
        })
        return f"白名单拦截：{reason}\n{_CONFIRM_HINT.format(tid=tid)}"
    if not isinstance(command, str) or not command.strip():
        return None
    allow = hl.load_allowlist(harness)
    decision, reason, seg, redirects = hl.classify_command(command, root, allow)
    if decision == "allow":
        hl.consume_one_time(harness, command)
        return None
    tid = hl.create_ticket(harness, "command", {
        "command": command, "segment": seg, "reason": reason,
        "redirects": redirects, "cwd": str(root),
    })
    return f"白名单拦截：{reason}\n{_CONFIRM_HINT.format(tid=tid)}"


def _log(harness, tool, inp, reason, tid):
    snippet = _first(inp, "command", "cmd", "cmd_text", "input", "script",
                     "file_path", "path")
    if isinstance(snippet, list):
        snippet = " ".join(str(x) for x in snippet)
    if isinstance(snippet, str) and len(snippet) > 400:
        snippet = snippet[:400] + "..."
    hl.log_denial(harness, {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "tool": tool, "reason": reason, "ticket": tid, "input": snippet,
    })


def decide(root, harness, tool, inp):
    """返回 (reason, denial_reason, ticket_id)。reason 非空则 deny。"""
    is_write = tool in hl.WRITE_TOOLS
    is_shell = tool in hl.SHELL_TOOLS
    if not is_write and not is_shell:
        # 未知工具按 payload 形状兜底
        if _is_command_key(inp):
            is_shell = True
        elif extract_write_targets(inp):
            is_write = True

    if is_write:
        reason = write_tool_decision(root, tool, inp)
        return reason, "写工具被拒绝", None
    if is_shell:
        before = set((harness / "pending").glob("tkt-*.json")) \
            if (harness / "pending").is_dir() else set()
        reason = shell_decision(root, harness, inp)
        if reason:
            after = set((harness / "pending").glob("tkt-*.json")) \
                if (harness / "pending").is_dir() else set()
            new = sorted(after - before)
            tid = new[-1].stem if new else None
            return reason, "白名单拦截", tid
    return None, "", None


@hl.guarded
def main() -> int:
    if DISABLE:
        return 0
    payload = hl.load_payload()
    tool = str(payload.get("tool_name") or payload.get("toolName")
               or payload.get("tool") or "").lower()
    inp = payload.get("tool_input") or payload.get("toolInput") \
        or payload.get("input") or {}
    if not isinstance(inp, dict):
        inp = {}
    root = hl.project_root(payload)
    harness = hl.harness_dir(root)
    if root is None or harness is None:
        return 0

    ok, bad = hl.verify_lock(root, harness)
    if not ok:
        # 锁单损坏：只放行只读操作
        is_write = tool in hl.WRITE_TOOLS
        is_shell = tool in hl.SHELL_TOOLS
        if not is_write and not is_shell:
            if _is_command_key(inp):
                is_shell = True
            elif extract_write_targets(inp):
                is_write = True
        if is_write:
            reason = ("防篡改锁单校验失败：%s。已拒绝一切变更操作。\n"
                      "请用户运行 install.sh --upgrade 修复，或在自己的终端核实文件完整性。"
                      % ", ".join(bad))
            _log(harness, tool, inp, "lock-broken write", None)
            hl.emit_deny("PreToolUse", reason)
            return 0
        if is_shell:
            cmd = _first(inp, "command", "cmd", "cmd_text", "input", "script")
            allow = hl.load_allowlist(harness)
            try:
                if isinstance(cmd, list):
                    d, _, _ = hl.classify_argv_list(
                        [str(x) for x in cmd], root, allow)
                else:
                    d, _, _, _ = hl.classify_command(cmd or "", root, allow)
            except Exception:
                d = "ticket"
            if d != "allow":
                _log(harness, tool, inp, "lock-broken shell", None)
                hl.emit_deny("PreToolUse",
                             "防篡改锁单校验失败：%s。仅只读命令放行。"
                             % ", ".join(bad))
        return 0

    reason, why, tid = decide(root, harness, tool, inp)
    if reason:
        _log(harness, tool, inp, why, tid)
        hl.emit_deny("PreToolUse", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
