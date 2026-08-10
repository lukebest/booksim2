# 8×6 Request–Grant 分组交换 NoC 基线研究报告

**几何：** 8×6 mesh 与折叠 2D torus；H=7，V=9；RAMP=2，RAMP_BW=2  
**金属线恒定：** torus 每链路带宽 = mesh 的一半（σ=2），对分带宽均为 6 flit/cy  
**类型：** bufferable（源端准入 + FIFO）/ bufferless（时隙预约、零缓冲）  
**仲裁：** CA 集中式 @ nid=28 / DA 目的端分布式  
**流量：** alltoall · allgather · allreduce · broadcast · reduce  
**产物：** `results/rg_noc_8x6.json`，`results/report_rg_noc_8x6.html`

## 0. 一页结论

1. **对 alltoall，逐流 request–grant 的控制平面收敛税远大于数据面本身。**  
   CA 下 2256 条 request 经 ≤4 入端口：解析下界 564 cy，控制面 DES 实测 **t_last_request = 1136 cy**；而 m=1 FIFO 基线 makespan 仅 **192 cy**（pg golden 188）。  
   **不聚合的 CA request–grant 作为 alltoall 基线是不可用的。**

2. **两个有效缓解：**  
   - **Request 聚合**（每源 1 条）：48 条 request，t_last_req ≈ 55（线延迟），bufferable CA m=1 makespan **326**（与数据面同量级）。  
   - **DA 分布式**：收敛打散到 48 个目的，m=1 makespan **518**（仍高于聚合 CA，但无 1136 cy 单点税）。

3. **同步 barrier（allgather/allreduce）** 把 R_rg 钉在最远节点往返（~111 cy + T_sched），每次集合通信付一次、随 m 摊薄；异步「每 grant = 一棵多播树」去掉 barrier 等待，但树间冲突使数据面更长——大 m 时同步往往更划算。

4. **Mesh vs torus（对分相等）：**  
   - 大消息 alltoall：二者由 bisect 绑定，下界相同。  
   - 小消息 / broadcast：torus 直径 55 vs mesh 94，DA bufferless broadcast m=1 为 **58 vs 97**。  
   - torus σ=2 在长消息上反噬；bufferable torus 还要 **2 VC dateline**（面积约翻倍）。

5. **bufferless** 用时隙预约构造性消除网内排队（零驻留断言通过）；bufferable 在单播上与之接近，多树 pattern 的快速路径会高估共享前缀负载。

## 1. 建模口径与审计

| 项 | mesh | folded torus |
|----|------|----------------|
| 无向链路 | 82 | 96 |
| σ（cy/flit） | 1 | 2 |
| 对分带宽 | 6 flit/cy | 6 flit/cy |
| 直径线延迟 | 94 | 55 |
| bufferable VC | 1 | 2（dateline） |

**金属线比** torus/mesh = 96/82 ≈ **1.171**（非严格恒定；见报告局限）。  
**delay×2 敏感度：** 折叠线长按 14/18 跑对照，写入 JSON `tag=sens_torus_delay`。

自检：`bisect_lb` 两拓扑逐位相等；torus CDG（dateline）无环；routing validate ✓。

## 2. Request–Grant 机制

```
源端数据 ──request──▶ 仲裁器 ──grant──▶ 源端闸门 ──▶ 数据平面
                         │
              控制平面（独立窄网，1 msg/cy/链路）
```

| Pattern | Request 语义 |
|---------|----------------|
| alltoall / broadcast / reduce | 异步单流（broadcast/reduce 为树） |
| allgather / allreduce | 默认同步 barrier：等齐 48 个 request 再统一 grant |
| allgather 对照 | 异步：每个 grant = 该源的一棵多播树 |

reduce = **gather + PE 本地归约**（ADR-002 / Arch-A2，网内无 ALU）。

## 3. 关键数字（mesh，除非注明）

| 配置 | m | makespan | 备注 |
|------|---|----------|------|
| FIFO alltoall | 1 | **192** | golden 对照 188 |
| CA bufferable alltoall 非聚合 | 1 | **1360** | t_last_req=1136 |
| CA bufferable alltoall 聚合 | 1 / 4 / 16 | 326 / 633 / 2505 | 可用基线 |
| DA bufferable alltoall | 1 / 4 / 16 | 518 / 608 / 2227 | 无单点收敛 |
| CA bufferless broadcast | 1 | 207 | R_rg≈111 占主导 |
| DA bufferless broadcast | 1 | **97** | 近数据下界 98 |
| torus DA bufferless broadcast | 1 | **58** | 直径优势 |
| CA allgather sync bufferless | 1 / 4 / 16 | 230 / 496 / 1667 | |
| CA allgather async bufferless | 1 / 4 / 16 | 208 / 469 / 1357 | 略快于 sync |

**W_out 敏感度**（聚合 alltoall m=4）：W=1 → 2702；W=4 → 806；W=16 → 633；W=∞ → 617。

## 4. 面积（归一化 IQ-XY = 1.0）

`area = 0.380 + 0.170 + 5·VC·Q·0.00365 + arbiter + ctrl_net`

- bufferable mesh（VC1,Q=19）≈ 0.97（含 CA 0.05 + ctrl 0.02）  
- bufferable torus（VC2）缓冲项翻倍  
- bufferless：缓冲 ≈ 0，总面积显著低于 1.0，但付仲裁表 + 控制网开销  

## 5. 选型建议

| 场景 | 建议 |
|------|------|
| alltoall 基线 | **CA + request 聚合 + bufferable**；或 DA |
| 切勿 | CA 逐流 request（2256 条） |
| broadcast / 小消息 | DA + bufferless；拓扑偏好 torus（注意 σ=2 与 VC 面积） |
| allgather | 大 m 用 sync barrier；小 m 可试异步树 |
| 面积优先 | bufferless（集合流）+ 小控制面 |

## 6. 验证清单

| # | 项 | 结果 |
|---|----|------|
| 1 | 对分带宽相等 | ✓ |
| 2 | torus σ=0.5 | ✓ |
| 3 | FIFO alltoall m=1 ≈ 188 | 192（+4） |
| 4 | bufferless 零驻留 | ✓（68 组） |
| 5 | 单播 bufferable ≲ bufferless | 见 JSON `bufferable_le_bufferless_unicast` |
| 6 | 保序 | ✓ |
| 7 | torus CDG 无环 | ✓ |
| 8 | 控制收敛 ≥ 564 量级 | t_last_req=1136 ✓ |

## 7. 已知局限

- 金属线按链路计数，torus 多 ~17%；折叠物理线长×2 用 delay_scale 对照。  
- 同 hop 延迟 7/9 系统性偏向 torus。  
- 多树 bufferable 用事件驱动单播展开，共享前缀被重复计数（故树 pattern 上 bufferable 可能高于 bufferless）。  
- 控制面与数据面拓扑同构、带宽独立，未建模控制/数据共用金属。

## 8. 文件

| 文件 | 作用 |
|------|------|
| `utils/rg_topo.py` | mesh/torus 拓扑、DOR、dateline CDG、金属/对分审计 |
| `utils/rg_bounds.py` | 五族下界 + R_rg + 控制收敛下界 |
| `utils/rg_collectives.py` | 五 pattern 流/树 |
| `utils/rg_arbiter.py` | CA/DA、同步/异步、预约/准入、控制面 DES |
| `utils/dse_rg_noc_8x6.py` | 数据面 DES + 扫描 |
| `utils/gen_rg_noc_report.py` | HTML 报告 |
| `results/rg_noc_8x6.json` | 全量数据 |
| `results/report_rg_noc_8x6.html` | 可读报告 |
