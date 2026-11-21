#!/usr/bin/env python3
"""Harness 任务重编排 / Harness task re-planning.

在开发或 debug 阶段，当用户提出新要求时，通过此脚本动态调整任务树：
  - insert:  在指定 step 之前/之后插入新 step
  - reorder: 调整 step 顺序
  - split:   将一个 step 拆分为多个
  - merge:   合并多个连续 step
  - delete:  删除未执行的 step（已完成的不能删）
  - update:  更新 step 的元数据（title/risk/description）

安全约束：
  - 变更操作只允许活跃状态（PLANNING/PENDING_AUTH/IN_PROGRESS/PAUSED）；
    终态任务（COMPLETED/ABORTED/FAILED）只能 list 查看
  - 重编排前必须创建 checkpoint
  - 已 COMPLETED 的 step 不能 delete（但可以 insert 后续）
  - 重编排必须记录到 plan.md Revision History
  - 重编排必须记录到 worklog.jsonl

Usage:
    # 在 step 3 之前插入新 step
    python harness_replan.py insert --before 3 \\
        --title "新增限流测试" --risk L2 --description "用户要求增加限流"

    # 在 step 3 之后插入新 step
    python harness_replan.py insert --after 3 \\
        --title "新增日志增强" --risk L1

    # 把 step 4 移到 step 2 之前
    python harness_replan.py reorder --from 4 --to 2

    # 将 step 3 拆分为 3 个子 step
    python harness_replan.py split --step 3 \\
        --into "实现核心逻辑,添加单元测试,更新文档"

    # 合并 step 3-5 为一个 step
    python harness_replan.py merge --from 3 --to 5 \\
        --title "合并后的实现+测试+文档"

    # 删除未执行的 step 6
    python harness_replan.py delete --step 6

    # 更新 step 3 的元数据
    python harness_replan.py update --step 3 \\
        --title "新标题" --risk L2 --description "新描述"

    # 查看当前任务树
    python harness_replan.py list
"""

from __future__ import annotations
import json
import sys
import re
import subprocess
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


# ============================================================
# task_tree schema 兼容层
# ------------------------------------------------------------
# 历史上 harness 的 step 条目用 {"step_id", "title", "risk",
# "status"="PLANNED/COMPLETED/..."}; 但轻量初始化或手写的 state 用
# {"id", "title", "phase", "status"="pending/completed/..."}。
# 两种 state.json 在真实项目里都存在（本仓库即 "id" 形态），旧脚本一律
# s["step_id"] 直接索引，在 "id" 形态下 KeyError 崩溃。
# 读取同时接受两种键；新建条目时保持宿主 state 既有的 schema。
# ============================================================
def _id_key(state: dict) -> str:
    """宿主 state 的 step 主键名: 任一裸 'id' 条目存在即视为轻量 schema."""
    for s in state.get("task_tree", []):
        if "id" in s and "step_id" not in s:
            return "id"
    return "step_id"


def _sid(step: dict):
    """读取一个 step 的标识，兼容 step_id / id."""
    val = step.get("step_id")
    return val if val is not None else step.get("id")


def _is_completed(step: dict) -> bool:
    return str(step.get("status", "")).upper() == "COMPLETED"


def _find_idx(task_tree: list, sid: str):
    for i, s in enumerate(task_tree):
        if _sid(s) == sid:
            return i
    return None


