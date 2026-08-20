# 2-full-ring 20 节点：九方案 makespan + request-grant Pareto

**几何：** 20 节点；偶数 index = AI core，奇数 = memory Home Agent；节点 19 与 0 相邻。
**Fabric：** 两个独立的并行 ring plane，每个 plane 自身双向。每节点每 plane 一个 inject/eject 端口，**plane 内双向共用同一 buffer**。有向段 `20 × 2 × 2 = 80`。相邻 hop 时延按边：`2,2,2,3,1,3,1,1,2,4,1,1,3,1,3,2,2,2,3,3`（最后一项是 HA19 ↔ C0）。
**流量：** 读往返。core→HA 请求 1 flit，HA→core 响应 R flit。**makespan = 最后一个响应 flit 被 core PE drain 的拍。**
**Workload：** `allpairs`（10×10 每对 m 个事务，确定性）+ `uniform`（每 core 发 K 个事务，目的地在 10 个 HA 中均匀随机，多 seed）。
**共同数据面（九方案相同）：** 点对点 credit-based flow control + **8 深上环队列** + I-tag + E-tag。
**九方案只改注入/调度策略：** S0 在有 credit 时 RR 上环；S1 再加失败计数 piggyback + AIMD 源端速率；S2 同一数据面上做 request-grant（iSLIP 族）；S3 读请求作 POP 调度信息，HA 调度后给响应；S4 同 S0 数据面 + kind-aware leave；S5 预约 dest leave 时隙（同拍留节点号更小的源）；S6 同 S5 预约表，同拍 dest 冲突留最老的 flit；S7 同 S6，本 plane 第一跳被占时改绑到另一 plane；S8 注入时现场选 hop+dest 都空的 plane。
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

| 档位 | LB_link | LB_port | LB_cut | LB_txn | bound | S0 | S1 | S2 | S3 | S4 | S5 | S6 | S7 | S8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| allpairs m=1 R=4（100 事务） | 35 | 20 | 32 | **41** | 41 | 129 (3.15×) | 122 (2.97×) | 88 (2.15×) | 129 (3.15×) | 122 (2.98×) | 100 (2.44×) | 100 (2.44×) | 83 (2.02×) | **72 (1.76×)** |
| uniform K=2500 R=4（25000 事务） | **8939** | 5168 | 7788 | 41 | 8939 | 14886 (1.66×) | 18170 (2.03×) | **10044 (1.12×)** | 14886 (1.66×) | 15075 (1.69×) | 13522 (1.51×) | 13200 (1.48×) | 12824 (1.43×) | **11971 (1.34×)** |

两档的主导项不同：allpairs 事务太少、资源计数没饱和，瓶颈是单事务往返时延；10k 那档段带宽主导，S2 做到 1.12× 物理极限。每核对齐 512 条 outstanding 之后，S3 与 S0 在这两档重合（S3 的 HA RR 不再是瓶颈）。S4 在 allpairs 上与温和 AIMD 均值同速、支配 S0；10k 上略慢于 S0——kind-aware leave 解的是 leave 口上的种类冲突，不是段带宽。

**为什么不紧。** 每条都是一次松弛：(1) 除 `LB_txn` 外丢掉了请求→响应依赖，把两波当独立车队——这是 allpairs 那档 bound 只有 41 的直接原因；(2) 不含偏转，假设每 flit 只走最短路，S0 实测 flit-hop 比最短路多约 40%；(3) plane 分配当自由变量；(4) 不含上环队列深度、leave 端口冲突、I/E-tag 抑制；(5) 四项各自取 max，没有联立，真正的 LP 松弛会更高。

## 2. 共同数据面，然后才是 S0 / S1 / S2 / S3 / S4

九方案跑在**同一条**数据面上，不是九种 fabric。

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

## 4.7 S5 leave-slot lock（`rg_ring2_dist.py` `ej_lock`）

