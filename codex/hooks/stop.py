#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop hook :: 收尾一致性检查（规则 9/10）。

在本轮即将结束前，校验：
  · 工作区存在未登记改动 → block（要求 publish/adopt）；
  · 有发布记录但 worklog 未同步 → block（要求 step done 追加 worklog）；
  · plan 未批准但有改动 → block（要求先规划）。
用 stop_hook_active 防止死循环。
"""
from __future__ import annotations
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_hook_lib as hl  # noqa: E402


@hl.guarded_context
def main() -> int:
    try:
        if os.environ.get("HARNESS_HOOKS_DISABLE") == "1":
            return 0
    except Exception:
        return 0
    payload = hl.load_payload()
    if payload.get("stop_hook_active"):
        return 0
    root = hl.project_root(payload)
    harness = hl.harness_dir(root)
    if root is None or harness is None:
        return 0

    ok, bad = hl.verify_lock(root, harness)
    if not ok:
        hl.emit_stop_block("防篡改锁单校验失败：%s" % ", ".join(bad))
        return 0

    reasons = []

    # 1. 未登记改动
    cli = harness / "scripts" / "harness"
    if cli.is_file():
        try:
            proc = subprocess.run(
                [sys.executable, str(cli), "_reconcile", "--quiet"],
                cwd=str(root), capture_output=True, text=True, timeout=15)
            if proc.returncode != 0 and (proc.stdout or "").strip():
                reasons.append(
                    "工作区存在未登记改动（未通过 harness publish/adopt 审计）：\n"
                    + proc.stdout.strip() +
                    "\n处理：用 harness publish 发布，或 harness adopt --reason ... 登记（需口令）。")
        except Exception as exc:
            hl.util_log_error(root, "stop reconcile failed: %s" % exc)

    # 2. worklog 同步：有 publish 但未 step done（open_step 存在且有该 step 的发布）
    st = hl.state_snapshot(harness)
    op = st.get("open_step") or {}
    if op.get("id"):
        sid = str(op["id"])
        has_publish = False
        has_done = False
        for rec in hl.read_jsonl_tail(harness / "manifest.jsonl", 200):
            if str(rec.get("step")) == sid and rec.get("event") in ("publish", "verify"):
                has_publish = True
        for w in hl.read_jsonl_tail(harness / "worklog.jsonl", 200):
            if w.get("event") == "step_done" and str(w.get("step")) == sid:
                has_done = True
        if has_publish and not has_done:
            reasons.append(
                "Step %s 有发布/验收记录但未收尾（规则 10：日志必完整）。"
                "请用 harness step done %s -m \"做了什么/为什么\" 追加 worklog 条目。"
                % (sid, sid))

    # 3. plan 未批准但有项目改动
    if st and not st.get("plan_approved"):
        import subprocess as _sp
        try:
            p = _sp.run(["git", "-C", str(root), "status", "--porcelain"],
                        capture_output=True, text=True, timeout=10)
            dirty = [l for l in (p.stdout or "").splitlines()
                     if l.strip() and not l.startswith("??")
                     and ".harness/" not in l]
            if dirty:
                reasons.append(
                    "plan 未批准但工作区有改动（规则 1：行动前先规划）。"
                    "请先 harness publish plan.md 并请用户 harness approve。")
        except Exception:
            pass

    if reasons:
        hl.emit_stop_block("\n\n".join(reasons))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
