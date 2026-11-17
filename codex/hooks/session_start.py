#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SessionStart hook :: 注入黄金法则 + 风险矩阵 + 当前状态（保证 agent 不遗忘规范）。

这是"保证 AI 不会忘记 harness 规范"的第一道闸门：每次新会话 / resume / compact
启动时，把 GOLDEN_RULES + RISK_MATRIX + state_summary 拼进模型上下文。
同时注入事实（当前 step / plan 待办 / 最近步骤），便于会话续接（规则 9）。
"""
from __future__ import annotations
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_hook_lib as hl  # noqa: E402


def _pending_plan_items(root, limit=8):
    plan = root / "plan.md"
    if not plan.is_file():
        return []
    out = []
    try:
        for line in plan.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*[-*]\s*\[ \]\s*(.+)", line)
            if m:
                out.append(m.group(1).strip())
                if len(out) >= limit:
                    break
    except Exception:
        pass
    return out


def _facts_block(root, harness):
    """事实数据（当前 step / plan 待办 / 最近步骤 / 锁单状态），用于会话续接。"""
    st = hl.state_snapshot(harness)
    if not st:
        return ""
    parts = ["[Harness 状态 · 仅事实]"]
    parts.append("task=%s phase=%s plan=%s 当前 step=%s" % (
        st.get("task_id", "?"), st.get("phase", "?"),
        "APPROVED" if st.get("plan_approved") else "NOT_APPROVED",
        (st.get("open_step") or {}).get("id") if st.get("open_step") else st.get("last_step", "-")))
    pending = _pending_plan_items(root)
    if pending:
        parts.append("plan 待办:\n" + "\n".join("  - " + p for p in pending))
    for e in hl.read_jsonl_tail(harness / "worklog.jsonl", 3)[::-1]:
        files = e.get("files", [])
        parts.append("step %s [%s] %s files=%s" % (
            e.get("step"), e.get("ts", "")[:16], e.get("message", ""),
            ",".join(files[:6]) + ("..." if len(files) > 6 else "")))
    pend_dir = harness / "pending"
    nticket = len(list(pend_dir.glob("tkt-*.json"))) if pend_dir.is_dir() else 0
    if nticket:
        parts.append("待批授权票: %d（harness confirm）" % nticket)
    ok, bad = hl.verify_lock(root, harness)
    if not ok:
        parts.append("警告（事实）：防篡改锁单校验失败: %s。变更类操作将被拒绝，"
                     "需用户运行 install.sh --upgrade。" % ", ".join(bad))
    return "\n".join(parts)


def build_context(root, harness):
    parts = [hl.GOLDEN_RULES, hl.RISK_MATRIX]
    parts.append(hl.state_summary(harness))
    if harness is None:
        parts.append(
            "提示: 本仓库尚未初始化 Harness。若用户要求工程化约束/长程任务，"
            "先运行 install.sh <project> --objective \"...\"。")
    elif not hl.plan_exists(harness):
        parts.append("警告: 未找到 plan.md。执行任何 L1+ 操作前必须先规划并取得用户 APPROVED。")
    else:
        facts = _facts_block(root, harness)
        if facts:
            parts.append(facts)
    parts.append(
        "本会话所有文件修改都必须经由 harness publish；"
        "PreToolUse hook 会拦截绕过 publish 的直接写文件与危险命令。"
        "验收必须用 harness verify 固化证据，AI 自述不算。"
    )
    return "\n\n".join(parts)


@hl.guarded_context
def main() -> int:
    payload = hl.load_payload()
    root = hl.project_root(payload)
    harness = hl.harness_dir(root)
    text = build_context(root, harness)
    hl.emit_context("SessionStart", text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
