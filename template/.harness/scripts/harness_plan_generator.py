#!/usr/bin/env python3
"""根据用户目标生成 plan.md 骨架 / Generate plan.md skeleton from objective.

LLM 启动新任务时调用此脚本，自动生成结构化 plan.md，LLM 只需填写
<待补充> 占位符即可，避免从零撰写模板的机械性工作。

Usage:
    python harness_plan_generator.py "修复登录bug"
    python harness_plan_generator.py "重构支付模块" --scopes src/payment/ tests/payment/
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime, timezone


def generate_plan(objective: str, scopes: list = None,
                  task_id: str = None) -> str:
    """根据目标生成 plan / Generate plan from objective."""

    if not task_id:
        state_path = Path(".harness/state.json")
        if state_path.exists():
            state = json.load(state_path.open())
            task_id = state.get("task_id", "TASK-XXX")
        else:
            task_id = "TASK-XXX"

    # 自动检测涉及的文件类型
    scopes = scopes or _auto_detect_scopes(objective)

    # 自动检测风险信号
    risks = _detect_risks(objective)

    # 生成任务树建议
    steps = _suggest_steps(objective, risks)

    plan = f"""# Task Plan: {task_id}

## 任务目标 / Objective
{objective}

## 任务树 / Task Tree
{chr(10).join(f"- [ ] {i+1}. {s}" for i, s in enumerate(steps))}

## 影响范围 / Impact Scope
- 文件 / Files: <待 LLM 补充>
- 模块 / Modules: <待 LLM 补充>
- 数据 / Data: <待 LLM 补充>
- 外部依赖 / External Deps: <待 LLM 补充>

## 风险评估 / Risk Assessment
| Step | 风险等级 | 风险描述 | 缓解措施 |
|---|---|---|---|
{chr(10).join(f"| {i+1} | {r['level']} | {r['desc']} | {r['mitigation']} |" for i, r in enumerate(risks))}

## 回退预案 / Rollback Plan
- 触发条件: 测试失败 / 编译错误率 > 10% / 连续 3 次失败
- 回退目标: checkpoint/{task_id}/<step-id>
- 备选方向: <待 LLM 补充>

## 证据需求 / Evidence Requirements
- 需要用户提供的: <待 LLM 补充>
- 需要读取的: <待 LLM 补充>
- 需要执行的: pytest -v, mypy, ruff

## 验收标准 / Acceptance Criteria
- [ ] 所有测试通过
- [ ] 无 lint 错误
- [ ] <待 LLM 补充业务条件>

## 用户授权 / User Authorization
- [ ] APPROVED by <user> at <timestamp>
- 授权范围 / Authorized Scopes: {scopes}

## Revision History

| 版本 | 时间 | 修改者 | 修改内容 | 触发反馈ID | 影响范围 |
|---|---|---|---|---|---|
| v1.0 | {datetime.now(timezone.utc).isoformat()} | harness_plan_generator | 初始版本 | - | - |
"""
    return plan


def _auto_detect_scopes(objective: str) -> list:
    """根据目标关键词推测 scopes / Infer scopes from objective keywords."""
    scopes = []
    keywords_map = {
        "auth": "src/auth/", "login": "src/auth/",
        "payment": "src/payment/", "order": "src/order/",
        "user": "src/users/", "api": "src/api/",
        "test": "tests/", "frontend": "src/components/",
        "backend": "src/", "db": "prisma/", "database": "prisma/",
    }
    obj_lower = objective.lower()
    for kw, scope in keywords_map.items():
        if kw in obj_lower and scope not in scopes:
            scopes.append(scope)
    return scopes or ["src/"]


def _detect_risks(objective: str) -> list:
    """检测目标中的风险信号 / Detect risk signals in objective."""
    risks = []
    obj_lower = objective.lower()

    risk_patterns = [
        (r"migrat|迁移|升级", "L2", "涉及数据/代码迁移", "备份 + 分阶段验证"),
        (r"deploy|部署|release", "L2", "涉及部署", "staging 验证 + 回滚预案"),
        (r"drop|delete|remove|删除", "L3", "涉及删除操作", "用户确认 + 备份"),
        (r"production|prod|生产", "L2", "涉及生产环境", "staging 验证 + 用户确认"),
        (r"database|db|schema|sql", "L2", "涉及数据库", "迁移脚本 + 回滚"),
        (r"auth|password|token|secret", "L2", "涉及认证敏感信息", "用户确认 + 不落盘"),
        (r"refactor|重构", "L1", "重构可能影响行为", "测试覆盖 + 行为对比"),
        (r"fix|bug|修复", "L1", "Bug 修复", "回归测试"),
    ]

    for pat, level, desc, mit in risk_patterns:
        if re.search(pat, obj_lower):
            risks.append({"level": level, "desc": desc, "mitigation": mit})

    return risks or [{"level": "L1", "desc": "一般代码修改",
                      "mitigation": "单元测试覆盖"}]


def _suggest_steps(objective: str, risks: list) -> list:
    """根据目标建议任务树 / Suggest task tree from objective."""
    # 通用步骤模板
    steps = [
        "1. 读取相关代码与测试，建立理解",
        "2. 实现核心修改",
        "3. 添加/更新测试",
        "4. 运行测试套件验证",
        "5. 生成 summary 与归档证据",
    ]
    # 根据风险追加步骤
    for r in risks:
        if "迁移" in r["desc"]:
            steps.insert(2, "2.5. 创建迁移脚本并验证")
        if "数据库" in r["desc"]:
            steps.insert(2, "2.5. 修改 prisma schema")
        if "生产" in r["desc"]:
            steps.append("6. staging 环境验证")
    # 重新编号
    result = []
    for i, s in enumerate(steps, 1):
        # 去掉原编号
        s = re.sub(r"^\d+\.\s*", "", s)
        result.append(f"{i}. {s}")
    return result


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Generate plan.md skeleton")
    p.add_argument("objective", help="Task objective")
    p.add_argument("--scopes", nargs="*", default=[])
    p.add_argument("--task-id")
    p.add_argument("--output", default="plan.md")
    args = p.parse_args()
    plan = generate_plan(args.objective, args.scopes, args.task_id)
    Path(args.output).write_text(plan)
    print(f"[HARNESS][OK] Plan generated: {args.output}")
    print(f"  Next: LLM reviews & fills in <待补充> placeholders")
