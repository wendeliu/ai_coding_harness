# -*- coding: utf-8 -*-
"""Harness 两级状态机转移图（有向图）。改下方 N/E 表后重跑即可重新出图。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import ConnectionStyle, FancyArrowPatch
from matplotlib.path import Path
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK HK", "Droid Sans Fallback"]
plt.rcParams["axes.unicode_minus"] = False

NODE = dict(bbox=dict(boxstyle="round,pad=0.35", fc="#eef3fb", ec="#2c5f9e", lw=1.7),
            fontsize=13, ha="center", va="center", weight="bold")
TERMINAL = dict(bbox=dict(boxstyle="round,pad=0.35", fc="#f7ece9", ec="#b04a3a", lw=2.4),
                fontsize=13, ha="center", va="center", weight="bold")
EDGE = dict(arrowstyle="-|>,head_width=0.38,head_length=0.75", mutation_scale=20,
            color="#333", lw=2.0, shrinkA=7, shrinkB=7, zorder=4)
BACK = dict(arrowstyle="-|>,head_width=0.38,head_length=0.75", mutation_scale=20,
            color="#7a6fb0", lw=2.0, shrinkA=7, shrinkB=7, zorder=4)
GRAY = dict(arrowstyle="-|>,head_width=0.38,head_length=0.75", mutation_scale=20,
            color="#607e95", lw=1.6, ls=(0,(5,3)), shrinkA=7, shrinkB=7, zorder=4)
LB = dict(fontsize=10.2, color="#111", ha="center", va="center",
          bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#bbb", lw=0.7, alpha=0.96))
LBB = dict(fontsize=10.2, color="#4a3f8f", ha="center", va="center",
           bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="#b0a8dd", lw=0.7, alpha=0.96))
LGG = dict(fontsize=9.6, color="#5a7386", ha="center", va="center",
           bbox=dict(boxstyle="round,pad=0.2", fc="white", ec="#c6d4de", lw=0.6, alpha=0.96))

def _edge_path(ax, start, end, source_box, target_box, rad):
    curve = ConnectionStyle.Arc3(rad=rad).connect(
        ax.transData.transform(start), ax.transData.transform(end))
    a, c, b = curve.vertices

    def point(t):
        return (1-t)**2*a + 2*t*(1-t)*c + t**2*b

    # 在屏幕坐标中裁剪曲线，给目标边框外的完整箭头留出 8pt 空隙。
    margin = ax.figure.dpi * 8 / 72
    source = source_box.get_window_extent().padded(margin)
    target = target_box.get_window_extent().padded(margin)
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo+hi)/2
        if source.contains(*point(mid)):
            lo = mid
        else:
            hi = mid
    t0 = (lo+hi)/2
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo+hi)/2
        if target.contains(*point(mid)):
            hi = mid
        else:
            lo = mid
    t1 = (lo+hi)/2
    p0, p2 = point(t0), point(t1)
    p1 = p0 + (t1-t0)*((1-t0)*(c-a) + t0*(b-c))
    vertices = ax.transData.inverted().transform([p0, p1, p2])
    return Path(vertices, [Path.MOVETO, Path.CURVE3, Path.CURVE3])


def draw(path, nodes, edges, foot, title, xlim, ylim):
    fig, ax = plt.subplots(figsize=(22, 12.4), dpi=140)
    ax.set_xlim(0, xlim); ax.set_ylim(0.2, ylim); ax.axis("off")
    node_boxes = {}
    for name,(x,y,style) in nodes.items():
        text = ax.text(x, y, name, zorder=6, **(NODE if style=="n" else TERMINAL))
        node_boxes[name] = text.get_bbox_patch()
    # 先确定节点框的实际大小，连线裁剪到边框外，避免箭头被节点文字遮住。
    fig.canvas.draw()
    for e in edges:
        a,b,lab,kind,rad = e[:5]
        lx,ly = e[5]
        curve = _edge_path(ax, nodes[a][:2], nodes[b][:2],
                           node_boxes[a], node_boxes[b], rad)
        ax.add_patch(FancyArrowPatch(path=curve,
                     **(EDGE if kind=="f" else BACK if kind=="b" else GRAY)))
        if lab:
            ax.text(lx, ly, lab, zorder=5,
                    **(LB if kind=="f" else LBB if kind=="b" else LGG))
    ax.text(xlim*0.012, 0.4, foot, fontsize=10.2, va="bottom", ha="left",
            linespacing=1.7,
            bbox=dict(boxstyle="round,pad=0.5", fc="#f4f4ee", ec="#999", lw=0.9))
    ax.set_title(title, fontsize=16, weight="bold", pad=14)
    fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="white")
    fig.savefig(path.rsplit(".", 1)[0] + ".svg", bbox_inches="tight", facecolor="white")
    plt.close(fig)

# ================= Task 级 =================
NT = {
 "CREATED":(1.8,9.5,"n"), "PLANNING":(5.8,9.5,"n"), "PENDING_AUTH":(9.8,9.5,"n"),
 "IN_PROGRESS":(13.8,9.5,"n"), "SUMMARIZING":(18.6,9.5,"n"),
 "PAUSED":(13.8,12.1,"n"), "ROLLING_BACK":(18.6,12.1,"n"),
 "FAILED":(13.8,7.2,"t"), "COMPLETED":(18.6,7.2,"t"),
 "ABORTING":(9.8,7.2,"n"), "ABORTED":(5.8,7.2,"t"),
}
ET = [
 ("CREATED","PLANNING","init（自动；--force 可重置）","f",0,(3.8,10.15)),
 ("PLANNING","PENDING_AUTH","approve：TTY 口令 + dev 分支 + 对账净","f",0,(7.8,10.45)),
 ("PENDING_AUTH","IN_PROGRESS","step start（首个）：无 open_step + sha 一致","f",0,(11.8,10.45)),
 ("IN_PROGRESS","SUMMARIZING","complete：无 open_step + 有 last_step + 对账净","f",0,(16.2,10.3)),
 ("SUMMARIZING","COMPLETED","自动（随后 merge：pending MR + 一次性 token）","f",0.30,(20.6,8.3)),
 ("SUMMARIZING","IN_PROGRESS","复核打回","b",0.30,(16.0,7.6)),
 ("IN_PROGRESS","PAUSED","暂停","f",0.05,(14.6,10.8)),
 ("PAUSED","IN_PROGRESS","恢复","f",0.05,(13.0,10.8)),
 ("PAUSED","ROLLING_BACK","回退","f",0,(16.2,12.6)),
 ("ROLLING_BACK","IN_PROGRESS","回退完成","b",0.22,(16.9,11.2)),
 ("ROLLING_BACK","FAILED","回退失败","b",0.30,(17.4,8.7)),
 ("IN_PROGRESS","FAILED","致命错误（commit 失败等）","f",0,(13.0,8.3)),
 ("IN_PROGRESS","PLANNING","在途改计划：replan / publish plan.md / approve","b",0.32,(9.0,7.9)),
 ("PENDING_AUTH","PLANNING","publish plan.md / replan（批准作废）","b",0.28,(7.0,8.75)),
 ("IN_PROGRESS","ABORTING","abort","g",0.15,(12.3,7.95)),
 ("PAUSED","ABORTING","abort","g",0.18,(11.6,8.85)),
 ("ROLLING_BACK","ABORTING","abort","g",0.20,(15.4,8.0)),
 ("SUMMARIZING","ABORTING","abort","g",0.15,(14.2,8.55)),
 ("CREATED","ABORTING","abort","g",0.18,(4.0,8.35)),
 ("ABORTING","ABORTED","abort：口令 + checkpoint tag + 确认后 reset --hard（自动）","f",0,(7.8,6.7)),
]
footT = ("通用于任何活跃状态 → ABORTING 的 abort 守卫：用户在自己终端输口令(TTY) + 存在 checkpoint tag(harness-baseline / harness-s*) + 交互确认\n"
         "终态 COMPLETED / ABORTED / FAILED 无出边（只读：status / log / inspect / validate / evidence / replan list）；唯一出路：init --force 重启一轮 → CREATED\n"
         "在途改计划（replan / publish plan.md / approve）会作废原批准（plan_approved=false）并退回 PLANNING 重新签字\n"
         "命令闸门 COMMAND_STATE_POLICY：step start ∈ {PENDING_AUTH, IN_PROGRESS(幂等 no-op)}；verify ∈ {IN_PROGRESS, PAUSED}；merge ∈ {SUMMARIZING, COMPLETED}；complete 仅 IN_PROGRESS")
draw("docs/state-machine-task.png", NT, ET, footT,
     "Task 级状态机（有向图，边上为转移条件）｜实线=前进  虚紫=回退/重签  虚灰=abort", 22.5, 13.4)

# ================= Step 级 =================
NS = {
 "PLANNED":(1.8,9.5,"n"), "CHECKPOINTING":(6.2,9.5,"n"), "EXECUTING":(10.8,9.5,"n"),
 "COMPLIANCE_REVIEW":(15.6,9.5,"n"), "COMMITTING":(20.2,9.5,"n"),
 "ROLLING_BACK":(6.2,7.2,"n"), "FAILED":(10.8,7.2,"t"),
 "VERIFYING":(15.6,7.2,"n"), "COMPLETED":(20.2,7.2,"t"),
 "PAUSED":(10.8,12.1,"n"), "BLOCKED":(15.6,12.1,"n"),
}
ES = [
 ("PLANNED","CHECKPOINTING","step start：无 open_step + plan sha 一致 + dev 分支","f",0,(4.0,10.3)),
 ("CHECKPOINTING","EXECUTING","通过 checkpoint（自动）","f",0,(8.5,10.15)),
 ("EXECUTING","COMPLIANCE_REVIEW","step done：step id 一致 + -m 非空 + reconcile 对账净","f",0,(13.2,10.45)),
 ("COMPLIANCE_REVIEW","COMMITTING","合规检查通过（自动）","f",0,(17.9,10.15)),
 ("COMPLIANCE_REVIEW","EXECUTING","打回整改","b",0.10,(13.2,9.15)),
 ("COMPLIANCE_REVIEW","BLOCKED","命中模糊点","f",0,(15.6,11.0)),
 ("BLOCKED","EXECUTING","咨询后继续","b",0.22,(12.4,11.3)),
 ("COMMITTING","VERIFYING","（自动）","f",0.22,(18.6,8.35)),
 ("VERIFYING","COMPLETED","验收：harness 亲自执行 exit=0 证据 + 二次提交 token（或 --no-verify --reason）","f",0,(17.0,6.6)),
 ("VERIFYING","EXECUTING","验收失败返工","b",0.25,(12.6,8.3)),
 ("EXECUTING","PAUSED","暂停","f",0.05,(11.7,10.8)),
 ("PAUSED","EXECUTING","恢复","f",0.05,(9.9,10.8)),
 ("EXECUTING","ROLLING_BACK","回退","f",0.15,(7.9,8.5)),
 ("PAUSED","ROLLING_BACK","回退","f",0.12,(7.6,9.7)),
 ("ROLLING_BACK","PLANNED","回退完成，重排后重走","b",0.35,(3.2,8.1)),
 ("EXECUTING","FAILED","致命错误","f",0.10,(10.0,8.35)),
 ("PLANNED","FAILED","失败","g",0.18,(5.0,8.15)),
 ("CHECKPOINTING","FAILED","失败","g",0.12,(8.0,7.6)),
 ("COMMITTING","FAILED","commit 失败","g",0.18,(16.8,7.75)),
 ("VERIFYING","ROLLING_BACK","回退","g",0.25,(10.8,5.9)),
]
footS = ("Step 状态由 step start / step done 内部自动串联推进，AI 不能停留在中间态：\n"
         "   step start = PLANNED→CHECKPOINTING→EXECUTING；   step done = EXECUTING→COMPLIANCE_REVIEW→COMMITTING→VERIFYING→COMPLETED\n"
         "与 Task 级的耦合：step COMPLETE 成功时自动对账 + commit + 打 harness-s<id> tag —— 该 tag 正是 abort / replan 的 checkpoint 检查依据")
draw("docs/state-machine-step.png", NS, ES, footS,
     "Step 级状态机（有向图，边上为转移条件）｜实线=前进  虚紫=返工/回退  虚灰=失败/次要路径", 22.5, 13.4)
print("saved")
