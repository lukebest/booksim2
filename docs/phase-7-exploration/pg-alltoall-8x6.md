# 8×6 分组交换 NoC：Partial-Good 下 Alltoall 解决方案与劣化率

**几何：** 8×6 mesh，H=7，V=9，RAMP=2，RAMP_BW=2  
**流量：** 一次性 alltoall；消息长度 m∈{1,5}；同源同目的 wormhole 保序  
**故障模型：** 照搬 ring_report 的 link + node 区域分类（不含 quadrant）。corner **链路**故障落在角节点 `(0,0)` 的入射边（与 hamilton_ring 为保 Hamilton 度≥2 而偏移到 (1,0) 附近的定义不同）。  
**产物：** `results/pg_alltoall_8x6.json`，`results/report_pg_alltoall_8x6.html`

## 1. 问题与硬约束

在 partial-good（PG）缺陷下，分组交换 2D mesh 要继续跑 alltoall，且：

1. **无死锁**（通道依赖图 CDG 无环；DES 交叉验证无 `STALL_LIMIT`）
2. **保序**（每 `(src,dst)` 唯一确定性路径 + wormhole；DES `ordered_ok`）

不满足时允许**牺牲额外 good 节点**（优先故障边界，再整行/整列，最后矩形屏蔽），被牺牲节点退出本次 alltoall。

## 2. PG 语义与方案详解

实现见 `utils/pg_routing.py`。引擎：自包含 credit / 每端口（每 VC）FIFO DES（`utils/dse_pg_alltoall_8x6.py`）。

### 2.1 PG 语义

| 语义 | 含义 |
|------|------|
| `dead` | 故障节点 PE+router+入射链路全失效（严格 ring_report） |
| `transit` | PE 不参与；router/链路仍可转发（holes_40 风格） |

所有进入 DES 的表必须同时满足：CDG 无环（无死锁）、每 `(src,dst)` 唯一路径（保序）、compute 集合连通。失败时由统一牺牲恢复器禁用额外 good 节点（边界 → 整行/整列 → 矩形屏蔽）。

### 2.2 M1 — XY（`xy`）

**思想：** 坚持维序路由（DOR）：先走完 X，再走 Y；硬件几乎不用改路由逻辑。

**路径：** 对每个 `(s,d)` 严格按 XY 折线前进；所需 hop 被故障删除则整表失败，进入牺牲恢复。

**无死锁：** 完整矩形上 XY 的 CDG 无环；残图上仍以 CDG 硬校验。

**特征：** 中心/角链路一断极易「穿不过」；恢复时常退化成与 M2 类似的大矩形牺牲。用于量化「坚持 XY 硬件」要付多少牺牲代价。

### 2.3 M2 — Rect-XY（`rect_xy`）

**思想：** 不在破损拓扑上绕路，而是裁成仍规则的子矩形，矩形内继续跑 XY。

**做法：** (1) 标出故障触及的行/列；(2) 在剩余行、列中各取最长连续段，叉成最大轴对齐矩形；(3) 矩形外原计算节点全部记为 `forced_sacrificed`；(4) 矩形内生成 XY 全表。

**无死锁：** 子矩形上经典 XY，CDG 无环。

**特征：** 牺牲粗、可预测；raw slowdown 常为负是因为参与者变少——应看 `irregularity_penalty` 与 `sacrifice_cost`。

### 2.4 M3 — Up\*/Down\*（`updown`）

**思想：** 在存活路由图上建 BFS 生成树，用「先上后下」限制转向，保证不规则连通图上的无死锁确定性路由。

**做法：** (1) 根 = 路由图中度最大节点；(2) `label(n)` = 到根 BFS 距离，朝根为 up、离根为 down、同层侧向视为 down；(3) 合法路径 = 若干 up 之后只能 down；(4) 约束下 BFS 取最短合法路径。

**无死锁：** Up\*/Down\* 按构造 CDG 无环。

**特征：** link/node 故障下通常**零牺牲**即可全表可行，是保住计算规模的主推荐；路径往往比 XY 更绕、负载更不均，故 raw slowdown 较高。

### 2.5 M3+LB — Up\*/Down\* + 负载均衡（`updown_lb`）

在 M3 路径表上后处理：统计有向边 alltoall 对数负载；每轮重排途经最热边的若干 `(s,d)`，用负载感知 Dijkstra（边权 ≈ 1+负载）换路；每轮后整表再校验 CDG，失败则回退。

**特征：** 目标是压低最大链路负载；在本 8×6 上对 median makespan 改善通常很小（合法路径集合较窄）。

### 2.6 M4 — Segment / 奇偶转向（`segment`）

**思想：** 简化 segment-based / odd-even 族：按列带施加不同转向禁令，打破 mesh 环依赖。

**转向规则**（列段宽 2，`seg=(x//2)%2`）：直行允许、180° 禁止；偶段禁北→东 / 南→西；奇段禁北→西 / 南→东。路径 = 约束下最短路。

**无死锁：** 完整 mesh 上属奇偶转向模型族；破损后仍 CDG 硬校验，不通则牺牲恢复。

**特征：** 介于 XY 与 Up\*/Down\*——有时零牺牲，中心故障时常需矩形化。

