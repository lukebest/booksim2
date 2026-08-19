# 2-full-ring 20 节点：五方案 makespan + request-grant Pareto

**几何：** 20 节点；偶数 index = AI core，奇数 = memory Home Agent；节点 19 与 0 相邻。
**Fabric：** 两个独立的并行 ring plane，每个 plane 自身双向。每节点每 plane 一个 inject/eject 端口，**plane 内双向共用同一 buffer**。有向段 `20 × 2 × 2 = 80`。相邻节点 **hop 时延 2 拍**。
**流量：** 读往返。core→HA 请求 1 flit，HA→core 响应 R flit。**makespan = 最后一个响应 flit 被 core PE drain 的拍。**
**Workload：** `allpairs`（10×10 每对 m 个事务，确定性）+ `uniform`（每 core 发 K 个事务，目的地在 10 个 HA 中均匀随机，多 seed）。
**共同数据面（五方案相同）：** 点对点 credit-based flow control + **8 深上环队列** + I-tag + E-tag。
**五方案只改注入/调度策略：** S0 在有 credit 时 RR 上环；S1 再加失败计数 piggyback + AIMD 源端速率；S2 同一数据面上做 request-grant（iSLIP 族）；S3 读请求作 POP 调度信息，HA 调度后给响应；S4 同 S0 数据面 + kind-aware leave（core 优先下响应，HA 优先下请求）。
**验证：** `results/verify_ring2_20.json`，检查项可执行、失败即点名具体量。

## 0. 和仓库里已有 ring 研究的关系

`utils/rg_ring_base.py` 是 8×6 维度切片 2D 环（行环 + 列环，每个节点是桥，有 transfer FIFO 和 Swap Rule）。**本拓扑没有转弯**，因此：

- 没有 transfer FIFO，没有 Swap Rule，没有 R4（转环零松弛）。
- E-tag 从「预留 Tx / transfer FIFO」**改绑到预留 eject 条目**。这是本研究的改编，不是 HiRD / HPCA'22 原语义，文档必须写明。
- 冲突只剩 R1（弧互斥）/ R2（上环点）/ R3（下环点，两方向共享 leave 端口）。

面积模型沿用 `rg_sched_cost.py` 的 bit-equivalent，校准锚点仍是 8×6 mesh 的 `greedy_ff = 0.05` IQ-XY。20 节点数字与 8×6 可比，但**不是 mm²，不能当流片估算**。

## 1. 拓扑与路由

节点 `i` 的角色：`is_core(i) ⇔ i % 2 == 0`。最短方向，平局走 CW（+1）。Plane 选择是注入/调度策略，不是拓扑事实：

| `plane_sel` | 行为 |
|---|---|
| `static_hash` | `(src + 3·dst + kind) mod 2` |
| `rr_per_pkt` | 每源轮转 |
| `least_occupied` | 当前占用更少的 plane（默认） |
| `req_resp_split` | 请求 plane 0，响应 plane 1 |

## 1.5 makespan 的理论下界（形式化）

记号：`N=20` 节点，`P=2` plane，`σ=1` 拍/flit（同一有向段上连续两 flit 的最小间隔），`λ=2` 拍（相邻节点 hop 时延），有向段 `2PN=80`。事务集合 `T`，每个 `t` 是 core→HA 的 `m_req` flit 请求 + HA→core 的 `m_resp` flit 响应，路径 `π(t)` 取最短方向。

四条下界都是「某资源必须搬的总量 ÷ 该资源容量」的计数论证，对**任何**调度策略成立，包括离线最优：

**LB_link（段带宽）** 对每条不区分 plane 的有向邻接 `(u,v)`，令
`L(u,v) = Σ_t m_req·[⟨u,v⟩∈π_req(t)] + m_resp·[⟨u,v⟩∈π_resp(t)]`，则
`makespan ≥ σ·⌈ max_(u,v) L(u,v) / P ⌉`。
必须除以 `P`：plane 分配是策略不是物理约束，同一有向 hop 两个 plane 都能承载。用某个具体 `plane_sel` 的单 plane 峰值会在仿真器平衡得更好时反过来超过实测值，那就不是下界。