每 (node, plane) 每拍只有 1 个 leave 口。两个方向的 flit 同拍到达，必有一个偏转再绕一圈。S5 在注入时用 `ETA = t + hops·λ` 预约 dest 的 leave 时隙；该槽已被占则本拍不上环（FIFO，不跳 dest）。同拍多个候选按节点号留 1 个。

这是分布式的：每个源只看 dest 的预约表，没有中心匹配。代价是一张约 64 拍窗口的 bitmap（`leave_slot_resv`）。偏转降到 0；上环仍可能因 hop 忙失败。

**预期（可证伪）：** 消灭偏转会砍掉 S0 多出来的 ~40% flit-hop，10k 和 allpairs 都比 S0 快，但仍落后预约整条弧的 S2。

## 4.8 S6 oldest dest clash（`rg_ring2_dist.py` `s6_params`）

S5 同拍多个源抢同一个 `(dst, plane, ETA)` 时按节点号留 1 个。节点号小的 core/HA 系统性地赢，10k 的尾核被拖长（p99 3816）。S6 改成留 `t_gen` 最早的 flit，预约表和面积与 S5 相同。

hop 前瞻（`hop_peek`）、1 拍/2 拍延迟预约、in-ring 改期（`ej_rebook`）、neighbor/ctrl1 弧锁在 10k 上都复现 S5 的 13522，没有单独出方案。HOL / dest-VOQ / inject-token 仍禁止做默认。

**预期（可证伪）：** allpairs 不回归（仍 100）；10k 严格低于 S5 的 13522，p99 缩短。

## 4.9 S7 hop bounce（`rg_ring2_dist.py` `s7_params`）

plane 在 offer 时就用 `least_occupied` 绑死。到注入拍，本 plane 第一跳常常已经被过路 flit 占住，另一 plane 的同向 hop 却是空的。S7 在 S6 预约表上加 `hop_bounce`：第一跳忙则看另一 plane——dest leave 也空才改绑。这和 `plane_bounce`（只在 dest 冲突时换）不是一回事；后者对 S6 是空操作。

代价仍是 `ring2_ej`：两个 plane 的第一跳占用本来就在本节点上，不新增预约 bit。`hb+pb` 组合 10k 不稳定（seed 1 比单 hop_bounce 差），默认只开 hop_bounce。

**预期（可证伪）：** 10k 严格低于 S6 的 13200，allpairs 不大幅回归。

## 4.10 S8 late plane（`rg_ring2_dist.py` `s8_params`）

S7 只在本 plane 第一跳忙时换。dest 已被占时 `_may_inject` 更早返回，换 plane 的机会用不上。S8 在 dest/hop 检查之前现场看两个 plane：hop 和 dest leave 都空的才能上；两个都能上就走当前占用更低的（`late_plane=occ`）。`need`（只在本 plane 不能上时才换）10k 是 12092，略慢于 occ 的 11971，所以默认 occ。

不新增预约 bit，面积仍是 `ring2_ej`。

**预期（可证伪）：** 10k 严格低于 S7 的 12824，allpairs 不大幅回归。

## 4.11 S9 late dir（`rg_ring2_dist.py` `s9_params`）

S8 的 10k stall 拆开之后，dest leave 拒绝只有 4675，同拍 dest hold 46959，512 outstanding 卡 85228，**第一跳 hop 拒绝 98376**（HA 响应 67945 / core 请求 30431）。环上 in-ring 从不 stall。`dest_old`（同 dest 已有更老 in-flight）allpairs 89、K=500 3372+；`nbr_adv`（邻居本拍注入广告）K=500 赢到 2511，10k 炸到 13300。都不作为默认。

S9 打的是「两个 plane 的本方向第一跳都忙」：若另一环方向绕路不超过 +2 hop，且 hop+dest leave 空，就改 `dir`/`target`。`late_dir=tie`（只允许等长）是空操作。不新增预约 bit，面积仍是 `ring2_ej`。

**预期（可证伪）：** 10k 严格低于 S8 的 11971，allpairs 不大幅回归。

