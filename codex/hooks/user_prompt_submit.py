#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook :: 每轮注入规则提醒 + 用户原话归档。

- 注入极简规则提醒，避免长对话中规范被"稀释/遗忘"。
- 依据黄金法则 11（用户反馈必固化），把用户本轮原话追加到 user_feedback.jsonl。
- 强制 agent 维护 plan/worklog：每轮提醒当前 step 状态与待办。
"""
from __future__ import annotations
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_hook_lib as hl  # noqa: E402


def _archive_raw_prompt(harness, prompt: str) -> None:
    """把用户原话归档到 user_feedback.jsonl（不改写、只追加）。"""
    if harness is None or not prompt.strip():
        return
    try:
        digest = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        record = {
            "feedback_id": "RAW-%d" % int(hl.monotonic() * 1000),
            "timestamp": hl.now_iso(),
            "type": "auto_raw_capture",
            "source": "codex_user_prompt_submit_hook",
            "user_raw_input": prompt,
            "user_input_hash": digest,
            "status": "pending_agent_restatement",
            "note": "黄金法则 11：Agent 需复述确认理解后再执行，并回填 user_confirmation。",
        }
        with (harness / "user_feedback.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        hl.util_log_error(harness, "archive_raw_prompt failed: %s" % exc)


def build_context(root, harness, prompt: str) -> str:
    _archive_raw_prompt(harness, prompt)
    lines = [
        "[Harness 提醒 · 每轮生效]",
        "· 任何 L1+ 操作前：plan.md 已 APPROVED + 已创建 checkpoint。",
        "· 改文件只用 harness publish；禁止直接 Write/Edit/apply_patch。",
        "· 用户意见先原话归档 user_feedback.jsonl 并复述确认，再执行。",
        "· 结论必须附证据；验收用 harness verify 固化，AI 自述不算。",
        "· 每个 Step 结束 harness step done 自动追加 worklog 条目。",
        hl.state_summary(harness),
    ]
    if harness is None:
        lines.append("· 本仓库未初始化 Harness；若任务属长程/生产相关，先运行 install.sh。")
    else:
        st = hl.state_snapshot(harness)
        op = st.get("open_step") or {}
        if op:
            lines.append("· 当前进行中 step=%s「%s」：完成后必须 harness verify + step done。"
                         % (op.get("id"), op.get("title", "")))
        elif st.get("plan_approved"):
            lines.append("· plan 已批准但无进行中 step：用 harness step start <id> --title \"...\"。")
        elif not st.get("plan_approved"):
            lines.append("· plan 未批准：先 harness publish plan.md，再请用户 harness approve。")
    return "\n".join(lines)


@hl.guarded_context
def main() -> int:
    payload = hl.load_payload()
    root = hl.project_root(payload)
    harness = hl.harness_dir(root)
    prompt = ""
    for k in ("prompt", "user_prompt", "input", "message"):
        v = payload.get(k)
        if isinstance(v, str) and v:
            prompt = v
            break
    hl.emit_context("UserPromptSubmit", build_context(root, harness, prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
