#!/usr/bin/env python3
"""验证 state.json 的一致性 / Validate state.json consistency.

Usage:
    python harness_validate_state.py
    python harness_validate_state.py /path/to/state.json
"""

import json
import sys


def validate(state_path: str = "state.json") -> list:
    """返回问题列表，空列表表示通过 / Return issues list."""
    state = json.load(open(state_path))
    issues = []

    # 1. 必填字段（current_step 允许 None = 无进行中 step）
    required = ["task_id", "status", "project_root", "authorized_scopes",
                "baseline", "task_tree"]
    for field in required:
        if field not in state or state[field] is None:
            issues.append(f"Missing required field: {field}")
    if "current_step" not in state:
        issues.append("Missing required field: current_step")

    # 2. status 合法值
    valid_statuses = {"CREATED", "PLANNING", "PENDING_AUTH", "IN_PROGRESS",
                      "PAUSED", "ROLLING_BACK", "ABORTING", "ABORTED",
                      "SUMMARIZING", "COMPLETED", "FAILED"}
    if state.get("status") not in valid_statuses:
        issues.append(f"Invalid status: {state.get('status')}")

    # 3. current_step 必须在 task_tree 中
    if state.get("current_step"):
        # 兼容 step_id / id 两种 step 主键 schema
        step_ids = [s.get("step_id", s.get("id")) for s in state.get("task_tree", [])]
        if state["current_step"] not in step_ids:
            issues.append(
                f"current_step {state['current_step']} not in task_tree"
            )

    # 4. baseline 完整性
    baseline = state.get("baseline", {})
    if baseline:
        if not baseline.get("commit"):
            issues.append("baseline.commit missing")
        if not baseline.get("tag"):
            issues.append("baseline.tag missing")

    # 5. authorized_scopes 必须以 / 结尾或明确文件
    for scope in state.get("authorized_scopes", []):
        if not scope.endswith("/") and "." not in scope:
            issues.append(
                f"Ambiguous scope: {scope} "
                "(should end with / or contain file extension)"
            )

    # 6. tried_directions 格式
    for d in state.get("tried_directions", []):
        for field in ("direction", "attempted_at", "rolled_back_at",
                      "trigger", "evidence"):
            if field not in d:
                issues.append(f"tried_directions entry missing {field}: {d}")

    # 7. user_authorization
    auth = state.get("user_authorization", {})
    if state.get("status") == "IN_PROGRESS":
        if not auth.get("approved_at"):
            issues.append("IN_PROGRESS without user_authorization.approved_at")
        if not auth.get("approved_by"):
            issues.append("IN_PROGRESS without user_authorization.approved_by")

    # 8. frozen 一致性
    if state.get("frozen") and state.get("status") != "COMPLETED":
        issues.append(f"frozen=true but status={state.get('status')}")

    return issues


if __name__ == "__main__":
    issues = validate(sys.argv[1] if len(sys.argv) > 1 else "state.json")
    if issues:
        print("[HARNESS][FAIL] State validation failed:")
        for i in issues:
            print(f"  - {i}")
        sys.exit(1)
    print("[HARNESS][OK] State validation passed.")
