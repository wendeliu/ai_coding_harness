#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PostToolUse hook :: 写后校验 + worklog/plan 维护提醒。

在"修改类"工具执行后：
  1. 跑 harness _reconcile，校验是否有未登记写入（绕过 publish 的直写）；
  2. 若发现未登记写入，把失败摘要注入上下文，提醒 Agent 立刻修正；
  3. 提醒维护 worklog/plan（规则 10）与验收证据（防造假）。
"""
from __future__ import annotations
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_hook_lib as hl  # noqa: E402

if os.environ.get("HARNESS_HOOKS_DISABLE") == "1":
    raise SystemExit(0)

_MUTATING = hl.WRITE_TOOLS | hl.SHELL_TOOLS


def run_reconcile(root, harness):
    """跑 harness _reconcile --quiet，返回未登记改动摘要或 None。"""
    cli = harness / "scripts" / "harness"
    if not cli.is_file():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(cli), "_reconcile", "--quiet"],
            cwd=str(root), capture_output=True, text=True, timeout=20)
        if proc.returncode != 0:
            out = (proc.stdout or "").strip()
            return out[-1500:] if out else None
    except Exception as exc:
        hl.util_log_error(root, "post_tool_use reconcile failed: %s" % exc)
    return None


@hl.guarded_context
def main() -> int:
    payload = hl.load_payload()
    tool_name = str(payload.get("tool_name") or payload.get("toolName") or "").lower()
    if tool_name not in _MUTATING:
        return 0
    root = hl.project_root(payload)
    harness = hl.harness_dir(root)
    if harness is None:
        return 0
    notes = []
    failure = run_reconcile(root, harness)
    if failure:
        notes.append(
            "[Harness PostToolUse] 写入校验未通过（存在未登记改动）：\n" + failure +
            "\n处理：用 harness publish <path> --from .harness-tmp/<file> 发布，"
            "或 harness adopt --reason \"...\" 登记（需口令）。")
    notes.append(
        "[Harness PostToolUse] 本次为修改类操作，请确认："
        "(1) 改动已通过 harness publish 发布（manifest 已登记）；"
        "(2) 本 Step 结束时用 harness step done -m \"做了什么/为什么\" 追加 worklog；"
        "(3) 验收用 harness verify 固化证据（AI 自述不算）；"
        "(4) 若改动 ≥ 阈值（10 文件 / 500 LOC）已触发回退。")
    hl.emit_context("PostToolUse", "\n\n".join(notes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
