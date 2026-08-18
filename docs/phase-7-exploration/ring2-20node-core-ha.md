# 2-full-ring 20 节点：三方案 makespan + request-grant Pareto

**几何：** 20 节点；偶数 index = AI core，奇数 = memory Home Agent；节点 19 与 0 相邻。
**Fabric：** 两个独立的并行 ring plane，每个 plane 自身双向。每节点每 plane 一个 inject/eject 端口，**plane 内双向共用同一 buffer**。有向段 `20 × 2 × 2 = 80`。
**流量：** 读往返。core→HA 请求 1 flit，HA→core 响应 R flit。**makespan = 最后一个响应 flit 被 core PE drain 的拍。**
**Workload：** `allpairs`（10×10 每对 m 个事务，确定性）+ `uniform`（每 core 发 K 个事务，目的地在 10 个 HA 中均匀随机，多 seed）。
**三方案：** S0 RR + I-tag/E-tag；S1 失败计数 piggyback + AIMD 源端速率；S2 request-grant（iSLIP 族移植）。
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

## 2. S0 基线（`rg_ring2_base.py`）

周期精确 DES。优先级 **in-ring > inject**。上环只允许进入整个 flit 时长都空的 slot（in-ring 流量已经在线上，lookahead < hop delay，所以「in-ring 永不被堵」是不变量而不是愿望）。

- **I-tag：** 某源在某 (plane, dir) 上饿 `t_inj` 拍后升 I-tag，抑制该环向上其他节点上环，直到自己上去。
- **E-tag：** 下环失败（共享 eject 队列满）`t_xfer` 次后升 E-tag，可以使用 `resv_ej` 条预留 eject 槽。失败则偏转，再绕一圈。
- 每 (node, plane) 每拍最多 1 次上环、1 次下环；两方向 RR 争 leave 端口。

失效模式是活锁 / 延迟长尾，不是死锁：每节点每 plane 每方向每拍最多到达 1 个 flit，偏转无条件可用。

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

已知 workload，两波调度：先请求，响应的 `release = request_eject + t_ha`。授权后传输刚性，站点零存储。算法表驱动：

`islip(I) | pim(I) | rr_oldest | lqf | ocf | bvn | greedy_ff | wavefront | batched_bcfs`

旋钮：冲突域 `arc` / `whole_ring`，`interval` / `free_at`，VOQ 粒度 `per_dst` / `per_plane_dir` / `grouped`，仲裁器 `central` / `per_plane` / `distributed_token`。

**计时约定与 mesh 家族相同：** `makespan = makespan_des + t_sched_cycles`。只在数据面上快、组合深度造不出来的算法，会被自己的调度延迟罚下去。

## 5. 面积

`distributed_cost("ring2_base")`：每 plane 共享 eject 队列 + E-tag 预留 + 重组缓冲 + I/E-tag 计数。没有 transfer FIFO，没有 Swap bypass。

`distributed_cost("ring2_aimd")`：S0 + 速率/令牌寄存器 + 失败计数 + piggyback 字段。

`distributed_cost("ring2_rg")`：站点存储 0。仲裁器按 `sched_cost(*_ring2)` 计价，外加一小笔控制面（central 0.08 / per_plane 0.05 / token 0.03，归一化/节点）。

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

## 7. 实测（allpairs m=1 R=4 / uniform 多 seed，plane_sel=least_occupied）

闭集中突发、验证 14/14 通过。数据面 makespan（S2 不含 `t_sched_cycles`）：

| 方案 | allpairs m=1 R=4 | uniform K=20 R=4 | uniform K=100 R=4 |
|---|---|---|---|
| S0 | 92 | 166 | 672 |
| S1 | 122（AIMD 配置均值；默认 92） | 259 | 1785 |
| S2 iSLIP I=2 | 85 | 181 | 671 |

S2 在数据面上能略赢 S0（allpairs 85 vs 92；`batched_bcfs` DES 82），但把 `t_sched_cycles` 计回之后全部掉到 105 以上，面积还比 S0 的 0.0207 贵 2–9×。**Pareto 前沿目前只有 S0。** 原因是这个拓扑没有 transfer FIFO，分布式基线已经很便宜——和 8×6 维度切片环「集中化省面积」的故事相反，必须分开说。

S1 在闭集中突发下经常更差：第一个 epoch 几乎人人上环 NACK，速率被打下去。这是可复现的结果，不是实现错误。

## 8. 跑法

```bash
python3 utils/verify_ring2_20.py
python3 utils/dse_ring2_20node.py          # --quick 做冒烟
python3 utils/dse_ring2_rg_pareto.py       # --refine 加密当前前沿
python3 utils/gen_ring2_report.py
```
