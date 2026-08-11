# 8×6 Request–Grant 分组交换 NoC 基线研究报告

**几何：** 8×6 mesh 与折叠 2D torus；H=7，V=9；RAMP=2，RAMP_BW=2  
**金属线恒定（数据面）：** torus 每链路带宽 = mesh 的一半（σ=2），对分带宽均为 6 flit/cy  
**控制平面（硬约束）：** request/grant 走**私有控制 NoC**（与数据面同构、独立物理链路），**不与数据面共链路**；不继承数据面 σ；走 **XY 维序路由**；单向时延 = **⌊含 link delay 的曼哈顿距离 / 2⌋**（数据面仍用满 H/V）  
**类型：** bufferable（源端准入 + FIFO）/ bufferless（时隙预约、零缓冲）  
**仲裁：** `CA` 集中式 @ **(x=4, y=0) = nid 4**（原点左上、先 x 后 y，即第 1 行第 5 列）/ `DA` 目的端分布式 / `CA-batch` = CA + 错峰 request + 时间窗批量仲裁 + **全局无冲突排程 BCFS**  
**流量：** alltoall · allgather · allreduce · broadcast · reduce  
**排程算法族（§6）：** 7 个类 iSLIP / BvN 成员；**一条 request = 一条 VOQ**（每源 N−1 条，alltoall 全网 2256 条），核心映射「VOQ / 置换矩阵 → 链路不相交路径集 LDPS」  
**产物：** `results/rg_noc_8x6.json`，`results/mesh_sched_pareto.json`，`results/report_rg_noc_8x6.html`，`results/mesh_sched_pareto*.png`

## 0. 一页结论

1. **错峰 + 时间窗 + 全局无冲突排程（CA-batch）在长消息 alltoall 上是最好的 request–grant 基线。**
   控制时延改为 ⌊曼哈顿/2⌋ 后，mesh bufferless alltoall m=16：CA-batch **1935 cy** vs 即时 CA 聚合 ~2701 vs FIFO 基线 3238。
   收益来自 BCFS 关键度排序 + 空洞回填（相对 FCFS：mesh 数个百分点、torus 十余百分点）。

2. **BCFS 的价值只在「多对多、链路争用型」流量上。** reduce / broadcast / allreduce 的增益恒为 0——
   它们的 makespan 由根节点 eject 端口或树直径这类**容量下界**决定，不是装箱质量决定，任何排序都打到同一下界。

3. **全局无冲突是 bufferless 成立的前提，且已被独立验证。** 79 组 CA-batch 配置全部 `conflict_free=✓`（逐链路两两复核），
   49 组 bufferless 回放 DES 实测 `max_residency=0` —— 路由器确实不需要任何 buffer。

4. **时间窗 W 存在最优点。** mesh alltoall 聚合 m=4：W=16 → 582 cy，W=64 → 616，W=256 → 740，W=∞（等齐）→ 612。
   W→0 退化成逐条即时仲裁（R_rg 最短但 BCFS 无视野）；W→∞ 退化成同步 barrier（视野最全但付「等最晚 request」税）。

5. **即便控制面完全私有、时延减半，CA 逐流 alltoall 仍被控制收敛税打穿。**
   2256 条 request 挤入 CA 控制路由器 ≤4 入端口：解析下界 564 cy，DES 实测 t_last_request ≈ **1887 cy**（路径争用主导，半曼哈顿几乎不改汇聚税）。
   缓解仍是那两条：**request 聚合**（每源 1 条 → 48 条，t_last_req≈49）或 **DA 分布式**。

6. **中心调度器放在 (4,0) 使 mesh 付出边缘代价，torus 不付。** 控制面时延 = ⌊曼哈顿/2⌋：
   mesh 最远 ⌊73/2⌋=**36 cy**，torus 最远 ⌊55/2⌋=**27 cy**。这是 torus 在**控制平面**上的额外结构优势，与数据面 σ=2 的劣势无关。

7. **面积：** 私有控制 NoC 是数据面金属恒定预算之外的增量（每节点 +0.12，相对 IQ-XY=1.0）；CA-batch 的仲裁逻辑再 +0.07。

8. **Request 严格按 VOQ：每源 N−1 条（§6）。** alltoall 全网 2256 条控制消息（`aggregate=False`），
   私有控制面 R_rg ≈ **1946～2010 cy**（聚合时仅 ~175）。调度与 grant 均以 VOQ 为粒度。

