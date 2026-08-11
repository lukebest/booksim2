# 8×6 Request–Grant 分组交换 NoC 基线研究报告

**几何：** 8×6 mesh 与折叠 2D torus；H=7，V=9；RAMP=2，RAMP_BW=2  
**金属线恒定（数据面）：** torus 每链路带宽 = mesh 的一半（σ=2），对分带宽均为 6 flit/cy  
**控制平面（硬约束）：** request/grant 走**私有控制 NoC**（与数据面同构、独立物理链路），**不与数据面共链路**；不继承数据面 σ  
**类型：** bufferable（源端准入 + FIFO）/ bufferless（时隙预约、零缓冲）  
**仲裁：** CA 集中式 @ nid=28 / DA 目的端分布式  
**流量：** alltoall · allgather · allreduce · broadcast · reduce  
**产物：** `results/rg_noc_8x6.json`，`results/report_rg_noc_8x6.html`

## 0. 一页结论

1. **即便控制面是完全私有的 NoC，CA 逐流 alltoall 仍被控制收敛税打穿。**  
   2256 条 request 挤入 CA 控制路由器 ≤4 入端口：解析下界 564 cy；私有控制 NoC DES 实测 **t_last_request ≈ 1136 cy**（超出部分是**控制消息互争**，不是数据干扰）。m=1 FIFO 数据基线仅 **192 cy**。  
   **不聚合的 CA request–grant 作为 alltoall 基线不可用——问题在控制面拓扑汇聚，不在「是否与数据共链路」。**

2. **两个有效缓解（仍在私有控制 NoC 上）：**  
   - **Request 聚合**（每源 1 条）：48 条 request，t_last_req ≈ 55，makespan 回到数据面量级。  
   - **DA 分布式**：收敛打散到 48 个目的控制端点，消除单点 564 cy 税。

3. **同步 barrier（allgather/allreduce）** 把 R_rg 钉在最远节点经**控制面**的往返；异步「每 grant = 一棵多播树」去掉 barrier 等待，大 m 时同步往往更划算。

4. **Mesh vs torus（数据面对分相等）：** 小消息/broadcast 上 torus 直径优势明显；长消息受数据面 σ=2 反噬。控制面带宽与数据 σ 解耦（始终 1 msg/cy/控制链路）。

5. **面积：** 私有控制 NoC 是数据面金属恒定预算之外的增量（每节点 +0.12，相对 IQ-XY=1.0）。

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
| 承载 | 数据 flit | request / grant 仅 |
| 金属预算 | mesh↔torus 对分恒定 | **额外**金属/面积 |

隔离断言写入 JSON：`control_noc_policy.shared_with_data_plane = false`，且每条 RG 行的 `ctrl.shared_with_data_plane = false`。

## 2. Request–Grant 语义

| Pattern | Request 语义 |
|---------|----------------|
| alltoall / broadcast / reduce | 异步单流（broadcast/reduce 为树） |
| allgather / allreduce | 默认同步 barrier：等齐 48 个 request 再统一 grant |
| allgather 对照 | 异步：每个 grant = 该源的一棵多播树 |

reduce = **gather + PE 本地归约**（ADR-002 / Arch-A2）。

端到端：`T = T_bound + R_rg + W_grant`，其中 `R_rg` 的线延迟项走 **ℓ_ctrl**（私有控制 NoC）。

## 3. 关键数字（mesh，除非注明）

数值来自重跑后的 `results/rg_noc_8x6.json`（`shared_with_data_plane=false` 全量断言通过）。控制 DES 本就不占用数据链路，makespan 与隔离前一致；面积按私有控制 NoC **+0.12/节点** 重计。

| 配置 | m | makespan | 备注 |
|------|---|----------|------|
| FIFO alltoall | 1 | **192** | 无控制 NoC；golden 对照 188 |
| CA bufferable alltoall 非聚合 | 1 | **1360** | t_last_req=**1136**（私有控制网互争） |
| CA bufferable alltoall 聚合 | 1 / 4 / 16 | 326 / 633 / 2505 | 可用基线 |
| DA bufferable alltoall | 1 / 4 / 16 | 518 / 608 / 2227 | 无单点收敛 |
| DA bufferless broadcast | 1 | **97** | 近数据下界 |
| torus DA bufferless broadcast | 1 | **58** | 直径优势 |

## 4. 面积（归一化 IQ-XY = 1.0）

`area = 0.380 + 0.170 + 5·VC·Q·0.00365 + arbiter + private_ctrl_noc(0.12)`

- RG 配置均含私有控制 NoC +0.12/节点（数据面金属恒定之外）  
- bufferable torus 另加 VC2 dateline 缓冲  
- FIFO 基线无控制 NoC、无仲裁器开销  

## 5. 选型建议

| 场景 | 建议 |
|------|------|
| alltoall 基线 | **CA + request 聚合 + bufferable**；或 DA |
| 切勿 | CA 逐流 request（2256 条）——私有控制网也救不了入端口汇聚 |
| broadcast / 小消息 | DA + bufferless；拓扑偏好 torus（注意数据面 σ=2 与 VC） |
| 面积 | 须为私有控制 NoC 单独买单（+0.12/节点） |

## 6. 验证清单

| # | 项 | 结果 |
|---|----|------|
| 1 | 对分带宽相等（数据面） | ✓ |
| 2 | torus 数据 σ=0.5 | ✓ |
| 3 | FIFO alltoall m=1 ≈ 188 | ~192 |
| 4 | bufferless 零驻留 | ✓ |
| 5 | 保序 | ✓ |
| 6 | torus CDG 无环 | ✓ |
| 7 | 控制收敛 ≥ 564 量级 | ✓ |
| 8 | **私有控制 NoC：shared_with_data_plane=false** | ✓ |

## 7. 已知局限

- 数据面金属线 torus/mesh≈1.17；折叠线长×2 用 delay_scale 对照。  
- 私有控制 NoC 是**额外**金属，不计入数据面对分恒定。  
- 多树 bufferable 快速路径会展开为单播、高估共享前缀。  
- 控制面 hop 延迟取与数据面相同的 H/V（线延迟主导）；未再为窄线单独标定。

## 8. 文件

| 文件 | 作用 |
|------|------|
| `utils/rg_topo.py` | mesh/torus 数据拓扑 |
| `utils/rg_bounds.py` | 下界（含控制入端口收敛） |
| `utils/rg_collectives.py` | 五 pattern |
| `utils/rg_arbiter.py` | **私有控制 NoC DES** + CA/DA 调度 |
| `utils/dse_rg_noc_8x6.py` | 数据面 DES + 扫描 |
| `utils/gen_rg_noc_report.py` | HTML |
| `results/rg_noc_8x6.json` / `report_rg_noc_8x6.html` | 数据与报告 |