实测：10k seed0 **11809**（1.32× bound，S8 11971），seed1/2 11864/11889。allpairs 73。dest-core board_fail 67945→62971。偏转 0。allpairs Pareto 仍是 S4 / S1 / S8（同面积 S9 被 72 支配）。

## 4.12 S10 resp-only late dir（`rg_ring2_dist.py` `s10_params`）

S9 对请求和响应都改向。10k 的第一跳拒绝主要是 HA 响应（67945 vs core 请求 30431）。S10 只对 `resp` 做 `late_dir=slack`，请求仍走最短路。`slack=1` 是空操作；`slack=4` 与 S9 相同；`slack=8` 10k 12085；`late_dir_hold`（最短路下一拍就空则等）K=500 输。面积仍是 `ring2_ej`。

**预期（可证伪）：** 10k 严格低于 S9 的 11809，allpairs 不大幅回归。

实测：10k seed0 **11781**，seed1/2 11729/11821。allpairs **69**（压住 S8 的 72）。dest-core board_fail 62971→61870。偏转 0。allpairs Pareto 变为 S4 / S1 / **S10**。

## 4.13 S11 hop hold（`rg_ring2_dist.py` `s11_params`）

S10 的剩余缺口仍是第一跳：`active_src` 的哈希序决定同拍谁先占用 hop。S11 在 `_pre_inject` 里按（plane, dir, idx）分组 HOL，只留最老的响应，其余本拍等待。不预约未来 hop（不是 hop_book / hop_peek / hop0_cred）。`hop_hold` 对请求也开 10k 11507；只对响应开更好（11451，allpairs 67）。dest-aware late_dir（cooler / pick / eager）allpairs 或 K=500 都输，不作为默认。

**预期（可证伪）：** 10k 严格低于 S10 的 11781，allpairs 不大幅回归。

实测：10k seed0 **11451**（1.28× bound），seed1/2 11509/11493。allpairs **67**。dest-core board_fail 61870→52288。p99 4248→2512，p50 1568→2235。偏转 0。allpairs Pareto 变为 S4 / S1 / **S11**。

## 4.14 S12 hop islip（`rg_ring2_dist.py` `s12_params`）

S11 顺序做 dest hold 再 hop hold：dest 留给最老的 HOL，即使它随后抢不到 hop。S12 改成一波本地 request-grant：dest 先 grant，再在 dest-granted 里做 hop grant，两者都拿到才提交；hop 失败则该 dest 本拍让给下一名（不是 hop_joint 的一遍独立集，也不是 hop_hold_retry）。`hop_islip=2` 10k 11481，不作为默认。

**预期（可证伪）：** 10k seed0 严格低于 S11 的 11451，allpairs 不大幅回归。

实测：10k seed0 **11402**（1.28× bound），seed1/2 11458/11397。allpairs **68**（+1）。K=20 均值 134，K=100 均值 501。偏转 0。面积仍是 `ring2_ej`。allpairs Pareto 仍是 S4 / S1 / **S11**（同面积 S12 被 67 支配）。

## 4.15 S13 hop short（`rg_ring2_dist.py` `s13_params`）

S12 hop grant 在 dest-granted 里按年龄挑。S13 改成优先剩余 hop 更短的（年龄作次键）。不是 dest-aware hop_hold，不是 late_dir dest。kind-split（resp 先、req leftover）对 S12 是空操作（core / HA 不共享 dest 或第一跳）；req-first 10k 11439，不作为默认。

**预期（可证伪）：** 10k seed0 严格低于 S12 的 11402，allpairs 不大幅回归，且不能只赢一个 seed。

实测：10k seed0 **11288**（1.26× bound），seed1/2 **11399 / 11270**（三 seed 全赢）。allpairs **68**。K=500 **2362**。K=20 均值 135.6，K=100 均值 518.9（不及 S12）。偏转 0。面积仍是 `ring2_ej`。allpairs Pareto 仍是 S4 / S1 / **S11**（同面积 68 被 67 支配）。

## 4.16 S14 HA sibling plane（`rg_ring2_dist.py` `s14_params`）