9. **把 iSLIP 直接搬到 mesh 会活锁（§6）。** 交叉开关上一条 VOQ 只要 1 个输出端口同意；mesh 上要路径**全部** h 条链路同意，
   概率 ~k^−(h−1)。alltoall（h≈6, k≈80）实测靠一致性通过的 VOQ 只有 **~18%**。
   **这正面回答了「为什么 mesh 需要集中调度器」——不是工程偷懒，是分布式匹配在路径资源上不收敛。**

10. **护航效应 + 控制汇聚税共同主导 alltoall。** LDPS 轮次已贴 Birkhoff 下界（latin 1.03×），
    但 VOQ 非聚合下 R_rg≈2000 把所有算法的 makespan 抬到 FIFO 之上；相位型相对流水型仍多付护航，
    而 incremental（islip）因「VOQ 到达即裁」在短消息 alltoall 上反超 `greedy_ff`（m=1：4238 vs 此前聚合口径下的时间端）。

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
| 单向时延 | hx·H + hy·V（满 H/V） | **⌊(hx·H + hy·V)/2⌋** |
| 承载 | 数据 flit | request / grant 仅 |
| 金属预算 | mesh↔torus 对分恒定 | **额外**金属/面积 |

隔离断言写入 JSON：`control_noc_policy.shared_with_data_plane = false`，且每条 RG 行的 `ctrl.shared_with_data_plane = false`。

## 2. 错峰 request → 时间窗批量仲裁 → BCFS

### 2.1 流水线

各节点**不同时**产生 request（默认每节点 U[0,64) 随机起跳，同一节点内多条 request 逐拍发出），
因此到达中心调度器的时刻天然离散。控制面单向时延取 ⌊曼哈顿线延迟/2⌋：

```
① t_gen(i)                      节点 i 产生 request（各节点不同）
② t_arr(i) = t_gen(i) + ℓ_ctrl(i→CA) + 控制网争用     ℓ_ctrl=⌊(hx·H+hy·V)/2⌋
③ CA 关闭长度 W 的滚动时间窗，取窗内到达的一批 request
   t_decide = max(窗尾, 批内最晚到达) + T_sched
④ release(i) = t_decide + ℓ_ctrl(CA→i) + 争用         grant 同样付半曼哈顿
⑤ t0(i) ≥ release(i)，由 BCFS 决定                     数据面起始时刻
```

实测（mesh alltoall 聚合 m=4）：到达离散度 **83 cy**，t_last_request_arrive=**95**，
t_last_grant / R_rg=**175 cy**（此前满 H/V 口径约为 109 / 128 / 266）。

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
| CA 即时（聚合） | bufferable | 275 | 709 | 2701 | 48 req, t_req=49 |
| CA 即时（聚合） | bufferless | 273 | 742 | 2670 | 48 req, t_req=49 |
| **CA-batch** | bufferless | **270** | **564** | **1935** | 48 req, t_req=95 |
| DA | bufferable | 349 | 589 | 2330 | 2256 req, t_req=175 |
| CA 即时（**非聚合**） | bufferable | **2067** | — | — | 2256 req, t_req=**1887** |

半曼哈顿控制时延下，m=1 时 CA-batch 已与即时 CA 持平（270 vs 273）；
**m 越大 BCFS 越划算**——m=16 时相对即时 CA 约 −28%。

### reduce

根节点 eject 端口是硬瓶颈，所有仲裁方案打到同一下界；BCFS 增益恒 0。
mesh：FIFO 98 / CA 178 / CA-batch 269 / DA 196（m=1）。请求-授权往返在这种「本来就很快」的 pattern 上纯属开销。

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
| 16 | **544** | 137 |
| 64 | 564 | 175 |
| 256 | 722 | 317 |
| ∞（等齐） | 561 | 156 |

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

## 6. 2D mesh 链路带宽与空间调度算法族（类 iSLIP / BvN）

数据 `results/mesh_sched_pareto.json`（660 行），图 `results/mesh_sched_pareto*.png`、`mesh_sched_lambda.png`。

### 6.0 Request 口径：一条 request = 一条 VOQ

交叉开关 IQ 模型里输入 \(i\) 对输出 \(j\) 有 \(\mathrm{VOQ}[i][j]\)；本族把同一语义落到 mesh：

> 节点 \(s\) 对每个目的 \(d\neq s\) 持有 \(\mathrm{VOQ}[s\rightarrow d]\)（**每源 N−1 条**）。
> **一条非空 VOQ 发出一条 request**；grant 授权该 VOQ 沿 XY 路径发完 \(m\) 拍消息。