**LB_port（端口）** 令 `B(n)` / `E(n)` 为节点 `n` 上必须上环 / 下环的 flit 总数（跨 plane 合并、请求+响应合并），每节点每 plane 各 1 个 inject / eject 口，则
`makespan ≥ σ·⌈ max_n max(B(n), E(n)) / P ⌉`。

**LB_cut（二等分割）** 环的二等分要切**两个**缺口。取 `X = {⟨N/2−1,N/2⟩, ⟨N/2,N/2−1⟩, ⟨N−1,0⟩, ⟨0,N−1⟩} × P`，共 8 条有向段；`C` 为路径穿过 `X` 的 flit-段数，则 `makespan ≥ σ·⌈C/|X|⌉`。数实际穿越次数——core/HA 交错布局下大量路径只有 1 跳，套用「一半流量过割」会高估到超过实测。

**LB_txn（单事务串行）** 事务内部严格串行，取最深的那个：
`makespan ≥ max_t [ hops(π_req(t))·λ + m_req·σ + t_ha + hops(π_resp(t))·λ + m_resp·σ ]`。

`bound = max(LB_link, LB_port, LB_cut, LB_txn)`。实测对照：

| 档位 | LB_link | LB_port | LB_cut | LB_txn | bound | S0 | S1 | S2 | S3 | S4 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| allpairs m=1 R=4（100 事务） | 35 | 20 | 32 | **41** | 41 | 129 (3.15×) | 122 (2.97×) | 88 (2.15×) | 129 (3.15×) | 122 (2.98×) |
| uniform K=2500 R=4（25000 事务） | **8939** | 5168 | 7788 | 41 | 8939 | 14886 (1.66×) | 18170 (2.03×) | **10044 (1.12×)** | 14886 (1.66×) | 15075 (1.69×) |

两档的主导项不同：allpairs 事务太少、资源计数没饱和，瓶颈是单事务往返时延；10k 那档段带宽主导，S2 做到 1.12× 物理极限。每核对齐 512 条 outstanding 之后，S3 与 S0 在这两档重合（S3 的 HA RR 不再是瓶颈）。S4 在 allpairs 上与温和 AIMD 均值同速、支配 S0；10k 上略慢于 S0——kind-aware leave 解的是 leave 口上的种类冲突，不是段带宽。

**为什么不紧。** 每条都是一次松弛：(1) 除 `LB_txn` 外丢掉了请求→响应依赖，把两波当独立车队——这是 allpairs 那档 bound 只有 41 的直接原因；(2) 不含偏转，假设每 flit 只走最短路，S0 实测 flit-hop 比最短路多约 40%；(3) plane 分配当自由变量；(4) 不含上环队列深度、leave 端口冲突、I/E-tag 抑制；(5) 四项各自取 max，没有联立，真正的 LP 松弛会更高。

## 2. 共同数据面，然后才是 S0 / S1 / S2 / S3 / S4

五方案跑在**同一条**数据面上，不是五种 fabric。

| 层 | S0 RR | S1 AIMD | S2 request-grant | S3 push-on-pull | S4 kind-aware leave |
|---|---|---|---|---|---|
| 相邻节点 hop 时延 | 2 拍 | 2 拍 | 2 拍 | 2 拍 | 2 拍 |
| 上环队列（每 node, plane） | 8 flit | 8 flit | 8 flit | 8 flit | 8 flit |
| 下环队列（每 node, plane） | 4 + 1 E-tag | 4 + 1 E-tag | 4 + 1 E-tag | 4 + 1 E-tag | 4 + 1 E-tag |
| inject / eject 端口 | 每 (node, plane) 1 个 | 同左 | 同左 | 同左 | 同左 |
| 点对点 credit FC | 有 | 有 | 有 | 有 | 有 |
| I-tag（上环饥饿有界） | 有 | 有 | 有 | 有 | 有 |
| E-tag（下环 / 预留 eject） | 有 | 有 | 有 | 有 | 有 |
| 每核 outstanding 读 | 512 | 512 | 512 | 512 | 512 |
| 有 credit 时 RR 上环 | 有 | 有 | — | 有（读请求受 512/核 outstanding 卡） | 有 |
| AIMD 源端速率（失败 piggyback） | — | 有 | — | — | — |
| 上环前 request-grant 匹配 | — | — | 有 | — | — |
| 读请求作 POP 调度信息 | — | — | — | 有（HA RR 响应） | — |
| kind-aware leave | — | — | — | — | 有（core 先 resp / HA 先 req，无额外 bit） |

