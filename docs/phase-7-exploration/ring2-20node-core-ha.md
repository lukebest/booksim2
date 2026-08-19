# 2-full-ring 20 节点：三方案 makespan + request-grant Pareto

**几何：** 20 节点；偶数 index = AI core，奇数 = memory Home Agent；节点 19 与 0 相邻。
**Fabric：** 两个独立的并行 ring plane，每个 plane 自身双向。每节点每 plane 一个 inject/eject 端口，**plane 内双向共用同一 buffer**。有向段 `20 × 2 × 2 = 80`。相邻节点 **hop 时延 2 拍**。
**流量：** 读往返。core→HA 请求 1 flit，HA→core 响应 R flit。**makespan = 最后一个响应 flit 被 core PE drain 的拍。**
**Workload：** `allpairs`（10×10 每对 m 个事务，确定性）+ `uniform`（每 core 发 K 个事务，目的地在 10 个 HA 中均匀随机，多 seed）。
**共同数据面（三方案相同）：** 点对点 credit-based flow control + **8 深上环队列** + I-tag + E-tag。
**三方案只改注入/调度策略：** S0 在有 credit 时 RR 上环；S1 再加失败计数 piggyback + AIMD 源端速率；S2 同一数据面上做 request-grant（iSLIP 族）。
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

单事务下界：`hops_req·lat + m_req·σ + t_ha + hops_resp·lat + m_resp·σ`。资源下界把请求波与响应波当成先后两个 convoy（链路峰值 + 端口峰值 + 直径割）。

## 2. 共同数据面，然后才是 S0 / S1 / S2

三方案跑在**同一条**数据面上，不是三种 fabric。

| 层 | S0 RR | S1 AIMD | S2 request-grant |
|---|---|---|---|
| 相邻节点 hop 时延 | 2 拍 | 2 拍 | 2 拍 |
| 上环队列（每 node, plane） | 8 flit | 8 flit | 8 flit |
| 下环队列（每 node, plane） | 4 + 1 E-tag | 4 + 1 E-tag | 4 + 1 E-tag |
| inject / eject 端口 | 每 (node, plane) 1 个 | 同左 | 同左 |
| 点对点 credit FC | 有 | 有 | 有 |
| I-tag（上环饥饿有界） | 有 | 有 | 有 |
| E-tag（下环 / 预留 eject） | 有 | 有 | 有 |
| 有 credit 时 RR 上环 | 有 | 有 | — |
| AIMD 源端速率（失败 piggyback） | — | 有 | — |
| 上环前 request-grant 匹配 | — | — | 有 |

**Credit：** 每条有向 hop 是一对 credit。上游发 flit 先扣 credit，下游槽位空出后归还。没有 credit 不准发。80 条有向段（20 × 2 plane × 2 方向）。

**上环队列：** 每 (node, plane) 8 flit，plane 内双向共用。PE 把 flit 交给 fabric 外的 backlog，只有队列有空位才 admit，所以注入点是**真反压**，不是把整批流量一次吞下。

**I-tag：** 某源在某 (plane, dir) 上饿 `t_inj` 拍后升 I-tag，抑制该环向上其他节点上环，直到自己上去。

**E-tag：** 下环失败（共享 eject 队列满，或该拍唯一的 leave 端口已被占）`t_xfer` 次后升 E-tag，可以使用 `resv_ej` 条预留 eject 槽。失败则偏转，再绕一圈。改绑到预留 eject，不是 HiRD 的 transfer-FIFO E-tag。

**端口口径三方案一致：** 每 (node, plane) 每拍 1 次上环、1 次下环，即每节点每拍可上 2 个 flit（两个 plane 各 1）。S2 的 `("inj", node, plane)` / `("ej", node, plane)` 资源 key 与 S0 的 DES 对齐——早期版本按**节点**记 cap 1，等于把 S2 的端口天花板砍半，那是一个记账错误，已修。

S0（`rg_ring2_base.py`）是这条数据面上的反应式基线：周期精确 DES，优先级 **in-ring > inject**，有 credit 且 slot 空才 RR 上环。两方向 RR 争 leave 端口。失效模式是活锁 / 延迟长尾，不是死锁：每节点每 plane 每方向每拍最多到达 1 个 flit，偏转无条件可用。

## 3. S1 AIMD（`rg_ring2_aimd.py`）