8×6（N=48）alltoall → 每源 47 条 VOQ、全网 **2256** 条控制消息（`aggregate=False`，不做「每源聚合一条」）。
调度决策与 LDPS 划分均以 VOQ 为粒度；树形 pattern 下每源通常只有 1 条「树 VOQ」。

### 6.1 核心映射：VOQ / 置换矩阵 → 链路不相交路径集（LDPS）

iSLIP / BvN 分配的是**一个**交叉开关上各 VOQ 对端口的占用：资源是二部匹配，单位是**置换矩阵**。
2D mesh 分配的是**有向链路 × 时间**，而且一条 VOQ 吃掉整条 XY 路径而不是一个输出端口。把分配单位换成

> **LDPS**（Link-Disjoint Path Set）= 一组 VOQ，其 XY 路径（树流为树边集）两两**链路不相交**，
> 且源/目的 ramp 用量 ≤ RAMP_BW·σ

之后，经典算法一一对应搬得过来：

| 交叉开关 | 2D mesh 对应物 | 实测 |
|---|---|---|
| \(\mathrm{VOQ}[i][j]\) request | \(\mathrm{VOQ}[s\rightarrow d]\) request | alltoall：2256 条、每源 47 条 ✓ |
| 置换矩阵（一个时隙配置） | 一个 LDPS（一轮） | 轮内链路不相交 → **按构造无冲突**，独立复核 540/540 ✓ |
| BvN 分解为最少置换数 | 划分 VOQ 集为最少 LDPS 轮次 | 下界 `max_e (#VOQs on e)`；alltoall 实测 1.03～1.19× |
| iSLIP：输出 grant + 输入 accept | 逐链路 RR grant + **全路径一致** accept | mesh 新增的硬约束，见 §6.2 |

七个成员：**batch/slot** `bvn_mesh`(first-fit) · `mwm_mesh`(pressure 最大权重) · `latin_mesh`(代数 ROM)；
**batch/pipelined** `bcfs`(区间装箱+多起点)；**incremental/slot** `islip_mesh`(RR 指针) · `pim_mesh`(随机)；
**incremental/pipelined** `greedy_ff`(到达序最早可行)。

### 6.2 结论一：全路径一致 accept 在 mesh 上塌陷 → mesh 必须集中调度

一条 VOQ 有 h 条链路、每条链路 k 个竞争者，逐链路独立 RR 授权下拿到**全部**授权的概率 ~k^−(h−1)：

| pattern（m=4） | 一致性通过比例 | request 条数 |
|---|---|---|
| broadcast（1 条树 VOQ） | 100% | 1 |
| allgather（48 棵树） | 95.8% | 48 |
| reduce（每源→根 1 条） | 93.6% | 47 |
| **alltoall（每源 N−1 条 VOQ）** | **17.8%** | **2256** |

所以纯链路局部的仲裁器在 mesh 高负载下会活锁。本族 5 个相位型成员统一为
「I 轮分布式一致性匹配 **+ 按优先级顺序补齐该轮 LDPS**」，成员差别落在**优先级规则**上
（flow_id / pressure / ROM / RR 指针 / 随机）。

**连带结果：迭代轮数 I 的边际收益为负。** alltoall m=4 下 I：1→4 数据面几乎不动（4433→4325），
却把 `T_sched` 乘到 1728，makespan 4576→**6053**。**I=1 在全部 (pattern, m) 上都不劣。**

### 6.3 结论二：护航效应 + VOQ 控制汇聚税

相位型一轮共享一个起始时刻；7/9 拍长线下轮时长由**线延迟**主导。VOQ 非聚合后，
alltoall 还要付私有控制面汇聚税 R_rg≈2000 cy（2256 条 request 挤入 CA 入端口）。

| mesh bufferless m=4 | 轮次/下界 | 护航比 | DES | T_sched | makespan | R_rg | 面积 |
|---|---|---|---|---|---|---|---|
| `latin_mesh` | 99/96 = **1.03×** | 2.26× | 5426 | 99 | 5525 | 2010 | **0.0023** |
| `bvn_mesh` | 114/96 = 1.19× | 2.44× | 5427 | 114 | 5541 | 2010 | 0.0106 |
| `islip_mesh` I=1 | 143/96 = 1.49× | 2.26× | 4433 | 143 | 4576 | **1946** | 0.0178 |
| `pim_mesh` I=1 | 143/96 = 1.49× | 2.24× | 4578 | 143 | 4721 | 1946 | 0.0172 |
| `greedy_ff` | — | — | **2129** | 2256 | **4385** | 1946 | 0.0500 |
| `bcfs` | — | — | 2135 | 11280 | 13415 | 2010 | 0.0883 |