第一跳冲突来自同节点跨 plane：`late_plane` 可以把两个 srcq 绑到同一 `(plane, dir, src)`。S14 在 peek 时若 HA 两条 srcq 撞车，短/老的留下，输家在 hop+dest 都空时换到另一 plane。dest-then-hop 于是看见两条 hop。不是 hop_hold_late，不是 hop_islip_busy，不是 late_dir_dest。只做 HA：core 侧 twin yield 把 allpairs 打到 70。两端都做（`late_plane_sib=1`）allpairs 71、10k 11135，不作为默认。

**预期（可证伪）：** 10k seed0 严格低于 S13 的 11288，三 seed 全赢，allpairs 不大幅回归（最好压过 S11 的 67）。

实测：10k seed0 **11043**（1.24× bound），seed1/2 **11224 / 11201**（三 seed 全赢）。allpairs **64**（压住 S11 的 67）。K=500 **2370**（不及 S13 的 2362）。偏转 0。面积仍是 `ring2_ej`。allpairs Pareto 前沿改成 S4 / S1 / **S14**。

## 5. 面积

五方案先付同一笔数据面：`credit_counters`（80 有向 hop）+ `boarding_queues`（每 (node, plane) 8 flit）+ 每 plane 共享 eject + E-tag 预留 + 重组缓冲 + I/E-tag 状态 + 每核 512 outstanding 记分板。没有 transfer FIFO，没有 Swap bypass。

`distributed_cost("ring2_base")`：上述共同数据面。

`distributed_cost("ring2_aimd")`：共同数据面 + 速率/令牌寄存器 + 失败计数 + piggyback 字段。

`distributed_cost("ring2_rg")`：共同数据面 + 仲裁器 `sched_cost(*_ring2)` + 一小笔控制面（central 0.08 / per_plane 0.05 / token 0.03，归一化/节点）。**不是站点存储 0。**

`distributed_cost("ring2_pop")`：共同数据面（已含每核 512 outstanding 记分板）+ HA pending/RR。读请求就是 grant，没有专用 pull-token 控制面。比 S0 贵、比带仲裁器的 S2 便宜。

`distributed_cost("ring2_dist")`：与 `ring2_base` 同位。kind-aware leave 是 mux 优先级，不占 bit。

`distributed_cost("ring2_ej")`：共同数据面 + 每 (node, plane) 64 拍 leave 时隙窗口。S5–S14 同位。

## 6. 产物

| 文件 | 内容 |
|---|---|
| `utils/rg_ring2_topo.py` | 拓扑、角色、路径、下界、workload |
| `utils/rg_ring2_base.py` | S0 DES |
| `utils/rg_ring2_aimd.py` | S1 AIMD |
| `utils/rg_ring2_rg.py` | S2 调度 + 回放 |
| `utils/rg_ring2_pop.py` | S3 NGSF 式 push-on-pull |
| `utils/rg_ring2_dist.py` | S4–S14 分布式 leave、late dir、hop hold / islip |
| `utils/dse_ring2_20node.py` | 十五方案 makespan |
| `utils/dse_ring2_rg_pareto.py` | 面积-性能 Pareto（`--refine` 给 loop 用） |
| `utils/verify_ring2_20.py` | 可执行断言 |
| `results/ring2_20node.json` | 十五方案扫 |
| `results/ring2_rg_pareto.json` / `.png` | Pareto |
| `results/verify_ring2_20.json` | 门禁 |
| `results/report_ring2_20node.html` | 报告 |
| `results/ring2_core_recv_bw_allpairs.png` | 每核接收带宽（allpairs） |
| `results/ring2_core_recv_bw_uniform.png` | 每核接收带宽（uniform K=20） |
| `utils/dse_ring2_core10k.py` | 同 pattern、每核 10000 响应 flit 的 S0–S14 对比 |
| `results/ring2_core10k.json` | 10k 每核接收曲线（分箱）+ 上环 / 队列统计 |
| `results/ring2_core_recv_bw_10k.png` | 十五方案每核接收带宽（aligned x） |
| `results/ring2_core_recv_bw_10k_overlay.png` | 十五方案均值叠图 + 解析下界理想接收 |