上环、下环失败打在 flit 上。请求路径的计数随响应 piggyback 回 core。源端按 epoch 做 AIMD 令牌桶：

```
无失败: rate += alpha
有失败: rate *= beta
rate ∈ [rate_min, rate_max]
```

`aimd_scope=core_only` 只限 core 的请求注入；`both` 时 HA 用本地失败计数限响应注入（响应没有再回 HA 的报文）。

**预期（可证伪）：** 闭集中突发下，几乎每个源在第一个 epoch 都看到上环 NACK，速率被打下去，makespan 往往差于 S0。AIMD 的价值在开环/持续负载，不在「一次倒空」。

## 4. S2 request-grant（`rg_ring2_rg.py`）

已知 workload，两波调度：先请求，响应的 `release = request_eject + t_ha`。授权后传输刚性，但 **credit 计数、I-tag、E-tag、共享 eject 仍在**：grant 只保证上环时 hop 已有 credit，所以本闭集中突发里 I/E-tag 几乎不被触发（上环失败 = 0），不是把它们从微结构里拿掉。算法表驱动：

`islip(I) | pim(I) | rr_oldest | lqf | ocf | bvn | greedy_ff | wavefront | batched_bcfs`

旋钮：冲突域 `arc` / `whole_ring`，`interval` / `free_at`，VOQ 粒度 `per_dst` / `per_plane_dir` / `grouped`，仲裁器 `central` / `per_plane` / `distributed_token`。

**计时约定与 mesh 家族相同：** `makespan = makespan_des + t_sched_cycles`。只在数据面上快、组合深度造不出来的算法，会被自己的调度延迟罚下去。

## 5. 面积

三方案先付同一笔数据面：`credit_counters`（80 有向 hop）+ 每 plane 共享 eject + E-tag 预留 + 重组缓冲 + I/E-tag 状态。没有 transfer FIFO，没有 Swap bypass。

`distributed_cost("ring2_base")`：上述共同数据面。

`distributed_cost("ring2_aimd")`：共同数据面 + 速率/令牌寄存器 + 失败计数 + piggyback 字段。

`distributed_cost("ring2_rg")`：共同数据面 + 仲裁器 `sched_cost(*_ring2)` + 一小笔控制面（central 0.08 / per_plane 0.05 / token 0.03，归一化/节点）。**不是站点存储 0。**

## 6. 产物

| 文件 | 内容 |
|---|---|
| `utils/rg_ring2_topo.py` | 拓扑、角色、路径、下界、workload |
| `utils/rg_ring2_base.py` | S0 DES |
| `utils/rg_ring2_aimd.py` | S1 AIMD |
| `utils/rg_ring2_rg.py` | S2 调度 + 回放 |
| `utils/dse_ring2_20node.py` | 三方案 makespan |
| `utils/dse_ring2_rg_pareto.py` | 面积-性能 Pareto（`--refine` 给 loop 用） |
| `utils/verify_ring2_20.py` | 可执行断言 |
| `results/ring2_20node.json` | 三方案扫 |
| `results/ring2_rg_pareto.json` / `.png` | Pareto |
| `results/verify_ring2_20.json` | 门禁 |
| `results/report_ring2_20node.html` | 报告 |
| `results/ring2_core_recv_bw_allpairs.png` | 三方案每核接收带宽（allpairs） |
| `results/ring2_core_recv_bw_uniform.png` | 三方案每核接收带宽（uniform K=20） |
| `utils/dse_ring2_core10k.py` | 同 pattern、每核 10000 响应 flit 的 S0/S1/S2 对比 |
| `results/ring2_core10k.json` | 10k 每核接收曲线（分箱）+ 上环 / 队列统计 |
| `results/ring2_core_recv_bw_10k.png` | 三方案每核接收带宽（aligned x） |
| `results/ring2_core_recv_bw_10k_overlay.png` | 三方案均值叠图 |

## 7. 实测（allpairs m=1 R=4 / uniform 多 seed，plane_sel=least_occupied）

闭集中突发、验证 16/16 通过。数据面 makespan（S2 不含 `t_sched_cycles`）：

| 方案 | allpairs m=1 R=4 | uniform K=20 R=4 | uniform K=100 R=4 |
|---|---|---|---|
| S0 | 92 | 166 | 672 |
| S1 | 122（AIMD 配置均值；默认 92） | 259 | 1785 |
| S2 iSLIP I=2 | 85 | 181 | 671 |

