#!/usr/bin/env python3
"""Checks on results/write_dbid_8x6.json. Every check names the quantity that
failed. Writes results/verify_write_dbid_8x6.json and exits non-zero on failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = json.loads((ROOT / "results" / "write_dbid_8x6.json").read_text())
OLD = json.loads((ROOT / "results" / "load_sweep_8x6.json").read_text())
rows = D["rows"]
checks: list[dict] = []


def check(name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + " :: " + detail)


def sel(cfg, group, **kw):
    return {r["lam"]: r for r in rows if r["config"] == cfg
            and r["group"] == group
            and all(r.get(k) == v for k, v in kw.items())}


# 1. HA in-flight DBIDs never exceed K
bad = [(r["config"], r["lam"], r["dbid_k"], r["ha_out_max"]) for r in rows
       if r["config"].endswith("_dbid") and r["dbid_k"]
       and r["ha_out_max"] > r["dbid_k"]]
n = sum(1 for r in rows if r["config"].endswith("_dbid") and r["dbid_k"])
check("HA 在途 DBID ≤ K", not bad, f"{n} 行检查，违规 {bad[:3]}")

# 2. lambda* never above the analytic anchor (stable region only)
for fab, key in (("mesh", "mesh_xy"), ("ring", "ring_fixed")):
    a = D["anchors"][key]
    for sch in ("base", "islip2d", "dbid"):
        rs = [r for r in rows if r["config"] == f"{fab}_{sch}"
              and r["group"] == "main" and r["stable_gp"]]
        ls = max(r["lam"] for r in rs) if rs else 0
        check(f"{fab}_{sch} λ*≤解析上限", ls <= a + 1e-9,
              f"λ*={ls} 上限={a:.3f}")

# 3. stable points deliver what was offered (goodput ratio) and never exceed it
over = [(r["config"], r["lam"], r["goodput"]) for r in rows
        if r["m"] == 1 and r["goodput"] > r["lam"] * 1.03]
check("goodput 不超过注入率(+3% 统计容差)", not over, f"违规 {over[:3]}")

# 4. unbounded DBID == baseline + round trip: same goodput within noise
for fab in ("mesh", "ring"):
    b = sel(f"{fab}_base", "main")
    c = sel(f"{fab}_dbid", "control")
    worst = max(abs(b[l]["goodput"] - c[l]["goodput"]) for l in b if l in c)
    wl = max((l for l in b if l in c),
             key=lambda l: abs(b[l]["goodput"] - c[l]["goodput"]))
    check(f"{fab} K=∞ 与基线 goodput 一致", worst <= 0.02,
          f"最大差 {worst:.4f} @λ={wl}")
    lo = min(b)
    d_lat = c[lo]["p50"] - b[lo]["p50"]
    check(f"{fab} K=∞ 在低载只多一个往返", d_lat > 0,
          f"λ={lo}: p50 {b[lo]['p50']:.0f} → {c[lo]['p50']:.0f} (+{d_lat:.0f})")

# 5. baseline / centralized points reproduce the earlier load sweep
old = {(r["config"], r["lam"]): r for r in OLD["rows"]
       if r.get("group") == "main"}
for cfg in ("mesh_base", "mesh_islip2d", "ring_base", "ring_islip2d"):
    diffs = []
    for r in rows:
        if r["config"] == cfg and r["group"] == "main":
            o = old.get((cfg, r["lam"]))
            if o and o["warmup"] == r["warmup"] and o["measure"] == r["measure"]:
                diffs.append(abs(o["accepted"] - r["accepted"]))
    check(f"{cfg} 与既有 load_sweep 对齐", diffs and max(diffs) < 1e-4,
          f"{len(diffs)} 点，最大 |Δaccepted|={max(diffs) if diffs else None}")

# 6. each DBID carries two control messages
r = [x for x in rows if x["config"] == "mesh_dbid" and x["group"] == "main"
     and x["lam"] == 0.3][0]
check("控制消息 = 2×grant", abs(r["ctrl_msgs_per_cy"]
      - 2 * r["goodput"] * 48) / max(r["ctrl_msgs_per_cy"], 1e-9) < 0.25,
      f"ctrl_msgs_per_cy={r['ctrl_msgs_per_cy']} goodput*48*2="
      f"{2 * r['goodput'] * 48:.2f}")

(ROOT / "results" / "verify_write_dbid_8x6.json").write_text(
    json.dumps(checks, indent=1, ensure_ascii=False))
fails = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} 通过")
sys.exit(1 if fails else 0)