要点：

- 轮次数已贴下界（latin 1.03×），但相对 `greedy_ff` 仍慢——护航仍在。
- **incremental（islip）在 VOQ 口径下比 batch 相位型快一截**：VOQ 到达即裁，不必等窗口攒齐 2256 条；
  短消息 alltoall m=1 上甚至是全局最快（4238）。
- `bcfs` 的 T_sched=11280 把 makespan 打穿，全程不在前沿。

**顺带证否**：Latin 方阵在 mesh 上不是 N−1=47 轮——XY 下置换不链路不相交，实测 99 轮。

### 6.4 结论三：计入 T_sched 后的 Pareto（VOQ 口径）

`T_sched` 回灌后：`bcfs` / `mwm_mesh` 仍全程不在前沿。alltoall 前沿变成
`latin`（面积）— `pim`/`islip`（中段）— `greedy_ff`（时间，m≥4）；
m=1 时间端换成 `islip_mesh`（流水型的 T_sched=2256 压过了它的 span 优势）。

### 6.5 每个 (pattern, m) 的 Pareto 前沿与选型（mesh · bufferless · VOQ）

| pattern | m | FIFO | 时间端（最快） | 面积端（最省） | λ 换手 |
|---|---|---|---|---|---|
| alltoall | 1 | 192 | `islip_mesh` **4238** | `latin_mesh` 5233 @0.0023 | 16→latin |
| alltoall | 4 | 701 | `greedy_ff` **4385** | `latin_mesh` 5525 @0.0023 | 2→islip / 16→latin |
| alltoall | 16 | 3238 | `greedy_ff` **5398** | `latin_mesh` 6695 @0.0023 | 4→islip / 16→latin |
| allgather | 1 | 362 | `greedy_ff` **328** | `bvn_mesh` 1061 @0.0018 | 不换手 |
| allgather | 4 | 1157 | `greedy_ff` **610** | `bvn_mesh` 1179 @0.0018 | 不换手 |
| allgather | 16 | 4337 | `greedy_ff` **1511** | `bvn_mesh` 1653 @0.0018 | 2→islip / 16→bvn |
| allreduce | 1 | 107 | `greedy_ff` 252 | `bvn_mesh` 427 @0.0018 | 16→islip |
| allreduce | 4 | 247 | `greedy_ff` 276 | `bvn_mesh` 491 @0.0018 | 16→islip |
| allreduce | 16 | 811 | `greedy_ff` **723** | `islip_mesh` 794 @0.0047 | 4→islip |
| broadcast | 1/4/16 | 139/280/844 | `islip_mesh` 182/183/**189** | 同 @0.0018 | 全程 islip |
| reduce | 1 | 98 | `greedy_ff` 257 | `bvn_mesh` 458 @0.0018 | 不换手 |
| reduce | 4 | 175 | `greedy_ff` 297 | `bvn_mesh` 471 @0.0018 | 16→bvn |
| reduce | 16 | 652 | `greedy_ff` 760 | `bvn_mesh` 840 @0.0018 | **4→bvn** |

读法：

- **alltoall 在 VOQ 口径下全部慢于 FIFO**（控制汇聚税主导，与 §2 非聚合结论一致）。
- **短消息 alltoall 选 `islip_mesh` I=1**；长消息时间优先仍选 `greedy_ff`，面积优先选 `latin_mesh`。
- 树形 / broadcast 数字与聚合口径几乎相同（request 条数本就 ≤48）。

### 6.6 Torus 对照（σ=2 的影响）

| pattern | m | mesh 最优 | torus 最优 | torus/mesh |
|---|---|---|---|---|
| alltoall | 1 | `islip_mesh` 4238 | `islip_mesh` 2328 | **0.55×** |
| alltoall | 16 | `greedy_ff` 5398 | `mwm_mesh` 4516 | 0.84× |
| allgather | 16 | `greedy_ff` 1511 | `bvn_mesh` 1166 | 0.77× |
| broadcast | 16 | `islip_mesh` 189 | `islip_mesh` 150 | 0.79× |
| reduce | 16 | `greedy_ff` 760 | `greedy_ff` 857 | **1.13×** |
| allreduce | 16 | `greedy_ff` 723 | `greedy_ff` 829 | **1.15×** |

VOQ 非聚合下 torus 对短消息 alltoall 的优势放大到 0.55×（控制路径更短 + 轮次下界更低）；
长消息 reduce/allreduce 上 σ=2 仍反超。

## 7. 选型建议

| 场景 | 建议 |
|------|------|
| 长消息 alltoall（m≥4） | **CA-batch + bufferless**：全局无冲突排程 + 零缓冲，m=16 比即时 CA 快 29% |
| 短消息 alltoall（m=1） | 即时 CA + request 聚合；等窗的 R_rg 不值得 |
| 切勿 | CA 逐流 request（2256 条）——私有控制网也救不了入端口汇聚 |
| broadcast / reduce / 小消息 | DA + bufferless；这些 pattern 的瓶颈是容量下界，别为全局排程买单 |
| 拓扑 | 控制平面偏好 torus（顶点传递，CA 边缘放置不吃亏）；数据面注意 σ=2 与 VC |
| 面积 | 须为私有控制 NoC 单独买单（+0.12/节点），CA-batch 再 +0.07 |

排程算法（§6，**VOQ 口径**：一 request = 一 VOQ，每源 N−1 条）：

| 场景 | 建议 | 理由 |
|------|------|------|
| 短消息 alltoall（m=1） | **`islip_mesh` I=1**（0.0178） | VOQ 到达即裁，避开 batch 等窗；m=1 全局最快 4238 |
| 长消息 alltoall 时间优先 | **`greedy_ff`**（0.0500） | 区间装箱无护航；m=4/16 时间端 |
| 长消息 alltoall 面积优先 | **`latin_mesh`**（0.0023 ROM） | λ≳16 换手；面积省 ~22× |
| broadcast | **`islip_mesh` I=1**（0.0018） | 单 VOQ，一致性 100%，最快且最省 |
| 面积敏感 + 树形 collective | **`bvn_mesh`**（0.0018） | 轮次已达下界 |
| torus + 短 alltoall | **`islip_mesh`** | VOQ 口径下 torus/mesh≈0.55× |
| torus + 长 alltoall | **`mwm_mesh`** | 下界降到 60 轮后「更少轮次」才值得付面积 |
| 迭代轮数 I | **恒取 I=1** | I>1 几乎不改善 DES，却线性放大 T_sched |
| 切勿 | `bcfs`、mesh 上的 `mwm_mesh` | 全程不在 Pareto 前沿 |
| 切勿 | 把 alltoall 的 N−1 条 VOQ 聚合成 1 条 request 当基线 | 会系统性低估控制汇聚税（R_rg 175 vs ~2000） |

## 8. 验证清单

| # | 项 | 结果 |
|---|----|------|
| 1 | 对分带宽相等（数据面） | ✓ |
| 2 | torus 数据 σ=0.5 | ✓ |
| 3 | FIFO alltoall m=1 ≈ 188 | ~192 |
| 4 | bufferless 零驻留（117 组） | ✓ |
| 5 | 保序 | ✓ |
| 6 | torus CDG 无环 | ✓ |
| 7 | 控制收敛 ≥ 564 量级 | ✓（1887） |
| 8 | 私有控制 NoC `shared_with_data_plane=false` | ✓ |
| 9 | 中心调度器坐标一致为 (4,0)，nid=4 | ✓ |
| 10 | 控制平面 XY 维序路由 | ✓ |
| 11 | Request 到达时刻确实离散（67 组多源配置 spread>0） | ✓ |
| 12 | **BCFS 全局无冲突**（79 组逐链路复核，冲突数 0） | ✓ |
| 13 | CA-batch bufferless 回放零驻留（49 组） | ✓ |
| 14 | 点对点 BCFS 从不劣于 FCFS（43 组） | ✓ |
| 15 | 单播单调性 bufferable≲bufferless（18 组周期级精确行） | ✓ |

§6 排程算法族专项（660 行全覆盖）：

| # | 项 | 结果 |
|---|----|------|
| 16 | 全部 7 个算法 reservation 无重叠（`conflict_free`） | ✓ 660/660 |
| 17 | bufferless 回放 `max_residency=0` 且预约窗口自洽 | ✓ 330/330 |
| 18 | LDPS 轮次数 ≥ `max_e load(e)`（Birkhoff 置换数界类比） | ✓ 540/540 |
| 19 | 相位型算法**同轮**流的链路集独立复核两两不相交 + ramp ≤ RAMP_BW·σ | ✓ 540/540 |
| 20 | 面积模型标定：`greedy_ff` @2256 流 = `ARB_AREA['ca']` = 0.0500 | ✓ |
| 21 | 同一负载下相位型相关仲裁步数 < 流水型（Θ(轮次) vs Θ(流数)） | ✓ 60/60 |
| 22 | alltoall 下全路径一致 accept 成功率 < 50%（活锁证据） | ✓（~18%） |
| 23 | **VOQ 纪律**：alltoall 一 request=一 VOQ，每源 47、全网 2256，`aggregate=False` | ✓ |

## 9. 已知局限

- 数据面金属线 torus/mesh≈1.17；折叠线长×2 用 `torus_delay_scale=2` 对照。
- 私有控制 NoC 是**额外**金属，不计入数据面对分恒定。
- **BCFS 是在线窗口局部最优**：CA 只能给眼前这一批打分，无法预知后续窗口。
  多树 pattern（mesh allgather m=16）出现 2 组「本批更紧、全局更慢」的反向案例（最差 +4.6%）；
  点对点 pattern 上未出现。允许离线全局排程（W=∞，所有 request 先到齐）可消除此效应。
- BCFS 路径固定为 XY 维序；允许自适应路由会扩大可行域，但需重做 CDG 无环性论证。
- 多树 bufferable（48 棵树 allgather）与 2256 流 alltoall 用事件驱动单播展开近似，
  共享树边被重复计数、head-of-line 停顿被高估，是**保守上界**（torus σ=2 上偏差最大）。
  严格单调性验证只覆盖周期级精确的 18 组。
- 控制面单向时延 = ⌊曼哈顿线延迟/2⌋；未再为窄线单独标定电气参数。
- reduce = **gather + PE 本地归约**（ADR-002 / Arch-A2），无网内算术。
- **§6 的面积/时序是结构性解析模型，不是综合结果**：门级深度按 12 级/周期折算，比较器按 0.6 flop-bit/位估，
  ROM 位按 0.15 折算。绝对值有系数不确定性；但两类算法 Θ(轮次数) vs Θ(流数) 的数量级差异不依赖标定，
  λ 敏感度表已给出系数偏差下冠军换手的边界。
- **§6 的相位型成员含一个「路径级顺序补齐」步**：纯逐链路 RR 的全路径一致 accept 成功率趋零（§6.2），
  严格只保留分布式一致性会活锁。这压平了 I 的收益——是 mesh 的结构性结论，不是实现取舍。
- **`bcfs` 按串行多起点计价**（5×流数步）。若改为 5 份硬件并行，时间回 1×、面积升约 5×，
  在 Pareto 图上是沿前沿平移而非跨越，「被 `greedy_ff` 支配」的结论不变。
- §6 全部成员的路径固定为 XY 维序，LDPS 的链路不相交判定不含路由自由度。

## 10. 文件

| 文件 | 作用 |
|------|------|
| `utils/rg_topo.py` | mesh/torus 数据拓扑，CA 位置 (4,0) |
| `utils/rg_bounds.py` | 下界（含控制入端口收敛） |
| `utils/rg_collectives.py` | 五 pattern |
| `utils/rg_arbiter.py` | 私有控制 NoC DES + CA/DA 即时调度 |
| `utils/rg_batch_sched.py` | **错峰 request + XY 控制路由 + 时间窗批量仲裁 + BCFS 全局无冲突排程 + 独立冲突检查器** |
| `utils/dse_rg_noc_8x6.py` | 数据面 DES + 扫描 + 验证 |
| `utils/rg_mesh_sched.py` | **§6 算法族**：LDPS 原语 + 7 个成员（相位型 5 + 流水型 2）+ 轮次独立复核 |
| `utils/rg_sched_cost.py` | **§6** 解析资源模型：状态比特/比较器 → 归一面积；门级深度 → `T_sched` |
| `utils/dse_mesh_sched_pareto.py` | **§6** 扫描 algo × pattern × m × topo × plane（+I）→ `mesh_sched_pareto.json` |
| `utils/gen_mesh_sched_pareto_plot.py` | **§6** Pareto 面板图 / 每 pattern 大图 / λ 敏感度图 |
| `utils/gen_rg_noc_report.py` | HTML（按 pattern 归类） |
| `results/rg_noc_8x6.json` / `report_rg_noc_8x6.html` | 数据与报告 |
| `results/mesh_sched_pareto.json` / `mesh_sched_pareto*.png` / `mesh_sched_lambda.png` | §6 数据与图 |
