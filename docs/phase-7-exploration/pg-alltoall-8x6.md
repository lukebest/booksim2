# 8×6 分组交换 NoC：Partial-Good 下 Alltoall 解决方案与劣化率

**几何：** 8×6 mesh，H=7，V=9，RAMP=2，RAMP_BW=2  
**流量：** 一次性 alltoall；m∈{1,5}；同源同目的 wormhole 保序  
**故障模型：** ring_report 的 link + node（不含 quadrant）；corner 链路在 `(0,0)` 入射边  
**产物：** `results/pg_alltoall_8x6.json`，`results/report_pg_alltoall_8x6.html`

## 1. 硬约束

无死锁（CDG）+ 保序（每对唯一路径，VC 序列为 `(src,dst)` 确定性函数）。不满足时可牺牲 good 节点（升序基数搜索；先剔除度 0 孤立点）。

## 2. 方案

### A 类 · 转向限制（1 VC）

M1 XY · M2 Rect-XY · M3 Up\*/Down\*（±LB）· M4 Segment（±LB）

### B 类 · VC 分层

| ID | 方案 | VC 用法 | 典型 VC | 特点 |
|----|------|---------|---------|------|
| M5 | 真 f-ring | 相位×方向（E/W/N/S） | 4 | 矩形块 + XY 环绕；链路须退休端点 |
| M6 | LASH | 每对一层，层内 CDG 无环 | 1–2 | 最短路；VC 性价比高 |
| M6b | LASH-TOR | 允许中途升层 | 1–2 | 层数已很低时收益有限 |
| M7 | 条带 dateline | 跨竖带 VC+1 | 5–6 | 极简；面积换性能 |
| M9 | 双向 Up\*/Down\* | VC0=UD，VC1=DU，按对选 | 2 | 易实现；路径短于单层 UD |
| M10 | 虚拟规则网格 | 逻辑 XY；X 相 VC0 / Y 相 VC1 | 2 | 上层仍见规则 mesh；缺边固定绕路 |

示意图与最优表见 HTML 报告。

## 3. Golden 与规模

- 健康 XY：`m=1 → 188 cy`，`m=5 → 770 cy`
- 扫描：882 行（含 Q 敏感度子集）；最优判据：**先牺牲最少，再 makespan**

## 4. 指标：raw / irreg 百分比

表中百分比都是「比值 − 1」再写成百分数（正=更慢，负=更快）：

| 列 | 公式 | 基准 | 怎么读 |
|----|------|------|--------|
| **raw** | `mk / mk_golden − 1` | 健康 8×6 XY | 相对无故障黄金配置慢了多少。负值常因牺牲后 A↓、流量按 A² 降，**不代表路由更好** |
| **irreg** | `mk / LB_same_A − 1` | 同一存活集合 A 的解析下界 | 同规模下相对「本来该跑多久」的额外开销；更适合比路由质量 |

例：`raw = +20%` → makespan 是 golden 的 1.2 倍；`irreg = +9.8%` → 比同 A 下界大约再慢 10%。

## 5. 最优结果（最新）

按牺牲→makespan：几乎全是 **M7 Stripe**（约 70/72 场）。例外：dead `node_corner_2x2`→M5；transit `node_corner_3x3` m=1→M6。

dead·m=1 中位：

| 方案 | sac | mk | load | VC | irreg |
|------|-----|-----|------|-----|-------|
| M7 Stripe | 0 | **194.5** | **143.5** | 5 | **+9.8%** |
| M10 Virtual | 0 | 239.5 | 170 | 2 | +23.4% |
| M5 f-ring | 0.5 | 248 | 178 | 4 | +19.1% |
| M6 / M6b LASH | 0 | 257 | 169.5 | 1.5 | +49.2% |
| M9 Dual UD | 0 | 342.5 | 210 | 2 | +96.1% |
| M3 Up\*/Down\* | 0 | 344.5 | 213 | 1 | +96.1% |

**怎么选：** 要最快 → M7（5–6 VC）；要少 VC → M6/M6b（1–2）或 M10（2）；要 XY 硬件语义 → M5；零 VC → M3。M6b 与 M6 中位相同（层数已触底）。M9 相对 M3 改善很小。

## 6. 文件

| 文件 | 作用 |
|------|------|
| `utils/pg_routing.py` | M1–M10 路由 / CDG / 牺牲 |
| `utils/dse_pg_alltoall_8x6.py` | DES + 扫描 |
| `utils/gen_pg_alltoall_report.py` | HTML |
| `results/pg_alltoall_8x6.json` | 数据 |
| `results/report_pg_alltoall_8x6.html` | 报告 |