## 7. 实测（allpairs m=1 R=4 / uniform 多 seed，plane_sel=least_occupied）

闭集中突发、验证 29/29 通过。数据面 makespan（S2 不含 `t_sched_cycles`）：

| 方案 | allpairs m=1 R=4 | uniform K=20 R=4 | uniform K=100 R=4 |
|---|---|---|---|
| S0 | 129 | 188 | 669 |
| S1 | 122（AIMD 配置均值；最好 115） | 173 | 701 |
| S2 iSLIP I=2 | **88** | **145** | **526** |
| S3 push-on-pull | 129 | 188 | 669 |
| S4 kind-aware leave | 122 | 184 | 669 |
| S5 leave-slot lock | 100 | 160 | 595 |
| S6 oldest dest clash | 100 | 151 | 585 |
| S7 hop bounce | 83 | 159 | 573 |
| S8 late plane | 72 | 145 | 542 |
| S9 late dir | 73 | 145 | **519** |
| S10 resp late dir | 69 | 141 | 534 |
| S11 hop hold | **67** | **140** | **514** |
| S12 hop islip | 68 | **134** | **501** |
| S13 hop short | 68 | 135.6 | 518.9 |
| S14 HA sib plane | **64** | **129.7** | **504** |
| 解析下界 | 41 | 95 | 376 |

**Pareto 图上 S2 有 109 个点，S0–S14 各 1 个。** 同面积 0.0458 上 S14（mk 64）支配 S11（67）、S13（68）、S12（68）、S10（69）、S8（72）、S9（73）、S7（83）和 S5/S6（100）。 不是画重了：参考方案各自只有一种硬件结构，而 request-grant 的仲裁器是可设计对象，每组旋钮取值对应一块不同的、都可实现的电路，面积和调度延迟都不同，必须单独评估。旋钮空间 = 算法 9 种（`islip, pim, rr_oldest, lqf, ocf, bvn, greedy_ff, wavefront, batched_bcfs`）× 迭代轮数（islip/pim 取 1,2,4，其余仅 1）→ 13 种组合，× 冲突域 2（`arc` / `whole_ring`）× 占用表示 2（`interval` / `free_at`）× 仲裁器 2 = 104，再加一片补充切片（VOQ 粒度、token 仲裁器、带 RTT 流水线）去重后 109。y 轴已把 `t_sched_cycles` 计回。

hop 时延 2 拍、上环队列 8 深、端口口径与每核 512 outstanding 对齐之后，**S2 在纯数据面上仍最快**（allpairs DES 88，10k 10044）。把 `t_sched_cycles` 计回后，S14（area 0.0458 / mk 64）**支配**原先前沿上的 S11（0.0458 / 67）和 S2 `rr_oldest`（0.1997 / 106）。**Pareto 前沿现在是三点：S4（0.0444 / 122）、S1（0.0449 / 115）、S14（0.0458 / 64）。** S11 / S12 / S13 同面积，被 S14 支配。S0 被 S4 支配，S3 被支配。S2 要赢回前沿，必须把调度延迟压下去，或在更大流量上比面积。

S1 默认用温和 AIMD（α=0.15 / β=0.85 / epoch=64 / rate_min=0.30）。allpairs 均值 122、最好 115，压过 S0 的 129；K=20 也赢（173 vs 188）。流量再大开始落后：K=100 是 701 vs 669，10k 是 18170 vs 14886（1.22×）。教科书组合（0.05 / 0.5 / 32 / 0.05）会把 10k 打到 3×，那是地板太低，不是 AIMD 本身不能用。

S3 用读请求当调度信息：五方案对齐为每核最多 512 条 outstanding 读，HA 对已到请求 RR 后给整段响应。allpairs / K≤100 都远小于 512，S3 与 S0 重合。窗口否认时摘掉 I-tag 之前，uniform 曲线中间会大段掉到 0——过期 I-tag 把 HA 响应关在门口。

