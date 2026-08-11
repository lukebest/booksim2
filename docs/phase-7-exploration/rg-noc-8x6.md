# 8×6 Request–Grant 分组交换 NoC 基线研究报告

**几何：** 8×6 mesh 与折叠 2D torus；H=7，V=9；RAMP=2，RAMP_BW=2  
**金属线恒定（数据面）：** torus 每链路带宽 = mesh 的一半（σ=2），对分带宽均为 6 flit/cy  
**控制平面（硬约束）：** request/grant 走**私有控制 NoC**（与数据面同构、独立物理链路），**不与数据面共链路**；不继承数据面 σ；走 **XY 维序路由**  
**类型：** bufferable（源端准入 + FIFO）/ bufferless（时隙预约、零缓冲）  
**仲裁：** `CA` 集中式 @ **(x=4, y=0) = nid 4**（原点左上、先 x 后 y，即第 1 行第 5 列）/ `DA` 目的端分布式 / `CA-batch` = CA + 错峰 request + 时间窗批量仲裁 + **全局无冲突排程 BCFS**  
**流量：** alltoall · allgather · allreduce · broadcast · reduce  
**产物：** `results/rg_noc_8x6.json`，`results/report_rg_noc_8x6.html`

## 0. 一页结论

1. **错峰 + 时间窗 + 全局无冲突排程（CA-batch）在长消息 alltoall 上是最好的 request–grant 基线。**
   mesh bufferless alltoall m=16：CA-batch **1906 cy** vs 即时 CA 2698 vs DA 2048 vs FIFO 基线 3238。
   收益来自 BCFS 的关键度排序 + 空洞回填：相对纯到达序（FCFS）贪心，mesh 上 +4.7…8.2%，torus 上 +12.7…14.8%。

2. **BCFS 的价值只在「多对多、链路争用型」流量上。** reduce / broadcast / allreduce 的增益恒为 0——
   它们的 makespan 由根节点 eject 端口或树直径这类**容量下界**决定，不是装箱质量决定，任何排序都打到同一下界。

3. **全局无冲突是 bufferless 成立的前提，且已被独立验证。** 79 组 CA-batch 配置全部 `conflict_free=✓`（逐链路两两复核），
   49 组 bufferless 回放 DES 实测 `max_residency=0` —— 路由器确实不需要任何 buffer。

4. **时间窗 W 存在最优点。** mesh alltoall 聚合 m=4：W=16 → 582 cy，W=64 → 616，W=256 → 740，W=∞（等齐）→ 612。
   W→0 退化成逐条即时仲裁（R_rg 最短但 BCFS 无视野）；W→∞ 退化成同步 barrier（视野最全但付「等最晚 request」税）。

5. **即便控制面完全私有，CA 逐流 alltoall 仍被控制收敛税打穿。**
   2256 条 request 挤入 CA 控制路由器 ≤4 入端口：解析下界 564 cy，DES 实测 t_last_request ≈ **1888 cy**，makespan 2105。
   缓解仍是那两条：**request 聚合**（每源 1 条 → 48 条，t_last_req=73）或 **DA 分布式**。

6. **中心调度器放在 (4,0) 使 mesh 付出边缘代价，torus 不付。** CA 到最远角的控制线延迟：mesh (0,5) → 4·7+5·9=**73 cy**；
   folded torus 顶点传递，最远仍 4·7+3·9=**55 cy**。这是 torus 在**控制平面**上的额外结构优势，与数据面 σ=2 的劣势无关。

7. **面积：** 私有控制 NoC 是数据面金属恒定预算之外的增量（每节点 +0.12，相对 IQ-XY=1.0）；CA-batch 的仲裁逻辑再 +0.07。

## 1. 私有控制 NoC 模型

```
源端 ──request──▶ [私有控制 NoC] ──▶ 仲裁器 ──grant──▶ [私有控制 NoC] ──▶ 源端闸门
                                                                      │
                                                                      ▼
                                                         数据平面 NoC（独立物理链路）
```

