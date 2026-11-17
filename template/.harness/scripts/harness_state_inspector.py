#!/usr/bin/env python3
"""一站式状态查看器 / All-in-one state inspector.

LLM 不必分别读取 state.json / worklog / feedback / compliance / git tags，
调用此脚本即可一站式查看全部状态。

Usage:
    python harness_state_inspector.py
"""

import json
import subprocess
import sys
from pathlib import Path


def inspect():
    """打印完整状态 / Print full state."""
    print(f"\n{'='*70}")
    print(f"  Harness State Inspector")
    print(f"{'='*70}\n")

    _print_state()
    _print_worklog_summary()
    _print_feedback_summary()
    _print_compliance_summary()
    _print_git_tags()
    _print_file_sizes()

    print(f"{'='*70}\n")


def _print_state():
    state_path = Path(".harness/state.json")
    if not state_path.exists():
        print("[state.json] NOT FOUND\n")
        return
    state = json.load(state_path.open())
    print("[state.json]")
    print(f"  task_id:       {state.get('task_id')}")
    print(f"  objective:     {(state.get('task_objective') or '')[:80]}")
    print(f"  status:        {state.get('status')}")
    print(f"  current_step:  {state.get('current_step')}")
    print(f"  frozen:        {state.get('frozen')}")
    print(f"  baseline:      {state.get('baseline', {}).get('tag', 'N/A')}")
    print(f"  scopes:        {state.get('authorized_scopes', [])}")
    uf = state.get("user_feedback", {})
    print(f"  feedback:      total={uf.get('total_count', 0)}, "
          f"pending={len(uf.get('pending_confirmations', []))}")
    steps = state.get("task_tree", [])
    completed = sum(1 for s in steps if s.get("status") == "COMPLETED")
    print(f"  steps:         {completed}/{len(steps)} completed")
    print()


def _print_worklog_summary():
    wl_path = Path(".harness/worklog.jsonl")
    if not wl_path.exists() or wl_path.stat().st_size == 0:
        print("[worklog.jsonl] EMPTY\n")
        return
    entries = []
    for line in wl_path.open():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    print(f"[worklog.jsonl] {len(entries)} entries")
    print("  Last 5 entries:")
    for e in entries[-5:]:
        ts = e.get("timestamp", "")[:19]
        t = e.get("type", "?")
        sid = e.get("step_id", "?")
        print(f"    {ts} [{t}] step={sid}")
    print()


def _print_feedback_summary():
    fb_path = Path("user_feedback.jsonl")
    if not fb_path.exists() or fb_path.stat().st_size == 0:
        print("[user_feedback.jsonl] EMPTY\n")
        return
    records = []
    for line in fb_path.open():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    raw_inputs = [r for r in records if r.get("phase") == "raw_input"]
    confirmations = [r for r in records if r.get("phase") == "user_confirmation"]
    print(f"[user_feedback.jsonl] {len(records)} records "
          f"({len(raw_inputs)} feedbacks, {len(confirmations)} confirmed)")
    if raw_inputs:
        print("  Recent feedbacks:")
        for r in raw_inputs[-3:]:
            ts = r.get("timestamp", "")[:19]
            fid = r.get("feedback_id", "?")
            t = r.get("type", "?")
            raw = (r.get("user_raw_input") or "")[:60]
            print(f"    {ts} {fid} [{t}] {raw}")
    if records:
        print(f"  Hash chain head: {records[-1].get('record_hash', '?')[:24]}...")
    print()


def _print_compliance_summary():
    cr_path = Path(".harness/compliance_report.json")
    if not cr_path.exists():
        print("[compliance_report.json] NOT FOUND\n")
        return
    try:
        report = json.load(cr_path.open())
        entries = report.get("entries", [])
        passed = sum(1 for e in entries if e.get("passed"))
        failed = len(entries) - passed
        print(f"[compliance_report.json] {len(entries)} checks, "
              f"{passed} passed, {failed} failed")
        if failed:
            print("  Recent failures:")
            fails = [e for e in entries if not e.get("passed")][-3:]
            for e in fails:
                print(f"    {e.get('timestamp','')[:19]} {e.get('file','?')}")
    except json.JSONDecodeError:
        print("[compliance_report.json] CORRUPTED")
    print()


def _print_git_tags():
    result = subprocess.run(
        ["git", "tag", "-l"],
        capture_output=True, text=True
    )
    tags = [t for t in result.stdout.splitlines() if t.strip()]
    if not tags:
        print("[git tags] NONE\n")
        return
    baselines = [t for t in tags if t.startswith("baseline/")]
    checkpoints = [t for t in tags if t.startswith("checkpoint/")]
    dones = [t for t in tags if t.startswith("done/")]
    aborts = [t for t in tags if t.startswith("abort/")]
    print(f"[git tags] {len(tags)} total")
    print(f"  baseline:    {len(baselines)}")
    print(f"  checkpoint:  {len(checkpoints)}")
    print(f"  done:        {len(dones)}")
    print(f"  abort:       {len(aborts)}")
    if baselines:
        print(f"  latest baseline: {baselines[-1]}")
    print()


def _print_file_sizes():
    print("[file sizes]")
    for f in [".harness/state.json", ".harness/worklog.md", ".harness/worklog.jsonl",
              ".harness/user_feedback.jsonl", "plan.md", ".harness/summary.md",
              ".harness/scripts/compliance_check.py"]:
        p = Path(f)
        if p.exists():
            size = p.stat().st_size
            print(f"  {f:<25} {size:>8} bytes")
    print()


if __name__ == "__main__":
    inspect()