S4 用 leave 口的种类优先级：allpairs 122（2.98× bound），K=20 略赢 S0（184 vs 188），K=100 持平，10k 反而慢到 15075。它解的是「谁先占用共享 leave 口」，不是段带宽，也不是 S2 那种 hop 预约。

S5 预约 dest leave 时隙：allpairs 100（2.44× bound），K=20 / K=100 / 10k 都赢 S0（160 / 595 / 13522），偏转为 0。仍落后 S2 的数据面（88 / 145 / 10044），但面积只比 S0 多一张 64 拍窗口。

S6 把同拍 dest 冲突从节点号改成 oldest：allpairs 仍 100；K=20 160→151；K=100 595→585；10k 13522→13200（1.48× bound），p99 3816→3050。面积与 S5 相同。

S7 在第一跳忙时改绑 plane：allpairs 100→83（2.02× bound）；K=20 略回退到 159；K=100 585→573；10k 13200→12824（1.43× bound）。

S8 注入时现场选 hop+dest 都空的 plane：allpairs 83→**72**（1.76× bound，分布式首次快过 S2 DES 的 88）；K=20 **145**（与 S2 持平）；K=100 573→542；10k 12824→**11971**（1.34× bound）。面积与 S5–S7 相同。

S9 第一跳仍忙则改走另一环方向（≤+2 hop）：allpairs 73；K=100 **519**；10k **11809**（1.32× bound）。同面积被 S8 的 72 支配。

S10 只对响应做这次改向：allpairs **69**（1.68× bound，压住 S8）；K=20 **141**；K=100 534（不及 S9）；10k **11781**。

S11 同拍第一跳只留最老响应：allpairs **67**；K=20 **140**；K=100 **514**；10k **11451**。Pareto 前沿改成 S4 / S1 / **S11**。

S12 dest-then-hop request-grant：allpairs 68；K=20 **134**；K=100 **501**；10k **11402**。同面积被 S11 支配。

S13 hop grant 优先短路径：allpairs 68；K=20 135.6；K=100 518.9；K=500 **2362**；10k **11288**。同面积被 S11 支配。

S14 HA sibling plane yield：allpairs **64**；K=20 **129.7**；K=100 **504**；K=500 2370；10k **11043 / 11224 / 11201**。Pareto 前沿改成 S4 / S1 / **S14**。

## 8. 跑法

```bash
python3 utils/verify_ring2_20.py
python3 utils/dse_ring2_20node.py          # --quick 做冒烟
python3 utils/dse_ring2_rg_pareto.py       # --refine 加密当前前沿
python3 utils/dse_ring2_core10k.py         # 同 pattern 10000 flit/core；--quick 冒烟
python3 utils/gen_ring2_report.py
```

## 9. 同 pattern、10000 响应 flit / core

Workload 固定：`uniform` K=2500、R=4、seed=0，`plane_sel=least_occupied`。每个 core 收 **10000** 个响应 flit（10 core × 2500 txn × 4 flit）。十五方案吃同一批事务，同一条数据面（hop 2 拍、上环队列 8 深、下环 4+1、每核 outstanding 512）。S1 用温和 AIMD：α=0.15、β=0.85、epoch=64、rate_min=0.30。