**Credit：** 每条有向 hop 是一对 credit。上游发 flit 先扣 credit，下游槽位空出后归还。没有 credit 不准发。80 条有向段（20 × 2 plane × 2 方向）。

**上环队列：** 每 (node, plane) 8 flit，plane 内双向共用。PE 把 flit 交给 fabric 外的 backlog，只有队列有空位才 admit，所以注入点是**真反压**，不是把整批流量一次吞下。

**I-tag：** 某源在某 (plane, dir) 上饿 `t_inj` 拍后升 I-tag，抑制该环向上其他节点上环，直到自己上去。

**E-tag：** 下环失败（共享 eject 队列满，或该拍唯一的 leave 端口已被占）`t_xfer` 次后升 E-tag，可以使用 `resv_ej` 条预留 eject 槽。失败则偏转，再绕一圈。改绑到预留 eject，不是 HiRD 的 transfer-FIFO E-tag。

**端口口径五方案一致：** 每 (node, plane) 每拍 1 次上环、1 次下环，即每节点每拍可上 2 个 flit（两个 plane 各 1）。S2 的 `("inj", node, plane)` / `("ej", node, plane)` 资源 key 与 S0 的 DES 对齐——早期版本按**节点**记 cap 1，等于把 S2 的端口天花板砍半，那是一个记账错误，已修。

S0（`rg_ring2_base.py`）是这条数据面上的反应式基线：周期精确 DES，优先级 **in-ring > inject**，有 credit 且 slot 空才 RR 上环。两方向 RR 争 leave 端口。失效模式是活锁 / 延迟长尾，不是死锁：每节点每 plane 每方向每拍最多到达 1 个 flit，偏转无条件可用。

## 3. S1 AIMD（`rg_ring2_aimd.py`）

上环、下环失败打在 flit 上。请求路径的计数随响应 piggyback 回 core。源端按 epoch 做 AIMD 令牌桶：

```
无失败: rate += alpha
有失败: rate *= beta
rate ∈ [rate_min, rate_max]
```

默认取温和配置：`α=0.15`、`β=0.85`、`epoch=64`、`rate_min=0.30`。教科书组合（0.05 / 0.5 / 32 / 0.05）在闭集中突发里会一路乘到地板，makespan 约 3× S0。

`aimd_scope=core_only` 只限 core 的请求注入；`both` 时 HA 用本地失败计数限响应注入（响应没有再回 HA 的报文）。

**预期（可证伪）：** 闭集中突发下，第一个 epoch 仍会看到上环 NACK；温和配置把地板托在 0.30，小流量可以赢 S0，10k 大约慢 20%。AIMD 的价值仍偏开环/持续负载，但不再是「一倒空就崩」。

## 4. S2 request-grant（`rg_ring2_rg.py`）

已知 workload，两波调度：先请求，响应的 `release = request_eject + t_ha`。每核 outstanding 超过 512 时按代切开：下一波请求的 `release` 是上一波响应的 eject。授权后传输刚性，但 **credit 计数、上环队列、I-tag、E-tag、共享 eject 仍在**：grant 只保证上环时 hop 已有 credit，所以本闭集中突发里 I/E-tag 几乎不被触发（上环失败 = 0），不是把它们从微结构里拿掉。算法表驱动：