### 2.7 M4+LB — Segment + 负载均衡（`segment_lb`）

与 M3+LB 相同流程，起点换成 M4 路径表。

### 2.8 M5 — Fault-ring + 2 VC（`fault_ring_vc`）

**思想：** 强制绕开故障矩形（即使 transit 下洞内 router 仍活着也不穿洞），在穿孔图上用 Up\*/Down\* 选路，并用 2 VC + 垂线 dateline 加强隔离。

**做法：** (1) 节点故障：取故障 bbox，内部节点从路由图剔除；纯链路故障无 bbox，只在已断链图上路由；(2) 剩余图上跑 Up\*/Down\*；(3) dateline = 故障中心列（链路故障用 `mx//2`）；路径每水平穿过该列一次，VC 奇偶翻转。DES 每端口按 VC 分队列与 credit。

**与 M3 差别：** transit 节点洞上 M3 可穿洞转发，M5 禁止；M5 多 2 VC 硬件假设。

**特征：** 零牺牲场景多，makespan 常接近 M3；大洞绕行时可能略差于可穿洞的 transit-M3。

### 2.9 对照表

| 方案 | 路由本质 | 硬件改动 | 典型牺牲 | 适用意图 |
|------|----------|----------|----------|----------|
| M1 XY | 严格先 X 后 Y | 最小（原 XY） | 高 | 量化不改路由的代价 |
| M2 Rect-XY | 裁矩形 + XY | 最小 | 固定偏高 | 规整化、可预测 |
| M3 Up\*/Down\* | 树标号 + 先上后下 | 路由表/逻辑 | 通常 0 | **保规模主方案** |
| M3+LB | M3 + 热点重路由 | 同 M3 | 同 M3 | 压最大链路负载 |
| M4 Segment | 列带奇偶转向 | 转向限制 | 中高 | 折中绕路能力 |
| M4+LB | M4 + LB | 同 M4 | 同 M4 | 同左 |
| M5 Fault-ring 2VC | 禁穿洞 + Up\*/Down\* + dateline VC | 2 VC + 绕障表 | 通常 0 | 强制隔离故障区 |

## 3. Golden 与规模

- 健康 8×6 XY：`m=1 → 188 cy`，`m=5 → 770 cy`（解析 LB 分别为 98 / 480；差距来自长线 credit RTT 与 HOL）
- 全量：18 故障（9 link + 9 node）× 2 语义 × 7 路由配置 × 2 消息长度 @ Q=19，外加 Q∈{4,8} 敏感度子集 → **522 行**
- **Q**：入端口 FIFO 深度 / 出链路 credit 初值。默认 **Q=19 = 2·V+1**（V=9），覆盖最长垂直链路的 credit 往返，使链路可跑满 1 flit/cy；Q 偏小会因 credit 饥饿人为拉长 makespan

## 4. 主要结果（Q=19，不含 quadrant）

### 4.1 零牺牲保连通：Up\*/Down\* / Fault-ring

- **推荐默认方案：M3 Up\*/Down\***——全部 18 个 link/node 场景 **零牺牲**；m=1 dead 中位 raw slowdown ≈ **+91%**。
- m=5 时带宽项放大，不规则拓扑的链路热点更伤。
- 负载均衡（M3+LB）在本网格上对 median makespan 改善通常很小。

### 4.2 高牺牲、低 raw slowdown：XY / Rect-XY / Segment

负的 `raw_slowdown` **不是**路由变好，而是牺牲后参与节点减少、工作量锐减。主指标应看 **`irregularity_penalty`**（同存活集合下相对无死锁参考负载）。

### 4.3 transit vs dead

- 对 Up\*/Down\*：transit 通常不差于 dead（router 保活只增路径选项）。
- 对 XY：transit 下更多场景可零牺牲（PE 洞不挡 XY），dead 则常需矩形牺牲。

### 4.4 Q 敏感度（子集）

Q=4 时 Up\*/Down\* 可明显慢于 Q=19（长线 credit 饥饿）。XY 小矩形场景对小 Q 往往不敏感。

## 5. 劣化率怎么读

1. **要保住计算规模** → 用 Up\*/Down\*（或 Fault-ring），接受相对 golden 约 **+70%～+90%（m=1）/ +140%～+150%（m=5）** 的 raw slowdown，牺牲成本 ≈ 0。
2. **可接受裁掉大量 good PE** → Rect-XY / 牺牲后的 XY，raw 可优于 golden，但 `sacrifice_cost` 高，且 alltoall 语义参与者变少。
3. **比较路由质量** → 看 `irregularity_penalty`，不要只看 raw。

## 6. 文件索引

| 文件 | 作用 |
|------|------|
| `utils/pg_faults_8x6.py` | 故障目录 + dead/transit 展开 |
| `utils/pg_routing.py` | 路由 / CDG / 牺牲恢复 / 下界 / LB |
| `utils/dse_pg_alltoall_8x6.py` | DES + 扫描 |
| `utils/gen_pg_alltoall_report.py` | HTML 报告 |
| `results/pg_alltoall_8x6.json` | 原始数据 |
| `results/report_pg_alltoall_8x6.html` | 可读报告（含 mesh SVG） |