| 方案 | makespan | 上环成功 | 上环失败 | 偏转 | 上环队列峰值 | 下环队列峰值 | 响应时延 p50 / p99 | outstanding 峰值 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| S0 RR | 14886 | 100000 | 189434 | 11348 | 8 | 1 | 2758 / 3817 | **512** |
| S1 AIMD | 18170 | 100000 | 118628 | 9381 | 8 | 1 | 23 / 205 | 54 |
| S2 iSLIP I=2 | **10044** | 100000 | 0 | 0 | 见下 | 0 | — | 509 |
| S3 push-on-pull | 14886 | 100000 | 189434 | 11348 | 8 | 1 | 2758 / 3817 | **512** |
| S4 kind-aware leave | 15075 | 100000 | 190870 | 11359 | 8 | 1 | 2790 / 4003 | **512** |
| S5 leave-slot lock | 13522 | 100000 | 92143 | **0** | 8 | 1 | 2388 / 3816 | **512** |
| S6 oldest dest clash | 13200 | 100000 | 93928 | **0** | 8 | 1 | 2515 / 3050 | **512** |
| S7 hop bounce | 12824 | 100000 | 71409 | **0** | 8 | 1 | 1967 / 3917 | **512** |
| S8 late plane | 11971 | 100000 | 67945 | **0** | 8 | 1 | 1559 / 4279 | **512** |
| S9 late dir | 11809 | 100000 | 62971 | **0** | 8 | 1 | 1638 / 4218 | **512** |
| S10 resp late dir | 11781 | 100000 | 61870 | **0** | 8 | 1 | 1568 / 4248 | **512** |
| S11 hop hold | 11451 | 100000 | **52288** | **0** | 8 | 1 | 2235 / **2512** | **512** |
| S12 hop islip | 11402 | 100000 | 732 | **0** | 8 | 1 | 2190 / 2572 | **512** |
| S13 hop short | 11288 | 100000 | 835 | **0** | 8 | 1 | 2181 / 2512 | **512** |
| S14 HA sib plane | **11043** | 100000 | 852 | **0** | 8 | 1 | **2159** / **2505** | **512** |

按目的 core 的响应上环（方向由 HA→core 最短路决定，九方案 CW/CCW 逐核相同；失败只计 slot 忙或 I-tag，**不含** AIMD 令牌拒绝、也不含 outstanding / leave-slot 等待）：

| core | 上环 (CW / CCW) | S0 失败 | S1 失败 | S2 失败 | S3 失败 | S4 失败 | S5 失败 | S6 失败 | S7 失败 | S8 失败 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 10000 (5008 / 4992) | 18575 | 11449 | 0 | 18575 | 18187 | 9320 | 9658 | 7952 | 6552 |
| 2 | 10000 (4888 / 5112) | 19286 | 11983 | 0 | 19286 | 20225 | 9425 | 9413 | 7438 | 7157 |
| 4 | 10000 (4920 / 5080) | 19114 | 12256 | 0 | 19114 | 18599 | 8836 | 9340 | 7029 | 6748 |
| 6 | 10000 (4924 / 5076) | 18488 | 12813 | 0 | 18488 | 18793 | 9428 | 9712 | 6842 | 6594 |
| 8 | 10000 (5124 / 4876) | 19894 | 12467 | 0 | 19894 | 19305 | 8806 | 9395 | 7124 | 7207 |
| 10 | 10000 (4828 / 5172) | 18589 | 12235 | 0 | 18589 | 19344 | 8877 | 9224 | 7143 | 6795 |
| 12 | 10000 (5056 / 4944) | 18678 | 11755 | 0 | 18678 | 18780 | 9563 | 9383 | 7378 | 6681 |
| 14 | 10000 (4912 / 5088) | 19640 | 10561 | 0 | 19640 | 19550 | 8968 | 9127 | 6544 | 6988 |
| 16 | 10000 (5032 / 4968) | 19087 | 11669 | 0 | 19087 | 19284 | 9514 | 9513 | 7107 | 6501 |
| 18 | 10000 (5044 / 4956) | 18083 | 11440 | 0 | 18083 | 18803 | 9406 | 9163 | 6852 | 6722 |
| **合计** | **100000 (49736 / 50264)** | **189434** | **118628** | **0** | **189434** | **190870** | **92143** | **93928** | **71409** | **67945** |

**S2 最快（10044 vs S0 14886，快 32%）。** 失败为 0 不是因为没有 I-tag / E-tag，而是 grant 只在 hop 已空时发出，反应式标签在本闭集中突发里用不上。分代预约把每核 in-flight 压在 512 附近（峰值 509，同拍完成/起飞交错）。

S3 的 100000 个响应都是 HA 按已到读请求调度后放出的（`n_pull_issued=100000`）。五方案 outstanding 对齐为每核 512 之后，S3 与 S0 的 10k 曲线重合：HA 一拍一条请求的 RR 不再卡吞吐，瓶颈回到共享数据面。响应上环仍不预约 hop。