`islip(I) | pim(I) | rr_oldest | lqf | ocf | bvn | greedy_ff | wavefront | batched_bcfs`

旋钮：冲突域 `arc` / `whole_ring`，`interval` / `free_at`，VOQ 粒度 `per_dst` / `per_plane_dir` / `grouped`，仲裁器 `central` / `per_plane` / `distributed_token`。

**计时约定与 mesh 家族相同：** `makespan = makespan_des + t_sched_cycles`。只在数据面上快、组合深度造不出来的算法，会被自己的调度延迟罚下去。

## 4.5 S3 push-on-pull（`rg_ring2_pop.py`）

读 memory 的请求**本身**就是 POP 调度信息，不再另走 1 bit notify / pull-token 控制面。对齐「egress 有窗口才发读、HA 按已到请求调度响应」，不是 S2 的 hop 预约。

- **请求：** core→HA 仍走 S0 数据面。五方案对齐：一条 outstanding 读占 1 个槽，每核最多 `core_outstanding`（默认 **512**）条未完成读才能再上环。可选的 `pop_window`（默认 0 = 关）是额外的 per-(core, resp plane) 上限。窗口不够计 `n_outst_wait` / `n_pull_wait`，**不计** `n_board_fail`。
- **目的侧：** 请求 PE drain 到 HA 后进入该 `(HA, resp_plane)` 的 pending。HA 每拍 RR 调度**一条**已到请求，放出该请求的整段响应（R flit）。
- **释放：** 该事务最后一拍响应在 core drain 后，归还 1 个 outstanding 槽。
- 默认 `pop_scope=req_as_grant`。`resp_only` 作消融：请求不卡窗口，HA 仍按到达请求调度。`both` 映射到 `req_as_grant`。
- 接收窗口把队头卡住时**必须摘掉 I-tag**（并清 starve）：I-tag 只保护「正在抢 hop 的源」。窗口否认不是 hop 饥饿；过期 I-tag 会把同向 HA 响应关在门口，环被抽空。S0/S1 的 `_may_inject` 否认走同一条路径。

和 S1 / S2 的差别：S1 看见的是本地上环 NACK；S2 预约弧段所以失败=0；S3 看见的是「这条读还能不能再发」以及 HA 面前有哪些已到请求，hop 仍可能忙。

## 4.6 S4 kind-aware leave（`rg_ring2_dist.py`）

分布式、零额外 bit：不改 credit / I-tag / 记分板，只改共享 leave 口上「谁先下」。core 上优先 eject **响应**（尽快还 outstanding），HA 上优先 eject **请求**（尽快放出响应）。其它本轮试过、默认关掉的旋钮：`resp_bypass_itag` / `no_req_itag`（I-tag 几乎从不升，无收益）、`ha_outst` / `req_slot`（K=500 无稳定赢）、`short_first`（更差）、`occ_yield`（死锁）。

**预期（可证伪）：** 在 `LB_txn` 主导的小 all-to-all 上，种类优先级能缩短依赖链等待，makespan 低于 S0 且面积相同。在 `LB_link` 主导的 10k 上它解不了段带宽，还可能比 S0 略慢。

## 5. 面积

五方案先付同一笔数据面：`credit_counters`（80 有向 hop）+ `boarding_queues`（每 (node, plane) 8 flit）+ 每 plane 共享 eject + E-tag 预留 + 重组缓冲 + I/E-tag 状态 + 每核 512 outstanding 记分板。没有 transfer FIFO，没有 Swap bypass。

`distributed_cost("ring2_base")`：上述共同数据面。

`distributed_cost("ring2_aimd")`：共同数据面 + 速率/令牌寄存器 + 失败计数 + piggyback 字段。

`distributed_cost("ring2_rg")`：共同数据面 + 仲裁器 `sched_cost(*_ring2)` + 一小笔控制面（central 0.08 / per_plane 0.05 / token 0.03，归一化/节点）。**不是站点存储 0。**

