"""Harness 任务状态机 / Harness task state machine.

定义 Task 与 Step 两级状态机，强制状态转换合法性。

Usage:
    from state_machine import StateMachine, TaskStatus
    sm = StateMachine()
    sm.transition(TaskStatus.PLANNING)
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from enum import Enum


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    PENDING_AUTH = "PENDING_AUTH"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    ROLLING_BACK = "ROLLING_BACK"
    ABORTING = "ABORTING"
    ABORTED = "ABORTED"
    SUMMARIZING = "SUMMARIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# 合法状态转换 / Allowed transitions
ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.PLANNING, TaskStatus.ABORTING},
    TaskStatus.PLANNING: {TaskStatus.PENDING_AUTH, TaskStatus.ABORTING},
    TaskStatus.PENDING_AUTH: {TaskStatus.PLANNING, TaskStatus.IN_PROGRESS,
                              TaskStatus.ABORTING},
    TaskStatus.IN_PROGRESS: {TaskStatus.PAUSED, TaskStatus.ROLLING_BACK,
                             TaskStatus.ABORTING, TaskStatus.SUMMARIZING,
                             TaskStatus.FAILED, TaskStatus.PLANNING},
    TaskStatus.PAUSED: {TaskStatus.IN_PROGRESS, TaskStatus.ABORTING},
    TaskStatus.ROLLING_BACK: {TaskStatus.IN_PROGRESS, TaskStatus.FAILED,
                             TaskStatus.ABORTING},
    TaskStatus.ABORTING: {TaskStatus.ABORTED},
    TaskStatus.SUMMARIZING: {TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED,
                             TaskStatus.ABORTING},
    TaskStatus.ABORTED: set(),
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
}


class StateMachine:
    """任务状态机 / Task state machine."""

    def __init__(self, state_path: str = "state.json"):
        self.state_path = Path(state_path)
        self.state: dict = self._load_or_init()

    def _load_or_init(self) -> dict:
        if self.state_path.exists():
            return json.load(self.state_path.open())
        return {
            "$schema": "harness-state-v1",
            "task_id": None,
            "task_objective": None,
            "status": TaskStatus.CREATED.value,
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
            "project_root": str(Path.cwd()),
            "authorized_scopes": [],
            "baseline": {},
            "task_tree": [],
            "current_step": None,
            "tried_directions": [],
            "user_feedback": {},
            "context_summary": {},
            "user_authorization": {},
            "compliance": {},
            "frozen": False,
            "frozen_at": None,
        }

    def transition(self, new_status: TaskStatus, **context):
        """执行状态转换 / Execute state transition.

        Args:
            new_status: 目标状态
            **context: 转换上下文（记录到 transitions 历史）

        Raises:
            ValueError: 非法状态转换
        """
        current = TaskStatus(self.state["status"])
        if new_status not in ALLOWED_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Illegal transition: {current.value} -> {new_status.value}. "
                f"Allowed: {[s.value for s in ALLOWED_TRANSITIONS[current]]}"
            )

        old_status = current.value
        self.state["status"] = new_status.value
        self.state["updated_at"] = self._now_iso()
        self.state.setdefault("transitions", []).append({
            "from": old_status,
            "to": new_status.value,
            "at": self._now_iso(),
            "context": context
        })
        self._save()
        self._log_transition(old_status, new_status.value, context)

    def _save(self):
        with self.state_path.open("w") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def _log_transition(self, frm: str, to: str, context: dict):
        """转换历史已记录在 state.json 的 transitions 字段，不再写 worklog.md
        （v3 用 worklog.jsonl，避免 reconcile 误报游离文件）。"""
        return

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


# ============ Step 级状态机 ============

class StepStatus(str, Enum):
    PLANNED = "PLANNED"
    CHECKPOINTING = "CHECKPOINTING"
    EXECUTING = "EXECUTING"
    COMPLIANCE_REVIEW = "COMPLIANCE_REVIEW"
    COMMITTING = "COMMITTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    ROLLING_BACK = "ROLLING_BACK"


STEP_TRANSITIONS: dict[StepStatus, set[StepStatus]] = {
    StepStatus.PLANNED: {StepStatus.CHECKPOINTING, StepStatus.FAILED},
    StepStatus.CHECKPOINTING: {StepStatus.EXECUTING, StepStatus.FAILED},
    StepStatus.EXECUTING: {StepStatus.COMPLIANCE_REVIEW, StepStatus.FAILED,
                           StepStatus.PAUSED, StepStatus.ROLLING_BACK},
    StepStatus.COMPLIANCE_REVIEW: {StepStatus.EXECUTING, StepStatus.COMMITTING,
                                   StepStatus.BLOCKED},
    StepStatus.COMMITTING: {StepStatus.VERIFYING, StepStatus.FAILED},
    StepStatus.VERIFYING: {StepStatus.COMPLETED, StepStatus.EXECUTING,
                           StepStatus.ROLLING_BACK, StepStatus.FAILED},
    StepStatus.BLOCKED: {StepStatus.PAUSED, StepStatus.EXECUTING},
    StepStatus.PAUSED: {StepStatus.EXECUTING, StepStatus.ROLLING_BACK,
                       StepStatus.FAILED},
    StepStatus.ROLLING_BACK: {StepStatus.PLANNED, StepStatus.FAILED},
    StepStatus.COMPLETED: set(),
    StepStatus.FAILED: set(),
}


class StepStateMachine:
    """Step 级状态机 / Step-level state machine."""

    def __init__(self, task_sm: StateMachine, step_id: str):
        self.task_sm = task_sm
        self.step_id = step_id

    def transition(self, new_status: StepStatus, **context):
        step = self._find_step()
        if not step:
            raise ValueError(f"Step {self.step_id} not found in task_tree")
        current = StepStatus(step["status"])
        if new_status not in STEP_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Illegal step transition: {current.value} -> {new_status.value}"
            )
        step["status"] = new_status.value
        step.setdefault("transitions", []).append({
            "from": current.value,
            "to": new_status.value,
            "at": StateMachine._now_iso(),
            "context": context
        })
        self.task_sm._save()

    def _find_step(self) -> dict:
        for step in self.task_sm.state["task_tree"]:
            # 兼容 step_id / id 两种 step 主键 schema
            sid = step.get("step_id", step.get("id"))
            if sid == self.step_id:
                return step
        return None
