#!/usr/bin/env python3
"""Checks on results/write_homa_8x6.json. Every check names the quantity that
failed. Writes results/verify_write_homa_8x6.json, exits non-zero on failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = json.loads((ROOT / "results" / "write_homa_8x6.json").read_text())
rows = D["rows"]
checks: list[dict] = []


def check(name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS " if ok else "FAIL ") + name + " :: " + detail)


def sel(cfg, group, **kw):
    return {r["lam"]: r for r in rows if r["config"] == cfg
            and r["group"] == group and all(r.get(k) == v for k, v in kw.items())}


homa = [r for r in rows if r["config"].endswith("_homa")]

# 1. per-message grant window respected
bad = [(r["config"], r["lam"], r["msg_inflight_max"], r["homa_window"])
       for r in homa if r["msg_inflight_max"] > r["homa_window"]]
check("每条消息 granted-未收 ≤ 窗口 W", not bad,
      f"{len(homa)} 行检查，违规 {bad[:3]}")

# 2. no unscheduled prefix => every packet is granted
z = [r for r in homa if r["group"] == "nounsched"]
check("unsched=0 时无 unscheduled 包", all(r["unsched_frac"] == 0 for r in z),
      f"{len(z)} 行，unsched_frac={[r['unsched_frac'] for r in z][:3]}")

# 3. control messages = scheduled packets
errs = []
for r in homa:
    if r["group"] == "main" and r["stable_gp"] and r["lam"] >= 0.1:
        want = (1 - r["unsched_frac"]) * r["goodput"] * 48
        errs.append(abs(r["ctrl_msgs_per_cy"] - want) / max(want, 1e-9))
check("grant 数 = 非 unscheduled 包数", errs and max(errs) < 0.15,
      f"{len(errs)} 个稳定点，最大相对误差 {max(errs):.3f}")

# 4. single-packet workload: Homa degenerates to the baseline
for fab in ("mesh", "ring"):
    b = sel(f"{fab}_base", "single")
    h = sel(f"{fab}_homa", "single")
    worst = max(abs(b[l]["goodput"] - h[l]["goodput"]) for l in b)
    dp = max(abs(b[l]["p50"] - h[l]["p50"]) for l in b if l <= 0.3)
    check(f"{fab} 单包负载下 Homa 退化为基线", worst <= 0.002 and dp <= 1,
          f"goodput 最大差 {worst:.4f}，低载 p50 最大差 {dp:.1f}")
    check(f"{fab} 单包负载下无 grant", all(h[l]["n_grants"] == 0 for l in h),
          f"n_grants={sorted({h[l]['n_grants'] for l in h})}")

# 5. stable points never above the analytic anchor; goodput ≤ offered
for fab, key in (("mesh", "mesh_xy"), ("ring", "ring_fixed")):
    a = D["anchors"][key]
    for sch, cfg, grp in (("base", "base", "main"), ("central", "islip2d", "main"),
                          ("homa", "homa", "main"), ("pool", "dbid", "pool")):
        rs = [r for r in rows if r["config"] == f"{fab}_{cfg}"
              and r["group"] == grp and r["stable_gp"]]
        ls = max(r["lam"] for r in rs) if rs else 0
        check(f"{fab}_{sch} λ*≤解析上限", ls <= a + 1e-9,
              f"λ*={ls} 上限={a:.3f}")
over = [(r["config"], r["lam"], r["goodput"]) for r in rows
        if r["goodput"] > r["lam"] * 1.05]
check("goodput 不超过注入率(+5%)", not over, f"违规 {over[:3]}")

# 6. FCT bookkeeping: every message class present, short <= long at low load
for fab in ("mesh", "ring"):
    for cfg in ("base", "islip2d", "homa"):
        r = sel(f"{fab}_{cfg}", "main")[0.05]
        ok = r["fct_mean_L1"] <= r["fct_mean_L8"] <= r["fct_mean_L64"]
        check(f"{fab}_{cfg} 低载 FCT 随消息长度单调", ok,
              f"L1/L8/L64 = {r['fct_mean_L1']}/{r['fct_mean_L8']}/"
              f"{r['fct_mean_L64']}")

# 7. SRPT helps the shorter scheduled class when sizes compete for grants
for fab in ("mesh", "ring"):
    s = sel(f"{fab}_homa", "srpt_w2")
    f = sel(f"{fab}_homa", "fcfs_w2")
    lam = max(s)
    check(f"{fab} SRPT 中等消息(8 包)FCT ≤ FCFS（W=2, D=4, λ={lam}）",
          s[lam]["fct_mean_L8"] <= f[lam]["fct_mean_L8"],
          f"SRPT {s[lam]['fct_mean_L8']} vs FCFS {f[lam]['fct_mean_L8']}")

(ROOT / "results" / "verify_write_homa_8x6.json").write_text(
    json.dumps(checks, indent=1, ensure_ascii=False))
fails = [c for c in checks if not c["ok"]]
print(f"\n{len(checks) - len(fails)}/{len(checks)} 通过")
sys.exit(1 if fails else 0)