`distributed_cost("ring2_pop")`：共同数据面（已含每核 512 outstanding 记分板）+ HA pending/RR。读请求就是 grant，没有专用 pull-token 控制面。比 S0 贵、比带仲裁器的 S2 便宜。

`distributed_cost("ring2_dist")`：与 `ring2_base` 同位。kind-aware leave 是 mux 优先级，不占 bit。

## 6. 产物

| 文件 | 内容 |
|---|---|
| `utils/rg_ring2_topo.py` | 拓扑、角色、路径、下界、workload |
| `utils/rg_ring2_base.py` | S0 DES |
| `utils/rg_ring2_aimd.py` | S1 AIMD |
| `utils/rg_ring2_rg.py` | S2 调度 + 回放 |
| `utils/rg_ring2_pop.py` | S3 NGSF 式 push-on-pull |
| `utils/rg_ring2_dist.py` | S4 分布式 leave / 本地策略 |
| `utils/dse_ring2_20node.py` | 五方案 makespan |
| `utils/dse_ring2_rg_pareto.py` | 面积-性能 Pareto（`--refine` 给 loop 用） |
| `utils/verify_ring2_20.py` | 可执行断言 |
| `results/ring2_20node.json` | 五方案扫 |
| `results/ring2_rg_pareto.json` / `.png` | Pareto |
| `results/verify_ring2_20.json` | 门禁 |
| `results/report_ring2_20node.html` | 报告 |
| `results/ring2_core_recv_bw_allpairs.png` | 每核接收带宽（allpairs） |
| `results/ring2_core_recv_bw_uniform.png` | 每核接收带宽（uniform K=20） |
| `utils/dse_ring2_core10k.py` | 同 pattern、每核 10000 响应 flit 的 S0–S4 对比 |
| `results/ring2_core10k.json` | 10k 每核接收曲线（分箱）+ 上环 / 队列统计 |
| `results/ring2_core_recv_bw_10k.png` | 五方案每核接收带宽（aligned x） |
| `results/ring2_core_recv_bw_10k_overlay.png` | 五方案均值叠图 + 解析下界理想接收 |

## 7. 实测（allpairs m=1 R=4 / uniform 多 seed，plane_sel=least_occupied）

闭集中突发、验证 19/19 通过。数据面 makespan（S2 不含 `t_sched_cycles`）：

| 方案 | allpairs m=1 R=4 | uniform K=20 R=4 | uniform K=100 R=4 |
|---|---|---|---|
| S0 | 129 | 188 | 669 |
| S1 | 122（AIMD 配置均值；最好 115） | 173 | 701 |
| S2 iSLIP I=2 | **88** | **145** | **526** |
| S3 push-on-pull | 129 | 188 | 669 |
| S4 kind-aware leave | **122** | 184 | 669 |
| 解析下界 | 41 | 95 | 376 |

**Pareto 图上 S2 有 109 个点，S0 / S1 / S3 / S4 各 1 个。** 不是画重了：S0、S1、S3、S4 各自只有一种硬件结构，而 request-grant 的仲裁器是可设计对象，每组旋钮取值对应一块不同的、都可实现的电路，面积和调度延迟都不同，必须单独评估。旋钮空间 = 算法 9 种（`islip, pim, rr_oldest, lqf, ocf, bvn, greedy_ff, wavefront, batched_bcfs`）× 迭代轮数（islip/pim 取 1,2,4，其余仅 1）→ 13 种组合，× 冲突域 2（`arc` / `whole_ring`）× 占用表示 2（`interval` / `free_at`）× 仲裁器 2 = 104，再加一片补充切片（VOQ 粒度、token 仲裁器、带 RTT 流水线）去重后 109。这些点绝大多数被支配，作用是把前沿撑出来——一个只画自己最好配置的 S2 无法反驳，画满 109 个之后仍只有一个配置留在前沿，结论才有分量。y 轴已把 `t_sched_cycles` 计回，所以 `batched_bcfs` 这类纯数据面极快（DES 几十拍）但组合深度换算出上千拍调度延迟的算法，会自己把自己罚出前沿。

