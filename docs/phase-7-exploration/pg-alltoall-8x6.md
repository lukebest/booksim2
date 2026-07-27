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

表中百分比都是「比值 − 1」再写成百分数：

| 列 | 公式 | 基准 | 怎么读 |
|----|------|------|--------|
| **raw** | `mk / mk_golden − 1` | 健康 8×6 XY | 相对无故障黄金配置慢了多少。可为负——牺牲后 A↓、总流量按 A² 降，**不代表路由更好** |
| **irreg** | `mk / LB_same_A − 1` | 同一存活集合 A 上**与路由无关的真下界** | 同规模下相对「无论怎么路由都至少要跑这么久」的额外开销；恒 ≥ 0，跨方案可比 |

`LB_same_A = max(minimax_load_lb·m, inj_term, lat_lb)`：

- `minimax_load_lb`：**割下界**。对每个轴对齐割 (S, S̄)，S→S̄ 的 `|S∩C|·|S̄∩C|` 对必须挤过 S 的活出边，
  故存在链路负载 ≥ `ceil(需求/出边数)`；取所有矩形割的最大值。任何路由（含无死锁约束之外的）都不可能低于它。
  健康 8×6 上该值 = 96，恰好等于 XY 的实际负载，说明界是紧的。
- `lat_lb` = H/V 加权最短路直径 + `2·RAMP + (m−1)`；`inj_term` = `ceil((A−1)·m / RAMP_BW)`。

因为最忙链路至少要搬 `minimax_load_lb·m` 个 flit，`mk ≥ LB_same_A` 恒成立。
（早期版本分母取的是 XY 随手装填后的可达负载，那是可达值不是下界，会被好方案压过去而出现负 irreg。）

**第 6 节的全方案 makespan 矩阵副行用 irreg 而非 raw**：不同方案牺牲数不同、A 不同，
raw 会因 A 变小而虚低；irreg 各自以自身 A 的下界为分母，才具可比性。raw 仍保留在单元格 tooltip 中。

## 5. 最优结果（最新）

按牺牲→makespan：几乎全是 **M7 Stripe**（约 70/72 场）。例外：dead `node_corner_2x2`→M5；transit `node_corner_3x3` m=1→M6。

dead·m=1 中位：

| 方案 | sac | mk | load | VC | irreg |
|------|-----|-----|------|-----|-------|
| M7 Stripe | 0 | **194.5** | **143.5** | 5 | **+93.8%** |
| M10 Virtual | 0 | 239.5 | 170 | 2 | +131.0% |
| M5 f-ring | 0.5 | 248 | 178 | 4 | +125.7% |
| M6 / M6b LASH | 0 | 257 | 169.5 | 1.5 | +160.2% |
| M9 Dual UD | 0 | 342.5 | 210 | 2 | +243.3% |
| M3 Up\*/Down\* | 0 | 344.5 | 213 | 1 | +241.4% |

（irreg 绝对值比早期版本高，是因为分母换成了真下界；方案间排序不变。M5 略低于 M10 是它中位多牺牲 0.5 个节点、A 更小所致。）

**怎么选：** 要最快 → M7（5–6 VC）；要少 VC → M6/M6b（1–2）或 M10（2）；要 XY 硬件语义 → M5；零 VC → M3。M6b 与 M6 中位相同（层数已触底）。M9 相对 M3 改善很小。

## 6. 文件

| 文件 | 作用 |
|------|------|
| `utils/pg_routing.py` | M1–M10 路由 / CDG / 牺牲 |
| `utils/dse_pg_alltoall_8x6.py` | DES + 扫描 |
| `utils/gen_pg_alltoall_report.py` | HTML |
| `results/pg_alltoall_8x6.json` | 数据 |
| `results/report_pg_alltoall_8x6.html` | 报告 |