| 属性 | 数据平面 | 私有控制平面 |
|------|----------|----------------|
| 物理链路 | mesh 82 / torus 96 | **另一套**同构链路 |
| 与对方共享？ | — | **否** |
| 链路带宽 | mesh 1 / torus 0.5 flit/cy | 始终 1 ctrl-msg/cy |
| 路由 | XY 维序（torus 走 dateline VC） | XY 维序 |
| 承载 | 数据 flit | request / grant 仅 |
| 金属预算 | mesh↔torus 对分恒定 | **额外**金属/面积 |

隔离断言写入 JSON：`control_noc_policy.shared_with_data_plane = false`，且每条 RG 行的 `ctrl.shared_with_data_plane = false`。

## 2. 错峰 request → 时间窗批量仲裁 → BCFS

### 2.1 流水线

各节点**不同时**产生 request（默认每节点 U[0,64) 随机起跳，同一节点内多条 request 逐拍发出），
因此到达中心调度器的时刻天然离散：

```
① t_gen(i)                      节点 i 产生 request（各节点不同）
② t_arr(i) = t_gen(i) + ℓ_xy(i→CA) + 控制网争用      XY 路由、逐跳 H=7/V=9
③ CA 关闭长度 W 的滚动时间窗，取窗内到达的一批 request
   t_decide = max(窗尾, 批内最晚到达) + T_sched
④ release(i) = t_decide + ℓ_xy(CA→i) + 争用           grant 同样付线延迟
⑤ t0(i) ≥ release(i)，由 BCFS 决定                     数据面起始时刻
```

实测（mesh alltoall 聚合）：到达时刻离散度 109 cy，t_last_request_arrive=128，
t_last_grant_arrive=266，即 R_rg=**266 cy**。

### 2.2 BCFS：点对点请求的全局无冲突排程算法

单条被授权的点对点流是一个**刚性时空印记**（wormhole、无缓冲）：给定起始 `t0`、XY 路径 `P`、`m` 个 flit，
它在有向链路 `e` 上的占用区间为

```
occ(e) = [ t0 + pref_P(e) , t0 + pref_P(e) + m·σ )
```

`pref_P(e)` 是源到 `e` 尾端的累计线延迟，`σ` 为每 flit 拍数（mesh 1 / torus 2）。
**全局无冲突** = 为一批 request 选一组 `t0`，使任意链路上任意两个印记不重叠，且源/目的 ramp 容量不被超出。
这是固定路由的 job-shop 型区间装箱（一般情形 NP-hard），BCFS 用三件事求解：

| 组件 | 做法 | 作用 |
|------|------|------|
| 关键度优先表调度 | `pressure(r) = Σ_{e∈P(r)} load(e)`，`load(e)` = 本批用 e 的流数；pressure 大者裕度最小，先排 | 先安置最受约束的流 |
| 精确「最早可行起点」 | 对每链路 / 每 ramp 维护区间图，直接跳到「所有链路 + 两端 ramp 同时空闲」的最早时刻，不逐拍试探 | 等价于在已有排程的空洞里 **backfill**，短流能填缝 |
| 多起点搜索 | criticality / longest-path / FCFS / 随机×2 各跑一遍，取批 makespan 最小者 | 摆脱单一启发式的坏例 |

跨窗口的预约**持久保留**：第 k+1 批排程时看得到第 k 批已占用的区间，因此是全局（而非仅批内）无冲突。

输出**按构造即无冲突**，并由独立检查器 `verify_conflict_free()` 逐链路两两复核。

### 2.3 BCFS vs FCFS（相同 release 时刻，只换排序策略）

| 配置 | m=1 | m=4 | m=16 |
|------|-----|-----|------|
| mesh alltoall 聚合 | 0.0% | **+4.7%** | **+8.2%** |
| torus alltoall 聚合 | **+13.1%** | **+14.8%** | **+12.7%** |
| reduce / broadcast / allreduce | 0.0% | 0.0% | 0.0% |

torus 增益更大：σ=2 使印记长一倍、装箱更紧张，全局视野更值钱。
点对点 43 组配置中 BCFS **从不劣于** FCFS，平均增益 6.4%。