hop 时延 2 拍、上环队列 8 深、端口口径与每核 512 outstanding 对齐之后，**S2 在数据面上稳定赢 S0**：allpairs 88 vs 129，uniform K=100 是 526 vs 669。把 `t_sched_cycles` 计回之后仍有配置留在前沿——`rr_oldest/I1/arc/int/central/per_plane` 在 area 0.1997 拿到 makespan 106。S4 与 S0 同面积 0.0444、allpairs 122 vs 129，因此**支配 S0**。**Pareto 前沿现在是三点：S4、S1、S2 的 rr_oldest。** S3（area 0.0451 / mk 129）与 S0 同速、略贵，仍被支配。换 S2 的 makespan 要付约 4.5× 面积，值不值取决于系统层。

S1 默认用温和 AIMD（α=0.15 / β=0.85 / epoch=64 / rate_min=0.30）。allpairs 均值 122、最好 115，压过 S0 的 129；K=20 也赢（173 vs 188）。流量再大开始落后：K=100 是 701 vs 669，10k 是 18170 vs 14886（1.22×）。教科书组合（0.05 / 0.5 / 32 / 0.05）会把 10k 打到 3×，那是地板太低，不是 AIMD 本身不能用。

S3 用读请求当调度信息：五方案对齐为每核最多 512 条 outstanding 读，HA 对已到请求 RR 后给整段响应。allpairs / K≤100 都远小于 512，S3 与 S0 重合。窗口否认时摘掉 I-tag 之前，uniform 曲线中间会大段掉到 0——过期 I-tag 把 HA 响应关在门口。

S4 用 leave 口的种类优先级：allpairs 122（2.98× bound），K=20 略赢 S0（184 vs 188），K=100 持平，10k 反而慢到 15075。它解的是「谁先占用共享 leave 口」，不是段带宽，也不是 S2 那种 hop 预约。

## 8. 跑法

```bash
python3 utils/verify_ring2_20.py
python3 utils/dse_ring2_20node.py          # --quick 做冒烟
python3 utils/dse_ring2_rg_pareto.py       # --refine 加密当前前沿
python3 utils/dse_ring2_core10k.py         # 同 pattern 10000 flit/core；--quick 冒烟
python3 utils/gen_ring2_report.py
```

## 9. 同 pattern、10000 响应 flit / core

Workload 固定：`uniform` K=2500、R=4、seed=0，`plane_sel=least_occupied`。每个 core 收 **10000** 个响应 flit（10 core × 2500 txn × 4 flit）。五方案吃同一批事务，同一条数据面（hop 2 拍、上环队列 8 深、下环 4+1、每核 outstanding 512）。S1 用温和 AIMD：α=0.15、β=0.85、epoch=64、rate_min=0.30。

| 方案 | makespan | 上环成功 | 上环失败 | 偏转 | 上环队列峰值 | 下环队列峰值 | 响应时延 p50 / p99 | outstanding 峰值 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S0 RR | 14886 | 100000 | 189434 | 11348 | 8 | 1 | 2758 / 3817 | **512** |
| S1 AIMD | 18170 | 100000 | 118628 | 9381 | 8 | 1 | 23 / 205 | 54 |
| S2 iSLIP I=2 | **10044** | 100000 | 0 | 0 | 见下 | 0 | — | 509 |
| S3 push-on-pull | 14886 | 100000 | 189434 | 11348 | 8 | 1 | 2758 / 3817 | **512** |
| S4 kind-aware leave | 15075 | 100000 | 190870 | 11359 | 8 | 1 | 2790 / 4003 | **512** |

按目的 core 的响应上环（方向由 HA→core 最短路决定，五方案 CW/CCW 逐核相同；失败只计 slot 忙或 I-tag，**不含** AIMD 令牌拒绝、也不含 outstanding 等待）：