S2 在数据面上能略赢 S0（allpairs 85 vs 92；`batched_bcfs` DES 82），但把 `t_sched_cycles` 计回之后全部掉到 105 以上。面积上 S2 还要在共同 credit + I/E-tag 数据面之外再付仲裁器，所以比 S0 更贵。**Pareto 前沿目前只有 S0。** 这和 8×6 维度切片环「集中化删掉站点存储、因而省面积」的故事相反：这里 S2 没有删掉 credit / I-tag / E-tag，只是多了一层匹配。

S1 在闭集中突发下经常更差：第一个 epoch 几乎人人上环 NACK，速率被打下去。这是可复现的结果，不是实现错误。

## 8. 跑法

```bash
python3 utils/verify_ring2_20.py
python3 utils/dse_ring2_20node.py          # --quick 做冒烟
python3 utils/dse_ring2_rg_pareto.py       # --refine 加密当前前沿
python3 utils/dse_ring2_core10k.py         # 同 pattern 10000 flit/core；--quick 冒烟
python3 utils/gen_ring2_report.py
```

## 9. 同 pattern、40000 响应 flit / core

Workload 固定：`uniform` K=10000、R=4、seed=0，`plane_sel=least_occupied`。每个 core 收 **40000** 个响应 flit（10 core × 10000 txn × 4 flit）。三方案吃同一批事务。

Makespan（最后一拍响应 drain）：

| 方案 | makespan | 上环成功 | 上环失败 | CW / CCW |
|---|---|---|---|---|
| S0 RR | **60800** | 400000 | 796781 | 200192 / 199808 |
| S1 AIMD | 170600 | 400000 | 94803 | 200192 / 199808 |
| S2 iSLIP I=2 | 65335 | 400000 | 0 | 200192 / 199808 |

按目的 core 的响应上环（方向由 HA→core 最短路决定，三方案 CW/CCW 逐核相同；失败只计 slot 忙或 I-tag，**不含** AIMD 令牌拒绝）：

| core | 上环 (CW / CCW) | S0 失败 (CW / CCW) | S1 失败 (CW / CCW) | S2 失败 |
|---|---|---|---|---|
| 0 | 40000 (19880 / 20120) | 79969 (39993 / 39976) | 9332 (4481 / 4851) | 0 |
| 2 | 40000 (20044 / 19956) | 78459 (39553 / 38906) | 8896 (4402 / 4494) | 0 |
| 4 | 40000 (19580 / 20420) | 82368 (39791 / 42577) | 10337 (4963 / 5374) | 0 |
| 6 | 40000 (20160 / 19840) | 78100 (39439 / 38661) | 9660 (4793 / 4867) | 0 |
| 8 | 40000 (20164 / 19836) | 80213 (40015 / 40198) | 9922 (5057 / 4865) | 0 |
| 10 | 40000 (20156 / 19844) | 80149 (40665 / 39484) | 10183 (5121 / 5062) | 0 |
| 12 | 40000 (20104 / 19896) | 82014 (41408 / 40606) | 9193 (4629 / 4564) | 0 |
| 14 | 40000 (20348 / 19652) | 77358 (39481 / 37877) | 9016 (4692 / 4324) | 0 |
| 16 | 40000 (19680 / 20320) | 78993 (39283 / 39710) | 9156 (4713 / 4443) | 0 |
| 18 | 40000 (20076 / 19924) | 79158 (39140 / 40018) | 9108 (4687 / 4421) | 0 |
| **合计** | **400000 (200192 / 199808)** | **796781 (398768 / 398013)** | **94803 (47538 / 47265)** | **0** |

这一档上 S0 仍是数据面最快。S2 刚性预约略慢（65335 vs 60800）；失败为 0 不是因为没有 I-tag / E-tag，而是 grant 只在 hop 已有 credit 时发出，反应式标签在本闭集中突发里用不上。S1 失败少一个数量级（令牌桶把注入拉开），但 makespan 是 S0 的 2.8×——闭集中突发下 AIMD 把速率打下去，和 §3 的预期一致。接收带宽图：`results/ring2_core_recv_bw_40k.png`（三面板、共用 x）和 `results/ring2_core_recv_bw_40k_overlay.png`（均值叠图）。
