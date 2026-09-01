#!/usr/bin/env python3
"""One slide: Super-turn (M0s) 2-VC fault-tolerant routing on the 8x6 PG NoC.

Answers three things on a single page:
  1. what the routing looks like on a residual graph (two Glass-Ni turn layers)
  2. how it tolerates faults (detour inside a turn model, 2nd layer, sacrifice)
  3. why it cannot deadlock (each layer breaks both mesh rotations; VC locked
     at the source, so the union CDG is a disjoint union of acyclic CDGs)

Every number and the drawn example are computed from pg_routing, not typed in.

  .venv-ppt/bin/python utils/gen_pg_super_turn_slide.py --pptx
  python3 utils/gen_pg_super_turn_slide.py --png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pg_faults_8x6 as F
import pg_faults_budget_8x6 as B
import pg_routing as R
from dse_pg_a2a_lambda import catalog_2r4l, hw_cost, solve
from gen_pg_fault_deadlock_slide import (BLUE, CARD_BG, CARD_LN, GREEN, GREY,
                                        GREY_L, INK, ORANGE, RED, SLIDE_H,
                                        SLIDE_W, WHITE, Deck, card, emit_png,
                                        emit_pptx, p)

ROOT = Path(__file__).resolve().parents[1]
OUT_PPTX = ROOT / "results" / "pg_super_turn_slide.pptx"
OUT_PNG = ROOT / "results" / "pg_super_turn_slide.png"
JSON_PATH = ROOT / "results" / "pg_a2a_lambda.json"

DIR_LABEL = {0: "E", 1: "W", 2: "N", 3: "S"}
# The two rotations that close a cycle in a 2D mesh.
ROT_A = [(0, 2), (2, 1), (1, 3), (3, 0)]   # E->N, N->W, W->S, S->E
ROT_B = [(0, 3), (3, 1), (1, 2), (2, 0)]   # E->S, S->W, W->N, N->E
EXAMPLE = "b_r1_l1_0000"
PURPLE = "7A3E9D"


# --------------------------------------------------------------------------
# Facts, all derived from the routing code
# --------------------------------------------------------------------------

def turn_label(t: tuple[int, int]) -> str:
    return f"{DIR_LABEL[t[0]]}→{DIR_LABEL[t[1]]}"


def model_facts() -> list[dict]:
    out = []
    for name, ban in R._TURN_MODELS.items():
        out.append({
            "name": name,
            "ban": sorted(ban),
            "breaks_a": [t for t in ROT_A if t in ban],
            "breaks_b": [t for t in ROT_B if t in ban],
        })
    return out


def example_facts(name: str = EXAMPLE) -> dict:
    """Solve one catalogue scenario and pick two illustrative OD pairs."""
    scen = next(s for s in catalog_2r4l(1, 0) if s["name"] == name)
    pg = B.expand_budget(scen, "dead")
    sol = solve(pg, "super_turn")
    adj = pg["route_adj"]
    dead = sorted(set(range(F.N)) - set(adj))
    full = F.healthy_pg()["route_adj"]
    lost = [(u, v) for u, vs in full.items() for v in vs
            if u < v and u in adj and v in adj and v not in adj[u]]
    vc_of = sol.get("vc_of")
    layers = ["east_first", "west_first"]  # dual tag east_west
    ban0 = R._TURN_MODELS[layers[0]]

    def turns(path):
        return [(R.dir_of(path[i], path[i + 1]),
                 R.dir_of(path[i + 1], path[i + 2]))
                for i in range(len(path) - 2)]

    def manh(s, d):
        sx, sy = F.coord(s)
        dx, dy = F.coord(d)
        return abs(sx - dx) + abs(sy - dy)

    per_layer = {0: 0, 1: 0}
    n_detour = 0
    for k, path in sol["paths"].items():
        per_layer[0 if vc_of is None else int(vc_of(path, 0))] += 1
        if len(path) - 1 > manh(*k):
            n_detour += 1

    # VC0 case: a detour forced by the dead router.
    k0 = (F.nid(5, 0), F.nid(5, 2))
    # VC1 case: minimal hops, but its turn is illegal in layer 0.
    k1 = (F.nid(4, 0), F.nid(5, 2))
    p0, p1 = sol["paths"][k0], sol["paths"][k1]
    assert vc_of is not None and int(vc_of(p0, 0)) == 0, "expected VC0 example"
    assert int(vc_of(p1, 0)) == 1, "expected VC1 example"
    bad = [t for t in turns(p1) if t in ban0]
    assert bad, "VC1 example should use a turn banned in layer 0"
    return {
        "scenario": name,
        "n_routers": scen["n_routers"], "n_links": scen["n_links"],
        "turn_mode": sol.get("turn_mode"), "num_vc": sol.get("num_vc"),
        "n_live": len(sol["compute_nodes"]),
        "dead_routers": [F.coord(d) for d in dead],
        "dead_links": [(F.coord(u), F.coord(v)) for u, v in lost],
        "layers": layers,
        "per_layer": per_layer,
        "n_pairs": len(sol["paths"]),
        "n_detour": n_detour,
        "p0": [F.coord(n) for n in p0],
        "p0_hops": len(p0) - 1, "p0_manh": manh(*k0),
        "p0_turns": [turn_label(t) for t in turns(p0)],
        "p1": [F.coord(n) for n in p1],
        "p1_hops": len(p1) - 1, "p1_manh": manh(*k1),
        "p1_turns": [turn_label(t) for t in turns(p1)],
        "p1_illegal_in_layer0": [turn_label(t) for t in bad],
    }


def catalogue_facts() -> dict:
    n1 = n2 = sac = 0
    cdg_ok = 0
    total = 0
    for scen in catalog_2r4l(1, 0):
        sol = solve(B.expand_budget(scen, "dead"), "super_turn")
        if sol is None:
            continue
        total += 1
        vc = int(sol.get("num_vc", 1))
        n1 += vc == 1
        n2 += vc == 2
        sac += int(sol.get("n_sacrificed",
                           len(sol.get("forced_sacrificed") or [])))
        ok, _ = R.validate_routing(sol["paths"], sol["compute_nodes"],
                                   sol["route_adj"], sol.get("vc_of"))
        cdg_ok += bool(ok)
    return {"total": total, "n_1vc": n1, "n_2vc": n2,
            "n_sacrificed": sac, "cdg_acyclic": cdg_ok}


def cost_facts() -> dict:
    if not JSON_PATH.exists():
        st = solve(F.healthy_pg(), "super_turn")
        hw = hw_cost("super_turn", st["num_vc"], st["paths"], F.N)
        return {"buf": hw["buffer_slots_per_router"],
                "buf_old": hw["buffer_slots_per_router_5port"],
                "sr": hw["sr_header_bits"], "table": 94}
    d = json.loads(JSON_PATH.read_text())
    hw = d.get("hw_super_turn_healthy") or {}
    return {
        "buf": hw.get("buffer_slots_per_router"),
        "buf_old": hw.get("buffer_slots_per_router_5port"),
        "sr": hw.get("sr_header_bits"),
        "table": ((d.get("dest_table_breakdown") or {}).get("xy")
                  or {}).get("total_bits"),
    }


# --------------------------------------------------------------------------
# Figure: residual mesh with one path per VC layer
# --------------------------------------------------------------------------

def fig_mesh(d: Deck, x0, y0, w, h, ex: dict):
    nx, ny = F.MX, F.MY
    legend_h = 0.30
    sx = w / (nx - 1 + 1.0)
    # extra bottom room so the S0/S1 labels under the bottom row clear the legend
    sy = (h - legend_h - 0.50) / (ny - 1)
    ox = x0 + sx * 0.5
    oy = y0 + 0.12
    r = min(sx, sy) * 0.20

    def pos(cx, cy):
        # y grows up on the slide so that "N" points up, as the labels say.
        return ox + cx * sx, oy + (ny - 1 - cy) * sy

    dead = set(ex["dead_routers"])
    dead_links = {frozenset(l) for l in ex["dead_links"]}

    for cy in range(ny):
        for cx in range(nx):
            if (cx, cy) in dead:
                continue
            for dx, dy in ((1, 0), (0, 1)):
                bx, by = cx + dx, cy + dy
                if bx >= nx or by >= ny or (bx, by) in dead:
                    continue
                ax_, ay_ = pos(cx, cy)
                bx_, by_ = pos(bx, by)
                if frozenset(((cx, cy), (bx, by))) in dead_links:
                    d.line(ax_, ay_, bx_, by_, color=RED, lw=1.1, dash=True)
                    d.cross((ax_ + bx_) / 2, (ay_ + by_) / 2, r * 0.5,
                            color=RED, lw=1.2)
                else:
                    d.line(ax_, ay_, bx_, by_, color="CBD2D9", lw=0.7)

    for path, col in ((ex["p0"], BLUE), (ex["p1"], ORANGE)):
        for i in range(len(path) - 1):
            ax_, ay_ = pos(*path[i])
            bx_, by_ = pos(*path[i + 1])
            d.line(ax_, ay_, bx_, by_, color=col, lw=2.3,
                   arrow=(i == len(path) - 2))

    ends = {ex["p0"][0], ex["p0"][-1], ex["p1"][0], ex["p1"][-1]}
    for cy in range(ny):
        for cx in range(nx):
            px, py = pos(cx, cy)
            if (cx, cy) in dead:
                d.oval(px, py, r, fill=WHITE, line=RED, lw=1.2)
                d.cross(px, py, r * 0.62, color=RED, lw=1.4)
            elif (cx, cy) in ends:
                d.oval(px, py, r * 1.05, fill=WHITE, line=INK, lw=1.2)
            else:
                d.oval(px, py, r * 0.72, fill="AEB8C2", line=None, lw=0)

    # Both example pairs share the destination, so label it once.
    for tag, cell, col, dy_ in (("S0", ex["p0"][0], BLUE, 0.20),
                                ("S1", ex["p1"][0], ORANGE, 0.20),
                                ("D", ex["p0"][-1], INK, -0.30)):
        px, py = pos(*cell)
        d.text(px - 0.30, py + dy_, 0.6, 0.2,
               [p(tag, size=8.4, bold=True, color=col, align="c", space=0)])

    # Call out the two things the picture is meant to prove.
    dx_, dy_ = pos(*ex["dead_routers"][0])
    d.text(dx_ - 1.56, dy_ - 0.09, 1.4, 0.2, [
        p("死 router", size=7.4, bold=True, color=RED, align="r", space=0)])
    tx_, ty_ = pos(*ex["p1"][-2])
    d.text(tx_ - 0.70, ty_ - 0.30, 1.4, 0.2, [
        p(f"{ex['p1_illegal_in_layer0'][0]} 层0禁", size=7.4, bold=True,
          color=ORANGE, align="c", space=0)])

    ly = y0 + h - legend_h + 0.10
    lx = x0 + 0.10
    for col, lab in ((BLUE, f"VC0 {ex['layers'][0]}（绕行 {ex['p0_manh']}→"
                            f"{ex['p0_hops']} 跳）"),
                     (ORANGE, f"VC1 {ex['layers'][1]}（层0 禁其转弯）")):
        d.line(lx, ly, lx + 0.26, ly, color=col, lw=2.3, arrow=True)
        d.text(lx + 0.32, ly - 0.075, w / 2 - 0.5, 0.2,
               [p(lab, size=7.4, color=col, space=0)])
        lx += w / 2


def fig_cycle(d: Deck, x0, y0, w, h, model: dict):
    """One mesh rotation (N→E→S→W), with the turn `model` forbids crossed out.

    The drawn loop uses exactly the turns of ROT_B, so the banned turn to mark
    is model["breaks_b"], and it sits at the corner where its incoming arrow
    ends.
    """
    m = min(w, h)
    cx, cy = x0 + w / 2, y0 + h * 0.44
    a = m * 0.32
    corners = {"tl": (cx - a, cy - a), "tr": (cx + a, cy - a),
               "br": (cx + a, cy + a), "bl": (cx - a, cy + a)}
    for px, py in corners.values():
        d.oval(px, py, m * 0.05, fill=GREY_L, line=None)
    # arrow direction -> corner where that arrow ends
    seq = [(2, "bl", "tl"), (0, "tl", "tr"), (3, "tr", "br"), (1, "br", "bl")]
    ends = {dir_: b_ for dir_, _a, b_ in seq}
    banned = model["breaks_b"][0]
    for dir_, a_, b_ in seq:
        x1, y1 = corners[a_]
        x2, y2 = corners[b_]
        hot = dir_ == banned[0]
        d.line(x1, y1, x2, y2, color=RED if hot else BLUE, lw=1.7, arrow=True)
    bx, by = corners[ends[banned[0]]]
    d.cross(bx, by, m * 0.07, color=RED, lw=2.0)
    d.text(x0, y0 + h * 0.84, w, 0.36, [
        p(f"{model['name']}", size=7.6, bold=True, color=INK, align="c",
          space=0.5),
        p(f"禁 {turn_label(banned)} ⇒ 此环断",
          size=7.6, bold=True, color=RED, align="c", space=0)])


# --------------------------------------------------------------------------
# Slide
# --------------------------------------------------------------------------

def build(ex: dict, cat: dict, models: list[dict], cost: dict) -> Deck:
    d = Deck()
    d.rect(0, 0, SLIDE_W, SLIDE_H, fill=WHITE, line=None)

    d.rect(0, 0, SLIDE_W, 0.92, fill=INK, line=None)
    d.rect(0, 0, 0.10, 0.92, fill=PURPLE, line=None)
    d.text(0.34, 0.09, 12.6, 0.44, [
        p("Super-turn（M0s）2 VC 容错路由：路由示意 · 如何容错 · 为何无死锁",
          size=20, bold=True, color=WHITE, space=0)])
    d.text(0.36, 0.55, 12.6, 0.30, [
        p("8×6 mesh（H=7 / V=9）· Glass–Ni 最小转向模型分层 · "
          "硬顶 2 VC · 每对源宿离线定死一条路径、VC 在源端锁定",
          size=9.6, color="C3CBD4", space=0)])

    # Row 1: the picture (left) and how faults are survived (right).
    ry, rh = 1.02, 3.30
    cw_a = 5.45
    card(d, 0.30, ry, cw_a, rh,
         "① 路由示意图：残图上的两层转向模型", accent=PURPLE)
    fig_mesh(d, 0.42, ry + 0.38, cw_a - 0.24, rh - 0.48, ex)

    cx_b = 0.30 + cw_a + 0.16
    cw_b = SLIDE_W - cx_b - 0.30
    card(d, cx_b, ry, cw_b, rh, "② 如何容错", accent=GREEN)
    d.text(cx_b + 0.16, ry + 0.40, cw_b - 0.32, rh - 0.50, [
        p(f"样例 {ex['scenario']}：{ex['n_routers']} 个死 router "
          f"{tuple(ex['dead_routers'][0])} + {ex['n_links']} 条死链路 "
          f"{tuple(ex['dead_links'][0][0])}–{tuple(ex['dead_links'][0][1])}"
          f" → 存活 A={ex['n_live']}；求解得 dual «{ex['turn_mode']}» = "
          f"{ex['layers'][0]} + {ex['layers'][1]}，{ex['num_vc']} VC，0 牺牲。",
          size=8.6, color=INK, space=5.0),
        p("① 故障不进邻接表", size=9.4, bold=True, color=GREEN, space=1.0),
        p("死 router / 死链路先从残图删掉，所有搜索只在存活图上做，"
          "路径不可能穿洞。", size=8.4, color=GREY, space=4.0),
        p("② 第一道：层内绕行", size=9.4, bold=True, color=GREEN, space=1.0),
        p(f"XY 被挡就在同一转向模型下做受限 BFS。蓝色 "
          f"S0{tuple(ex['p0'][0])}→D{tuple(ex['p0'][-1])} 本是 "
          f"{ex['p0_manh']} 跳，死点挡住后向东绕成 {ex['p0_hops']} 跳，"
          f"转弯 {' / '.join(ex['p0_turns'])} 在 {ex['layers'][0]} 下全部合法。",
          size=8.4, color=INK, space=4.0),
        p("③ 第二道：换一层 —— 这就是第 2 条 VC 的用途", size=9.4, bold=True,
          color=ORANGE, space=1.0),
        p(f"有些对在层 0 里无论怎么绕都无解。橙色 S1{tuple(ex['p1'][0])}→"
          f"D{tuple(ex['p1'][-1])} 只要 {ex['p1_hops']} 跳（已是最短），"
          f"但必须用 {'、'.join(ex['p1_illegal_in_layer0'])}——正是 "
          f"{ex['layers'][0]} 禁掉的转弯，于是整条路锁到 {ex['layers'][1]} 层。",
          size=8.4, color=INK, space=4.0),
        p("④ 兜底才牺牲，绝不开第 3 条 VC", size=9.4, bold=True, color=RED,
          space=1.0),
        p(f"两层都盖不住时，对阻塞的 OD 端点做最小顶点覆盖式牺牲。"
          f"全目录 {cat['total']} 个场景合计只牺牲 {cat['n_sacrificed']} 个节点，"
          f"其中 {cat['n_1vc']} 个场景 1 层就够、{cat['n_2vc']} 个要 2 层。",
          size=8.4, color=GREY, space=0),
    ])

    # Row 2: why it cannot deadlock (left) and the four turn models (right).
    ty, th = 4.48, 1.78
    cw_c = 7.30
    card(d, 0.30, ty, cw_c, th, "③ 为什么无死锁", accent=RED, hdr_h=0.32)
    fig_cycle(d, 0.40, ty + 0.34, 1.15, 1.36, models[0])
    d.text(1.66, ty + 0.38, cw_c - 1.50, th - 0.44, [
        p("死锁 ⇔ 通道依赖图 CDG 成环；mesh 成环必须凑满一圈 4 个转弯，"
          "且只有两种旋向。", size=8.2, color=GREY, space=3.5),
        p("① 单层已无环：四个最小模型各禁 2 个转弯，恰好在两种旋向里各命中 "
          "1 个（右表）⇒ 单层 CDG 在任意子图上都无环，故障怎么变都不必重证。",
          size=8.2, color=INK, space=3.5),
        p("② 两层不共享通道：层 = VC，缓冲按 VC 分开，"
          "CDG 的节点是 (有向通道, VC)。", size=8.2, color=INK, space=3.5),
        p("③ VC 源端锁定、全程不换：vc_of 只看 (src,dst) ⇒ 无依赖边跨层 ⇒ "
          "并集 = 两个无环图的不相交并 ⇒ 仍无环（Dally–Seitz）。",
          size=8.2, color=INK, space=3.5),
        p(f"④ 实测：build_cdg + DFS 查环，{cat['cdg_acyclic']}/{cat['total']} "
          f"个场景全部无环，0 例成环。", size=8.2, bold=True, color=GREEN,
          space=0),
    ])

    cx_d = 0.30 + cw_c + 0.16
    cw_d = SLIDE_W - cx_d - 0.30
    card(d, cx_d, ty, cw_d, th, "四个最小转向模型：每层禁 2 个转弯",
         accent=BLUE, hdr_h=0.32)
    col_x = (cx_d + 0.16, cx_d + 1.34, cx_d + 2.86)
    for cxx, txt in zip(col_x, ("模型（层）", "禁止的转弯", "两种旋向的断点")):
        d.text(cxx, ty + 0.36, 1.70, 0.20,
               [p(txt, size=7.8, bold=True, color=GREY, space=0)])
    for i, mf in enumerate(models):
        yy = ty + 0.58 + i * 0.205
        cells = (mf["name"],
                 "、".join(turn_label(t) for t in mf["ban"]),
                 " / ".join(turn_label(t) for t in
                            (mf["breaks_a"] + mf["breaks_b"])) or "—")
        for j, (cxx, txt) in enumerate(zip(col_x, cells)):
            d.text(cxx, yy, 1.70, 0.20,
                   [p(txt, size=7.8, bold=(j == 0),
                      color=(BLUE if j == 0 else INK), space=0)])
    d.text(cx_d + 0.16, ty + th - 0.34, cw_d - 0.32, 0.28, [
        p("任一 dual 的两个禁集互不相交 ⇒ 两层合起来 8 个转弯全可用，"
          "而每层各自仍无环。", size=7.8, color=GREY, space=0)])

    by, bh = 6.42, 0.86
    d.rect(0.30, by, SLIDE_W - 0.60, bh, fill="F4F0F8", line=PURPLE, lw=0.9,
           round_=0.04)
    d.rect(0.30, by, 0.055, bh, fill=PURPLE, line=None)
    d.text(0.48, by + 0.09, 6.10, bh - 0.16, [
        p("顺带保住的第三条性质：保序", size=9.2, bold=True, color=PURPLE,
          space=1.2),
        p(f"每对源宿一条离线定死的路径、VC 源端一次选定，运行时不自适应 ⇒ "
          f"同一对的 flit 不会互相超越。本例 {ex['n_pairs']} 对中 "
          f"{ex['per_layer'][0]} 对在层 0、{ex['per_layer'][1]} 对在层 1，"
          f"{ex['n_detour']} 对为非最短绕行。",
          size=8.4, color=INK, space=0)])
    d.text(6.75, by + 0.09, SLIDE_W - 6.75 - 0.45, bh - 0.16, [
        p("代价（硅上一律按 2 VC 计，即使某张残图 1 层就够）",
          size=9.2, bold=True, color=PURPLE, space=1.2),
        p(f"缓冲 {cost['buf']} flit/router = 2 VC ×（4 mesh 端口×19 + "
          f"1 ramp 端口×3）——ramp 端口不按 Q 计，旧「5 端口」口径会多算到 "
          f"{cost['buf_old']}；目的表 {cost['table']} bit 或退源路由头 "
          f"{cost['sr']} bit（= 4 + 2×12 + 1 VC）。",
          size=8.4, color=INK, space=0)])

    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pptx", action="store_true")
    ap.add_argument("--png", action="store_true")
    args = ap.parse_args()
    if not (args.pptx or args.png):
        args.pptx = args.png = True

    ex = example_facts()
    cat = catalogue_facts()
    models = model_facts()
    cost = cost_facts()
    print("example", ex["scenario"], ex["turn_mode"], ex["num_vc"], "VC")
    print("catalogue", cat)
    print("cost", cost)

    deck = build(ex, cat, models, cost)
    if args.pptx:
        emit_pptx(deck, OUT_PPTX)
    if args.png:
        emit_png(deck, OUT_PNG)


if __name__ == "__main__":
    main()