| core | 上环 (CW / CCW) | S0 失败 | S1 失败 | S2 失败 | S3 失败 | S4 失败 |
|---|---|---:|---:|---:|---:|---:|
| 0 | 10000 (5008 / 4992) | 18575 | 11449 | 0 | 18575 | 18187 |
| 2 | 10000 (4888 / 5112) | 19286 | 11983 | 0 | 19286 | 20225 |
| 4 | 10000 (4920 / 5080) | 19114 | 12256 | 0 | 19114 | 18599 |
| 6 | 10000 (4924 / 5076) | 18488 | 12813 | 0 | 18488 | 18793 |
| 8 | 10000 (5124 / 4876) | 19894 | 12467 | 0 | 19894 | 19305 |
| 10 | 10000 (4828 / 5172) | 18589 | 12235 | 0 | 18589 | 19344 |
| 12 | 10000 (5056 / 4944) | 18678 | 11755 | 0 | 18678 | 18780 |
| 14 | 10000 (4912 / 5088) | 19640 | 10561 | 0 | 19640 | 19550 |
| 16 | 10000 (5032 / 4968) | 19087 | 11669 | 0 | 19087 | 19284 |
| 18 | 10000 (5044 / 4956) | 18083 | 11440 | 0 | 18083 | 18803 |
| **合计** | **100000 (49736 / 50264)** | **189434** | **118628** | **0** | **189434** | **190870** |

**S2 最快（10044 vs S0 14886，快 32%）。** 失败为 0 不是因为没有 I-tag / E-tag，而是 grant 只在 hop 已空时发出，反应式标签在本闭集中突发里用不上。分代预约把每核 in-flight 压在 512 附近（峰值 509，同拍完成/起飞交错）。

S3 的 100000 个响应都是 HA 按已到读请求调度后放出的（`n_pull_issued=100000`）。五方案 outstanding 对齐为每核 512 之后，S3 与 S0 的 10k 曲线重合：HA 一拍一条请求的 RR 不再卡吞吐，瓶颈回到共享数据面。响应上环仍不预约 hop。

S4 的 10k 均值曲线贴着 S0，但 makespan 更长（15075 vs 14886，1.01×），上环失败略多（190870 vs 189434）。leave 种类优先级在段带宽饱和时帮不上忙，还会打乱 RR 对两个方向的公平。

S0 / S1 / S4 的上环队列都是 8 深且都跑满（峰值 8，S0 有 511885 次 admission stall，S4 有 516880），说明反压真的在起作用。**下环队列峰值只有 1**：4 深的 eject FIFO 在这个负载下根本用不上，S0 的 11348 次偏转来自「每 (node, plane) 每拍只有 1 个 leave 端口，两个方向同拍到达必有一个被挤掉」，不是队列满。E-tag 也因此几乎不触发。真正的限制是 leave 端口数，不是队列深度。

S2 的上环队列占用没法直接比：调度器给出的是刚性 t0，源端提前知道自己什么时候上环，所以只需在 t0 前把 flit 挪进队列，fabric 里的队列需求约为 1。但「已生成、尚未上环」的量峰值达 **267**（`max_src_wait`，512 分代之后从原先整批 25000 的 1249 降下来），这些 flit 停在 PE 侧 backlog 里——S2 把排队从 fabric 推到了源端。

S1 用温和 AIMD 之后，10k makespan 从教科书参数的 45748 收到 **18170**（S0 的 1.22×）。上环失败 118628，仍低于 S0 的 189434；响应 p50 / p99 为 23 / 205（S0 是 2758 / 3817）；outstanding 峰值 54。仍然是在拿一点吞吐换时延，但不再把速率打到地板。

接收带宽图：`results/ring2_core_recv_bw_10k.png`（五面板、共用 x）和 `results/ring2_core_recv_bw_10k_overlay.png`（均值叠图；黑点线是解析下界对应的匀速接收，`bound=8939` 拍、1.12 flit/cycle/core）。 S3 虚线叠在 S0 上；S4 橙色。