def _gen_insert_id(state: dict, anchor: str) -> str:
    """为 insert 生成唯一 step 标识（同级 step，非子级）.

    insert 语义=在 anchor 的同级位置插入新 step，故新 id 应与 anchor 同父、
    末段序号 +1（如 anchor=bug-03.1.3 -> bug-03.1.4；anchor=bug-03 -> bug-04；
    anchor=sk-001 -> sk-002；anchor=S8.5 -> S8.6；anchor=3 -> 4）。保留原序号
    位数零填充。取末段序号起首个未占用值。

    注意：旧实现用 `<anchor>.<n>` 生成子级 id，与 split 的子级命名空间
    冲突，且链式 `insert --after <新id>` 会逐层嵌套出 `.1.1.1.1` 怪名
    （见 bug：bug-03.1.3.1.1.1）。此处改为同级递增修复之。
    """
    used = {_sid(s) for s in state.get("task_tree", [])}
    # 拆父路径与末段：bug-03.1.3 -> ("bug-03.1", "3")；bug-03 -> ("", "bug-03")。
    if "." in anchor:
        parent, last = anchor.rsplit(".", 1)
        sep = "."
    else:
        parent, last, sep = "", anchor, ""
    m = re.match(r"^(.*?)(\d+)$", last)
    if not m:
        # 末段非数字（罕见手工 id，如 feat-x / a.b.c）：无法做同级序号递增，
        # 回退到 anchor 下首个未占用 .n（即 <anchor>.1/.2/...），至少保证唯一，
        # 不再无脑 .1 嵌套（取首个未占用值）。
        n = 1
        while True:
            cand = f"{anchor}.{n}"
            if cand not in used:
                return cand
            n += 1
    pre, numstr = m.group(1), m.group(2)
    width = len(numstr)
    n = int(numstr) + 1
    while True:
        nxt = str(n).zfill(width)
        cand = f"{parent}{sep}{pre}{nxt}" if (parent or pre) else nxt
        if cand not in used:
            return cand
        n += 1


def _fresh_step(state: dict, sid: str, title: str, *,
                phase=None, risk: str = "L1", status: str = "PLANNED",
                description: Optional[str] = None, extra: Optional[dict] = None) -> dict:
    """按宿主 state 既有 schema 构造一个新 step."""
    if _id_key(state) == "id":
        step = {"id": sid, "title": title,
                "phase": phase if phase is not None else 0,
                "status": status.lower()}
        if description:
            step["description"] = description
    else:
        step = {"step_id": sid, "title": title, "risk": risk,
                "status": status}
        if description is not None:
            step["description"] = description
    if extra:
        step.update(extra)
    return step


PROJECT_ROOT = Path.cwd()
_HARNESS = PROJECT_ROOT / ".harness"
STATE_PATH = _HARNESS / "state.json"
PLAN_PATH = PROJECT_ROOT / "plan.md"
WORKLOG_JSONL = _HARNESS / "worklog.jsonl"


class ReplanError(Exception):
    """重编排错误 / Re-planning error."""
    pass


# 变更类 replan 操作只允许的 Task 状态（与网关 COMMAND_STATE_POLICY["replan"]
# 一致）。list 只读、任何状态可用。终态任务（COMPLETED/ABORTED/FAILED）及
# 准终态（SUMMARIZING/ABORTING/ROLLING_BACK）一律拒绝——结项后改任务树会
# 破坏 evidence/manifest 与已合并产物的对应关系。此处独立校验：脚本可直接
# 被调用，不能只依赖网关闸门。
MUTABLE_STATUSES = {"PLANNING", "PENDING_AUTH", "IN_PROGRESS", "PAUSED"}


def _require_mutable_status(state: dict) -> None:
    cur = str(state.get("status") or state.get("phase")
              or "IN_PROGRESS").upper()
    if cur not in MUTABLE_STATUSES:
        raise ReplanError(
            "当前任务状态 %s 不允许 replan 变更操作（仅允许：%s）。"
            "终态任务的任务树已与 evidence/manifest 固化对应，只能查看（replan list）。"
            % (cur, ", ".join(sorted(MUTABLE_STATUSES))))


def _load_state() -> dict:
    if not STATE_PATH.exists():
        raise ReplanError(f"state.json not found at {STATE_PATH}")
    return json.load(STATE_PATH.open())