S4 的 10k 均值曲线贴着 S0，但 makespan 更长（15075 vs 14886，1.01×），上环失败略多（190870 vs 189434）。leave 种类优先级在段带宽饱和时帮不上忙，还会打乱 RR 对两个方向的公平。

S5 把偏转打到 0，响应上环失败从 189434 收到 92143，makespan 14886→13522（1.51× bound）。p50 2758→2388。没有消灭 hop 忙，所以仍落后 S2 的 10044。

S6 同预约表、同拍 dest 冲突留最老 flit：makespan 13200（1.48× bound），p99 3816→3050。上环失败略升到 93928——赢的是尾核公平，不是 hop 空闲。

S7 第一跳忙就换 plane：makespan 12824（1.43× bound），响应上环失败 93928→71409，p50 2515→1967。p99 3050→3917。

S8 注入时现场选 plane：makespan **11971**（1.34× bound），上环失败 71409→67945，p50 1967→1559。p99 3917→4279。

S9 第一跳仍忙则改向：makespan **11809**（1.32× bound），上环失败 67945→62971。

S10 只对响应改向：makespan **11781**（1.32× bound），上环失败 62971→61870，p50 1568。

S11 同拍第一跳只留最老响应：makespan 11451（1.28× bound），p99 4248→2512。仍落后 S2 的 10044。

S12 dest-then-hop request-grant：makespan **11402**（1.28× bound），seed 全赢。上环失败从 52288 收到 732，多半是 hold 后不再尝试上环的重分类，不是 hop 争用消失。p50 2235→2190，p99 2512→2572。仍落后 S2 的 10044。

S13 hop grant 优先短路径：makespan **11288**（1.26× bound），seed 11399 / 11270 全赢。上环失败 732→835。p50 2181，p99 2512。仍落后 S2 的 10044。

S14 HA sibling plane yield：makespan **11043**（1.24× bound），seed 11224 / 11201 全赢。上环失败 835→852。p50 2159，p99 2505。仍落后 S2 的 10044。

S0 / S1 / S4 的上环队列都是 8 深且都跑满（峰值 8，S0 有 511885 次 admission stall，S4 有 516880），说明反压真的在起作用。**下环队列峰值只有 1**：4 深的 eject FIFO 在这个负载下根本用不上，S0 的 11348 次偏转来自「每 (node, plane) 每拍只有 1 个 leave 端口，两个方向同拍到达必有一个被挤掉」，不是队列满。E-tag 也因此几乎不触发。真正的限制是 leave 端口数，不是队列深度。

S2 的上环队列占用没法直接比：调度器给出的是刚性 t0，源端提前知道自己什么时候上环，所以只需在 t0 前把 flit 挪进队列，fabric 里的队列需求约为 1。但「已生成、尚未上环」的量峰值达 **267**（`max_src_wait`，512 分代之后从原先整批 25000 的 1249 降下来），这些 flit 停在 PE 侧 backlog 里——S2 把排队从 fabric 推到了源端。

S1 用温和 AIMD 之后，10k makespan 从教科书参数的 45748 收到 **18170**（S0 的 1.22×）。上环失败 118628，仍低于 S0 的 189434；响应 p50 / p99 为 23 / 205（S0 是 2758 / 3817）；outstanding 峰值 54。仍然是在拿一点吞吐换时延，但不再把速率打到地板。

接收带宽图：`results/ring2_core_recv_bw_10k.png`（十五面板、共用 x）和 `results/ring2_core_recv_bw_10k_overlay.png`（均值叠图；黑点线是解析下界对应的匀速接收，`bound=8939` 拍、1.12 flit/cycle/core）。S3 虚线叠在 S0 上；S4 橙色；S5 青绿；S6 品红；S7 紫色；S8 金色；S9 绯红；S10 翠绿；S11 锈色；S12 靛蓝；S13 青蓝；S14 玫红。