## 3. 按 pattern 归类的关键数字（mesh，除非注明）

### alltoall

| 仲裁 | plane | m=1 | m=4 | m=16 | ctrl (m=1) |
|------|-------|-----|-----|------|------------|
| FIFO 基线 | fifo | **192** | 701 | 3238 | — |
| CA 即时（聚合） | bufferable | 336 | 711 | 2633 | 48 req, t_req=73 |
| CA 即时（聚合） | bufferless | 334 | 748 | 2698 | 48 req, t_req=73 |
| **CA-batch** | bufferless | 362 | **616** | **1906** | 48 req, t_req=128 |
| DA | bufferable | 395 | 565 | 2323 | 2256 req, t_req=204 |
| CA 即时（**非聚合**） | bufferable | **2105** | — | — | 2256 req, t_req=**1888** |

m=1 时 CA-batch 略逊于即时 CA（362 vs 334）：数据面本就只有 ~98 cy，等窗的 R_rg 增量（266 vs 73）盖过了排程收益。
**m 越大 BCFS 越划算**——m=16 时反超 792 cy（-29%）。

### reduce

根节点 eject 端口是硬瓶颈，所有仲裁方案打到同一下界；BCFS 增益恒 0。
mesh：FIFO 98 / CA 230 / CA-batch 307 / DA 285（m=1）。请求-授权往返在这种「本来就很快」的 pattern 上纯属开销。

### broadcast

单根多播树、控制消息最少（聚合后仅 1 条），直径主导。
mesh DA bufferless m=1 = **97 cy**（近数据下界）；torus = **58 cy**（直径 55 vs 94 的优势）。

### allgather / allreduce

默认同步 barrier（等齐 48 个 request 再统一 grant）；allgather 另做异步「每 grant = 一棵多播树」对照。
异步去掉 barrier 等待，但树间冲突使数据面可能更长；大 m 时同步往往更划算。

## 4. 敏感度

### 时间窗 W（mesh bufferless alltoall 聚合 m=4）

| W | makespan | R_rg |
|---|----------|------|
| 16 | **582** | 218 |
| 64 | 616 | 266 |
| 256 | 740 | 356 |
| ∞（等齐） | 612 | 228 |

### Request 产生时刻模型（mesh bufferless alltoall 聚合 m=4, W=64）

| 模型 | J | 到达离散度 | makespan | BCFS 增益 |
|------|---|-----------|----------|-----------|
| uniform_jitter | 0 | 73 | **584** | +11.1% |
| uniform_jitter | 64 | 109 | 616 | +4.7% |
| uniform_jitter | 256 | 282 | 694 | +7.6% |
| distance_skew | 任意 | **46** | 609 | +9.5% |
| burst | 256 | 323 | 657 | +2.0% |

J=0（所有节点同时产生）时离散度仅来自线延迟差（73 cy）；J 增大则离散度线性增长、makespan 上升，
且 BCFS 增益**下降**——release 时刻本身已把流拉开，可优化的重叠变少。
`distance_skew`（离 CA 远的节点提前发，补偿线延迟）把离散度从 109 压到 **46**，是低成本工程手段。

## 5. 面积（归一化 IQ-XY = 1.0）

`area = 0.380(crossbar) + 0.170(control) + 5·VC·Q·0.00365(buffer) + arbiter + private_ctrl_noc(0.12)`

| 仲裁 | arbiter 开销 | 理由 |
|------|-------------|------|
| CA | 0.05 | 集中式仲裁逻辑 |
| **CA-batch** | **0.07** | 另加每链路/每 ramp 区间图 + 多起点表调度状态 |
| DA | 0.03 | 每目的端简单仲裁 |

- RG 配置均含私有控制 NoC +0.12/节点（数据面金属恒定之外）
- bufferable torus 另加 VC2 dateline 缓冲；bufferless 靠时隙预约无需数据 VC
- FIFO 基线无控制 NoC、无仲裁器开销

## 6. 选型建议