def _save_state(state: dict):
    with STATE_PATH.open("w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def _append_worklog(entry: dict):
    with WORKLOG_JSONL.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_tag_exists(tag: str) -> bool:
    result = subprocess.run(
        ["git", "tag", "-l", tag],
        capture_output=True, text=True
    )
    return tag in result.stdout


def _ensure_checkpoint(state: dict) -> str:
    """确保重编排前有 checkpoint / Ensure checkpoint before re-planning.

    v3 兼容：接受 harness-baseline / harness-s* 标签作为有效 checkpoint
    （v3 用 git tag 作 checkpoint，不再用 checkpoint/<task>/<step> 格式）。
    """
    task_id = state.get("task_id", "UNKNOWN")
    current_step = state.get("current_step", "0")

    # v3 标签优先
    for v3tag in ("harness-baseline",):
        if _git_tag_exists(v3tag):
            return v3tag
    import subprocess as _sp
    try:
        out = _sp.run(["git", "tag", "--list", "harness-s*"],
                       capture_output=True, text=True).stdout
        if out.strip():
            return out.strip().splitlines()[0]
    except Exception:
        pass

    # 重编排专用 checkpoint
    replan_count = state.get("replan_count", 0) + 1
    replan_cp = f"checkpoint/{task_id}/replan-{replan_count}"

    if not _git_tag_exists(replan_cp):
        step_cp = f"checkpoint/{task_id}/{current_step}"
        if not _git_tag_exists(step_cp):
            raise ReplanError(
                f"No checkpoint found for re-planning. "
                f"v3: 先 harness step done 打 tag，或 harness approve 打 baseline。"
            )

    return replan_cp


def _update_plan_revision(state: dict, operation: str, description: str,
                            affected_steps: list):
    """更新 plan.md 的 Revision History / Update plan.md Revision History."""
    if not PLAN_PATH.exists():
        return

    content = PLAN_PATH.read_text()
    task_id = state.get("task_id", "UNKNOWN")
    replan_count = state.get("replan_count", 0) + 1
    version = f"v1.{replan_count + 1}"  # 假设初始 v1.0
    timestamp = _now_iso()
    steps_str = ", ".join(str(s) for s in affected_steps) if affected_steps else "-"

    new_row = (
        f"| {version} | {timestamp} | replan:{operation} | {description} | "
        f"replan-{replan_count} | {steps_str} |"
    )

    # 检查是否已有 Revision History 段
    if "## Revision History" in content:
        # 在表格末尾追加新行
        import re
        pattern = r'(## Revision History.*?\|---\|---\|---\|---\|---\|---\|\n)((?:\|[^\n]+\n)*)'
        match = re.search(pattern, content, re.DOTALL)
        if match:
            existing_rows = match.group(2)
            new_content = content[:match.end()] + existing_rows + new_row + "\n" + content[match.end():]
        else:
            new_content = content + "\n" + new_row + "\n"
    else:
        new_content = content + f"\n\n## Revision History\n\n| 版本 | 时间 | 修改者 | 修改内容 | 触发反馈ID | 影响范围 |\n|---|---|---|---|---|---|\n{new_row}\n"

    PLAN_PATH.write_text(new_content)


def _record_worklog(state: dict, operation: str, details: dict,
                      affected_steps: list):
    """记录重编排到 worklog / Record re-planning to worklog."""
    entry = {
        "type": "REPLAN",
        "timestamp": _now_iso(),
        "task_id": state.get("task_id"),
        "operation": operation,
        "details": details,
        "affected_steps": affected_steps,
        "replan_count": state.get("replan_count", 0) + 1
    }
    _append_worklog(entry)


# ============ 操作实现 ============

def op_insert(state: dict, before: str = None, after: str = None,
                title: str = "", risk: str = "L1",
                description: str = "") -> dict:
    """插入新 step / Insert new step.

    Args:
        before: 在此 step_id 之前插入
        after: 在此 step_id 之后插入
        title: 新 step 的标题
        risk: 风险等级 L0-L3
        description: 描述
    """
    if not before and not after:
        raise ReplanError("Must specify --before or --after")
    if before and after:
        raise ReplanError("Cannot specify both --before and --after")

    task_tree = state.get("task_tree", [])
    anchor = before or after
    anchor_idx = _find_idx(task_tree, anchor)
    if anchor_idx is None:
        raise ReplanError(f"Step {anchor} not found in task_tree")

    new_step_id = _gen_insert_id(state, anchor)
    anchor_phase = task_tree[anchor_idx].get("phase")
    new_step = _fresh_step(state, new_step_id, title or "Untitled",
                           phase=anchor_phase, risk=risk, status="PLANNED",
                           description=description,
                           extra={"inserted_by_replan": True,
                                  "inserted_at": _now_iso()})

    # 插入位置
    if before:
        task_tree.insert(anchor_idx, new_step)
    else:  # after
        task_tree.insert(anchor_idx + 1, new_step)

    # 更新 replan_count
    state["replan_count"] = state.get("replan_count", 0) + 1

    _save_state(state)
    _update_plan_revision(state, "insert", description or f"Insert: {title}",
                           [anchor, new_step_id])
    _record_worklog(state, "insert", {
        "new_step_id": new_step_id,
        "title": title,
        "risk": risk,
        "anchor": anchor,
        "position": "before" if before else "after"
    }, [anchor, new_step_id])

    return {
        "ok": True,
        "operation": "insert",
        "new_step_id": new_step_id,
        "title": title,
        "anchor": anchor,
        "position": "before" if before else "after"
    }


def op_reorder(state: dict, from_step: str, to_step: str) -> dict:
    """调整 step 顺序 / Reorder step.

    把 from_step 移到 to_step 之前。
    """
    task_tree = state.get("task_tree", [])

    from_idx = _find_idx(task_tree, from_step)
    to_idx = _find_idx(task_tree, to_step)

    if from_idx is None:
        raise ReplanError(f"Step {from_step} not found")
    if to_idx is None:
        raise ReplanError(f"Step {to_step} not found")
    if from_idx == to_idx:
        raise ReplanError("Cannot reorder to same position")

    # 取出 from_step，插入到 to_step 之前
    step = task_tree.pop(from_idx)
    # 重新查找 to_idx（因为 pop 后索引可能变化）
    to_idx = _find_idx(task_tree, to_step)
    task_tree.insert(to_idx, step)

    state["replan_count"] = state.get("replan_count", 0) + 1
    _save_state(state)
    _update_plan_revision(state, "reorder",
                           f"Move step {from_step} before {to_step}",
                           [from_step, to_step])
    _record_worklog(state, "reorder", {
        "from": from_step,
        "to": to_step
    }, [from_step, to_step])

    return {
        "ok": True,
        "operation": "reorder",
        "from": from_step,
        "to": to_step
    }


def op_split(state: dict, step_id: str, into: list) -> dict:
    """将一个 step 拆分为多个 / Split step into multiple.

    Args:
        step_id: 要拆分的 step
        into: 拆分后的标题列表
    """
    if len(into) < 2:
        raise ReplanError("Split must produce at least 2 steps")

    task_tree = state.get("task_tree", [])
    target_idx = _find_idx(task_tree, step_id)
    if target_idx is None:
        raise ReplanError(f"Step {step_id} not found")
    target_step = task_tree[target_idx]

    # 生成新 step_id：用 step_id.1, step_id.2, ...
    new_steps = []
    for j, title in enumerate(into, 1):
        new_step = _fresh_step(
            state, f"{step_id}.{j}", title,
            phase=target_step.get("phase"),
            risk=target_step.get("risk", "L1"),
            status="PLANNED",
            description=f"Split from step {step_id}",
            extra={"split_from": step_id, "split_at": _now_iso()})
        new_steps.append(new_step)

    # 替换原 step
    task_tree[target_idx:target_idx + 1] = new_steps

    new_ids = [_sid(s) for s in new_steps]
    state["replan_count"] = state.get("replan_count", 0) + 1
    _save_state(state)
    _update_plan_revision(state, "split",
                           f"Split step {step_id} into {len(into)} steps",
                           [step_id] + new_ids)
    _record_worklog(state, "split", {
        "original_step": step_id,
        "new_step_ids": new_ids,
        "titles": into
    }, [step_id] + new_ids)

    return {
        "ok": True,
        "operation": "split",
        "original_step": step_id,
        "new_step_ids": new_ids
    }


def op_merge(state: dict, from_step: str, to_step: str,
                title: str = "") -> dict:
    """合并多个连续 step / Merge consecutive steps.

    Args:
        from_step: 起始 step_id
        to_step: 结束 step_id
        title: 合并后的标题
    """
    task_tree = state.get("task_tree", [])

    from_idx = _find_idx(task_tree, from_step)
    to_idx = _find_idx(task_tree, to_step)

    if from_idx is None:
        raise ReplanError(f"Step {from_step} not found")
    if to_idx is None:
        raise ReplanError(f"Step {to_step} not found")
    if from_idx > to_idx:
        raise ReplanError(f"from ({from_step}) must be before to ({to_step})")

    # 检查是否有已完成的 step
    merged_steps = task_tree[from_idx:to_idx + 1]
    for s in merged_steps:
        if _is_completed(s):
            raise ReplanError(
                f"Cannot merge: step {_sid(s)} is already COMPLETED"
            )

    merged_ids = [_sid(s) for s in merged_steps]

    # 生成新 step
    new_step = _fresh_step(
        state, from_step,
        title or f"Merged: {from_step}-{to_step}",
        phase=merged_steps[0].get("phase"),
        risk="L1", status="PLANNED",
        description=f"Merged from {from_step} to {to_step}",
        extra={"merged_from": merged_ids, "merged_at": _now_iso()})

    # 替换
    task_tree[from_idx:to_idx + 1] = [new_step]

    state["replan_count"] = state.get("replan_count", 0) + 1
    _save_state(state)
    _update_plan_revision(state, "merge",
                           f"Merge steps {from_step}-{to_step}",
                           [from_step, to_step])
    _record_worklog(state, "merge", {
        "merged_steps": merged_ids,
        "new_step_id": from_step,
        "title": title
    }, [from_step, to_step])

    return {
        "ok": True,
        "operation": "merge",
        "merged_steps": merged_ids,
        "new_step_id": from_step
    }


def op_delete(state: dict, step_id: str) -> dict:
    """删除未执行的 step / Delete unexecuted step.

    已 COMPLETED 的 step 不能删除。
    """
    task_tree = state.get("task_tree", [])
    target_idx = _find_idx(task_tree, step_id)
    if target_idx is None:
        raise ReplanError(f"Step {step_id} not found")
    target_step = task_tree[target_idx]

    if _is_completed(target_step):
        raise ReplanError(
            f"Cannot delete step {step_id}: already COMPLETED. "
            "Use insert to add steps after it instead."
        )

    # 检查是否是 current_step
    if state.get("current_step") == step_id:
        raise ReplanError(
            f"Cannot delete step {step_id}: it is the current step. "
            "Advance or rollback first."
        )

    deleted_step = task_tree.pop(target_idx)
    state["replan_count"] = state.get("replan_count", 0) + 1
    _save_state(state)
    _update_plan_revision(state, "delete",
                           f"Delete step {step_id}: {target_step.get('title','')}",
                           [step_id])
    _record_worklog(state, "delete", {
        "deleted_step_id": step_id,
        "deleted_title": target_step.get("title", "")
    }, [step_id])

    return {
        "ok": True,
        "operation": "delete",
        "deleted_step_id": step_id,
        "deleted_title": target_step.get("title", "")
    }


def op_update(state: dict, step_id: str, title: str = None,
                risk: str = None, description: str = None) -> dict:
    """更新 step 的元数据 / Update step metadata."""
    task_tree = state.get("task_tree", [])
    target_idx = _find_idx(task_tree, step_id)
    if target_idx is None:
        raise ReplanError(f"Step {step_id} not found")
    target_step = task_tree[target_idx]

    changes = {}
    if title is not None:
        changes["title"] = {"old": target_step.get("title", ""),
                              "new": title}
        target_step["title"] = title
    if risk is not None:
        if risk not in ("L0", "L1", "L2", "L3"):
            raise ReplanError(f"Invalid risk: {risk}")
        changes["risk"] = {"old": target_step.get("risk", ""),
                             "new": risk}
        target_step["risk"] = risk
    if description is not None:
        changes["description"] = {"old": target_step.get("description", ""),
                                    "new": description}
        target_step["description"] = description

    if not changes:
        raise ReplanError("No changes specified (use --title/--risk/--description)")

    state["replan_count"] = state.get("replan_count", 0) + 1
    _save_state(state)
    _update_plan_revision(state, "update",
                           f"Update step {step_id}: {list(changes.keys())}",
                           [step_id])
    _record_worklog(state, "update", {
        "step_id": step_id,
        "changes": changes
    }, [step_id])

    return {
        "ok": True,
        "operation": "update",
        "step_id": step_id,
        "changes": changes
    }


def op_list(state: dict) -> dict:
    """查看当前任务树 / List current task tree."""
    task_tree = state.get("task_tree", [])
    current_step = state.get("current_step")

    steps = []
    status_icons = {"COMPLETED": "✓", "IN_PROGRESS": "●",
                    "PLANNED": "○", "PENDING": "○", "FAILED": "✗"}
    for s in task_tree:
        marker = "→" if _sid(s) == current_step else " "
        status_raw = str(s.get("status", "PLANNED"))
        status_icon = status_icons.get(status_raw.upper(), "?")
        steps.append({
            "marker": marker,
            "status_icon": status_icon,
            "step_id": _sid(s),
            "title": s.get("title", ""),
            "risk": s.get("risk", "?"),
            "status": status_raw
        })

    return {
        "ok": True,
        "operation": "list",
        "replan_count": state.get("replan_count", 0),
        "steps": steps
    }


# ============ CLI ============

def main():
    import argparse
    p = argparse.ArgumentParser(
        description="Harness task re-planning (insert/reorder/split/merge/delete/update/list)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Insert before step 3
  python harness_replan.py insert --before 3 --title "新增限流" --risk L2

  # Insert after step 3
  python harness_replan.py insert --after 3 --title "新增日志" --risk L1

  # Reorder: move step 4 before step 2
  python harness_replan.py reorder --from 4 --to 2

  # Split step 3 into 3 sub-steps
  python harness_replan.py split --step 3 --into "实现,测试,文档"

  # Merge steps 3-5
  python harness_replan.py merge --from 3 --to 5 --title "合并实现"

  # Delete step 6
  python harness_replan.py delete --step 6

  # Update step 3 metadata
  python harness_replan.py update --step 3 --title "新标题" --risk L2

  # List current task tree
  python harness_replan.py list
        """
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # insert
    sp = sub.add_parser("insert", help="插入新 step")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--before", help="在此 step_id 之前插入")
    g.add_argument("--after", help="在此 step_id 之后插入")
    sp.add_argument("--title", required=True, help="新 step 标题")
    sp.add_argument("--risk", default="L1", choices=["L0", "L1", "L2", "L3"])
    sp.add_argument("--description", default="")

    # reorder
    sp = sub.add_parser("reorder", help="调整 step 顺序")
    sp.add_argument("--from", dest="from_step", required=True)
    sp.add_argument("--to", dest="to_step", required=True)

    # split
    sp = sub.add_parser("split", help="拆分 step")
    sp.add_argument("--step", required=True)
    sp.add_argument("--into", required=True, help="逗号分隔的子标题列表")

    # merge
    sp = sub.add_parser("merge", help="合并连续 step")
    sp.add_argument("--from", dest="from_step", required=True)
    sp.add_argument("--to", dest="to_step", required=True)
    sp.add_argument("--title", default="")

    # delete
    sp = sub.add_parser("delete", help="删除未执行的 step")
    sp.add_argument("--step", required=True)

    # update
    sp = sub.add_parser("update", help="更新 step 元数据")
    sp.add_argument("--step", required=True)
    sp.add_argument("--title")
    sp.add_argument("--risk", choices=["L0", "L1", "L2", "L3"])
    sp.add_argument("--description")

    # list
    sp = sub.add_parser("list", help="查看当前任务树")
    sp.set_defaults(func=lambda a: "list")

    args = p.parse_args()

    try:
        state = _load_state()

        # list 不需要 checkpoint，也不做状态限制（只读）
        if args.cmd != "list":
            _require_mutable_status(state)
            _ensure_checkpoint(state)

        if args.cmd == "insert":
            result = op_insert(state, before=args.before, after=args.after,
                                title=args.title, risk=args.risk,
                                description=args.description)
        elif args.cmd == "reorder":
            result = op_reorder(state, args.from_step, args.to_step)
        elif args.cmd == "split":
            into_list = [t.strip() for t in args.into.split(",")]
            result = op_split(state, args.step, into_list)
        elif args.cmd == "merge":
            result = op_merge(state, args.from_step, args.to_step, args.title)
        elif args.cmd == "delete":
            result = op_delete(state, args.step)
        elif args.cmd == "update":
            result = op_update(state, args.step, title=args.title,
                                risk=args.risk, description=args.description)
        elif args.cmd == "list":
            result = op_list(state)
        else:
            p.print_help()
            sys.exit(1)

        if args.cmd == "list":
            # list 特殊输出
            print(f"\n{'='*60}")
            print(f"  Task Tree (replan_count={result['replan_count']})")
            print(f"{'='*60}")
            for s in result["steps"]:
                print(f"  {s['marker']} [{s['status_icon']}] step {s['step_id']:<8} "
                      f"[{s['risk']}] {s['status']:<12} {s['title']}")
            print(f"{'='*60}\n")
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"\n[HARNESS][REPLAN] {args.cmd} completed. "
                  f"replan_count={state.get('replan_count', 0)}")
            print(f"  Plan.md Revision History updated.")
            print(f"  Worklog updated.")

        sys.exit(0)
    except ReplanError as e:
        print(f"\n[HARNESS][REPLAN][FAIL] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