| 场景 | 建议 |
|------|------|
| 长消息 alltoall（m≥4） | **CA-batch + bufferless**：全局无冲突排程 + 零缓冲，m=16 比即时 CA 快 29% |
| 短消息 alltoall（m=1） | 即时 CA + request 聚合；等窗的 R_rg 不值得 |
| 切勿 | CA 逐流 request（2256 条）——私有控制网也救不了入端口汇聚 |
| broadcast / reduce / 小消息 | DA + bufferless；这些 pattern 的瓶颈是容量下界，别为全局排程买单 |
| 拓扑 | 控制平面偏好 torus（顶点传递，CA 边缘放置不吃亏）；数据面注意 σ=2 与 VC |
| 面积 | 须为私有控制 NoC 单独买单（+0.12/节点），CA-batch 再 +0.07 |

## 7. 验证清单

| # | 项 | 结果 |
|---|----|------|
| 1 | 对分带宽相等（数据面） | ✓ |
| 2 | torus 数据 σ=0.5 | ✓ |
| 3 | FIFO alltoall m=1 ≈ 188 | ~192 |
| 4 | bufferless 零驻留（117 组） | ✓ |
| 5 | 保序 | ✓ |
| 6 | torus CDG 无环 | ✓ |
| 7 | 控制收敛 ≥ 564 量级 | ✓（1888） |
| 8 | 私有控制 NoC `shared_with_data_plane=false` | ✓ |
| 9 | 中心调度器坐标一致为 (4,0)，nid=4 | ✓ |
| 10 | 控制平面 XY 维序路由 | ✓ |
| 11 | Request 到达时刻确实离散（67 组多源配置 spread>0） | ✓ |
| 12 | **BCFS 全局无冲突**（79 组逐链路复核，冲突数 0） | ✓ |
| 13 | CA-batch bufferless 回放零驻留（49 组） | ✓ |
| 14 | 点对点 BCFS 从不劣于 FCFS（43 组） | ✓ |
| 15 | 单播单调性 bufferable≲bufferless（18 组周期级精确行） | ✓ |

## 8. 已知局限

- 数据面金属线 torus/mesh≈1.17；折叠线长×2 用 `torus_delay_scale=2` 对照。
- 私有控制 NoC 是**额外**金属，不计入数据面对分恒定。
- **BCFS 是在线窗口局部最优**：CA 只能给眼前这一批打分，无法预知后续窗口。
  多树 pattern（mesh allgather m=16）出现 2 组「本批更紧、全局更慢」的反向案例（最差 +4.6%）；
  点对点 pattern 上未出现。允许离线全局排程（W=∞，所有 request 先到齐）可消除此效应。
- BCFS 路径固定为 XY 维序；允许自适应路由会扩大可行域，但需重做 CDG 无环性论证。
- 多树 bufferable（48 棵树 allgather）与 2256 流 alltoall 用事件驱动单播展开近似，
  共享树边被重复计数、head-of-line 停顿被高估，是**保守上界**（torus σ=2 上偏差最大）。
  严格单调性验证只覆盖周期级精确的 18 组。
- 控制面 hop 延迟取与数据面相同的 H/V（线延迟主导）；未再为窄线单独标定。
- reduce = **gather + PE 本地归约**（ADR-002 / Arch-A2），无网内算术。

## 9. 文件

| 文件 | 作用 |
|------|------|
| `utils/rg_topo.py` | mesh/torus 数据拓扑，CA 位置 (4,0) |
| `utils/rg_bounds.py` | 下界（含控制入端口收敛） |
| `utils/rg_collectives.py` | 五 pattern |
| `utils/rg_arbiter.py` | 私有控制 NoC DES + CA/DA 即时调度 |
| `utils/rg_batch_sched.py` | **错峰 request + XY 控制路由 + 时间窗批量仲裁 + BCFS 全局无冲突排程 + 独立冲突检查器** |
| `utils/dse_rg_noc_8x6.py` | 数据面 DES + 扫描 + 验证 |
| `utils/gen_rg_noc_report.py` | HTML（按 pattern 归类） |
| `results/rg_noc_8x6.json` / `report_rg_noc_8x6.html` | 数据与报告 |
