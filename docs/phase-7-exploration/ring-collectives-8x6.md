# 8×6 无缓冲环上的集合通信：paper 机制基线 + 静态拍图方案

**几何：** 8×6，48 节点；6 行环×8 + 8 列环×6 = **192 条有向弧**（96 无向，与折叠 torus 链路集逐条相同，金属 96/82 = 1.17× mesh）；H=7，V=9；t_turn=1；RAMP_BW=2
**判据：** 环用 **D-R 五子句**（R1 弧互斥 / R2 上环互斥 / R3 下环互斥 / R4 转环原子 / R5 VOQ 保序+静态定路），与 `islip2d-mesh-ring-8x6.md` 同一套
**基线（Part 1）：** `ring_base` = HPCA'22 机制（E-tag / I-tag + 偏转 + 桥 transfer FIFO + 目的端重组），**只支持 unicast**；`ring_islip2d` 作同能力对照
**静态拍图（Part 2）：** 环站支持 **copy-and-continue 弧多播**，归约在**节点 L1 buffer** 内做；环内仍严格零缓冲、零转环驻留
**能力分层：** T0 = 纯 unicast（paper 机制可达）；T1 = 弧多播 + L1 归约
**数据：** `results/ring_collectives_8x6.json`、`results/ring_tavg_8x6.json`、`results/ring_robust_8x6.json`、`results/calendars/ring_*.json`；mesh 参照 `results/multiflit_area_makespan.json`（本轮补齐 R=1/13）
**验证：** `results/verify_ring_collectives_8x6.json`，**56/56 通过**，全部可执行、失败即命名具体量；其中 **3 项记录为「预测被推翻」**而不是悄悄放宽
**报告：** `results/report_ring_collectives_8x6.html`；mesh 侧的 R=1/5/13 深度扫描补在 `results/report_multi_area_makespan.html` §5.5

## 0. 一页结论

1. **弧多播是这套硬件里唯一真正改变数量级的增量，但只对 fan-out 生效。** m=13 的 broadcast：flat unicast **323 拍 / 上环 611 flit** → 弧多播 **95 拍 / 上环 182 flit**（**3.4× makespan、3.36× 流量**）。
   但 `reduce` / `gather` / `alltoall` **没有 T1 行，这不是漏跑**：copy-and-continue 是 fan-out 原语，fan-in 无可复制，而 alltoall 的 N(N−1) 条消息两两不同，任何一站都不可能用一份副本服务两条。验证套件因此断言「多播不适用时 T1 与 T0 逐字段相同」（17 个 (pattern, algo) 对）。**买多播硬件对这三个 pattern 的收益精确为 0。**

2. **T0 下必须承认两条恒等式，否则整张表在自我恭维。** `allgather` 与 `alltoall` 是**同一个 unicast 流集**（2256 条送达，实测 makespan 逐项相同：m=1 均为 86 拍、m=13 均为 1044 拍）；`gather` 与 `reduce` 是**同一个网络需求**，差别只在 root 的 L1 是否累加。
   真正的差异必须落在 **T1 能力**与**归约点的 L1/ramp 瓶颈**上，不能落在名字上。

3. **L1 累加链的价值是「尺寸守恒」，不是「少发一次」。** 同一棵维度树上，m=13：gather 上环 **1066 flit / 657 拍**，reduce 上环 **611 flit / 189 拍**（**1.74× 流量、3.48× makespan**）。
   原因是折叠让每一跳的载荷都等于 payload 本身而不是累积的 bundle——这是 L1 归约相对「先收齐再算」的结构性优势。

4. **满环旋转确实打到弧负载下界，但「链路利用率 = 1.0」的预测被推翻。** II_eff = **47.0 拍 = 每轮最忙弧的负载**，逐 R 恒定（R=2/5/13/26/47 全部 47.0），这条预测成立。
   但关键弧利用率只有 R=1 时 8.3% → R=13 时 54.2% → R=47 时 **81.0%**，单调上升却只是**渐近**趋近 1。闭式为 `util = II·R / (T1 + II·(R−1))`，实测与闭式在 ±0.02 内吻合；填充代价 **T1 = 564 拍**永远摊不掉。**「旋转是吞吐最优」对，「旋转把链路跑满」错。**

5. **双向半弧买到的是跨度，不是带宽——而且两种 pattern 的机理还不同。** 多播方案（T1）上流量比恒为 **1.00×**、makespan 比 **1.07~1.64×**：一条 copy-and-continue 弧无论朝哪边走，都在同样的站点丢下同样的副本，所以只有 span 变短。
   unicast 方案（T0）上确实也省弧周期（流量比 **1.68~1.74×**、makespan 比最高 **2.45×**），但原因是单向绕行路径更长，不是负载被劈开。**「双向让峰值弧负载减半」这句话对两种情况都不成立。**

6. **paper 机制在漏斗型集合通信上打赢静态拍图，这与预期相反且原因是建模口径。** m=13 的 `reduce/dim_2phase`：`ring_base` **107 拍** vs 静态拍图 **189 拍**（拍图慢 1.77×）。
   机理不是偏转有魔法（该配置偏转率为 0），而是**下环端口的记账粒度**：拍图把一个抽取点整段（m·σ 拍）独占给一次传输，而 `ring_base` 按 L1 的 `RAMP_BW` 逐 flit 交错抽取。把拍图模型放宽到 2 个上/下环端口后 reduce 变 **124 拍**（1.52×）、gather **657 → 410 拍**（1.60×）、allgather/T1 **815 → 463 拍**（1.76×）——这才是同口径比较。**这一列必须公开，否则「静态拍图全面更快」是错的。**

7. **反过来，在扇出与全交换型集合通信上静态拍图大幅领先。** m=1 的 `alltoall`：拍图 **86 拍** vs `ring_base` **163 拍**（1.90×）vs `ring_islip2d` **1968 拍**；m=13 `alltoall` 拍图 **1044** vs base **1912**（1.83×）。
   m=13 `broadcast` 拍图 **95** vs base **202**（2.13×）。**`ring_islip2d` 只是同能力调度对照（每节点每轮一条 grant，63 轮），不是竞争者**，读作 control。

8. **小 m 下大多数集合通信是时延受限而不是带宽受限，所以下界必须给名字。** m=1 时六个 pattern 的最优拍图**全部**由 latency floor 绑定（59~112 拍）；m=13 才切换：allgather/T1 绑 **port**、alltoall 绑 **arc_load**、broadcast/allreduce 仍绑 **latency**。
   只报「makespan / 下界」而不报**是哪个下界**会把「已到界」和「还有空间」混为一谈。实测 makespan/LB：gather **1.06×**、broadcast **1.12×**（基本到界）；reduce **2.08×**、allreduce **1.67×**（还有空间，且空间的名字就是第 6 条的端口粒度）。

9. **利用率必须两个一起报。** m=13 全局利用率最高的是 flat allgather / alltoall（**52.3%**，192/192 条弧全部用到，关键弧 **61.0%**）；维度树方案全局只有 33%，但关键弧到 **72.2%**。
   **全局高而关键弧低 = 还能压；关键弧贴自己的界 = 只能换流集。** 所有方案的「关键弧周期 / 关键弧下界」都是 **1.00×**，即打包器在最忙那条弧上没有浪费。

10. **一个死节点会逼出一个额外相位，而不只是重新排一次——这条把计划的假设推翻了。** 维度切片算法靠**行列唯一交点**把某行的数据交给某列；交点死了，整列都拿不到那行的数据，**fabric 仍然连通，但拍图只有一条路**。
    实测 `allgather/dim_2phase/T1` 的 26 次重编译里 **12 次**必须追加修复相位，`allreduce/dim_2phase/T1` 最多需要 **3 个**修复相位。把这些报成「重编译，膨胀 1.2×」会掩盖一个缺失的相位——所以修复相位是显式构造并逐条通过 D-R 复核与交付集合复核的。

11. **「一个死点等于两处断环、必须有环站 bypass mux」也被推翻了一半。** 在 2-连通环上，**连续**的死点洞（1×1 / 2×2 / 象限）可以绕远路走完，去掉 bypass mux 后不可行场景数**增加 0**。
    真正需要 mux 的是**同一个环上分散的死点**：它把环切成互不相通的段。13 个含死点的场景里，去掉 mux 后不可行数从 **1/13 升到 4/13**（`allgather/dim_2phase/T1`，多出 3 个），`broadcast` / `allreduce` / `reduce` 的维度树各多出 **2 个**；而 **flat 方案一个都不多**（扁平流集本来就逐条重路由）。**硬件要求成立，但理由和场景与原假设不同。**

12. **旋转拿吞吐换掉了容错，交换率极高。** 27 个故障场景下 `allgather/ring_rotate` **20 个无合法拍图**（免重编译 7、重编译 0）；同一 pattern 的 `dim_2phase/T1` 只有 **1 个**不可行、26 个可重编译。
    旋转的每一步「所有节点同时上环、每条弧恰好用一次」没有留任何备用弧，这正是它打到下界的原因，也正是它一断就死的原因。**这两件事是同一个性质。**

13. **膨胀比必须做功归一化，否则读反。** 死节点同时删掉容量**和工作量**，所以原始 makespan 比会低于 1.0——那是阵列变小，不是丢节点让集合通信变快。
    做功归一化后最坏膨胀：`allgather/dim_2phase/T1` **1.56×**、`broadcast/dim_2phase/T1` **1.35×**、`reduce/dim_2phase/T0` **1.24×**；flat 方案普遍 **1.00~1.14×**（流集扁平，重排更自由）。

14. **J\* 在刚性拍图上是个没有信息量的指标，真正的量是「实际被吸收的拍数」。** 硬 barrier 下 makespan 精确增加「最迟释放量」（3 个抖动模型 × 3 个 J 全部逐拍相等），所以 J\*（膨胀 ≤5%）只是 makespan 的 5% 换个说法。
    引入第三种策略 **repack**（把源端迟到时刻当作 t_min 重新编译）才测得真实松弛：J=256 burst 下 `allgather/flat` 吸收 **170 拍**、`allgather/dim_2phase/T1` **70 拍**、而 `broadcast/dim_2phase/T1` 与 `ring_rotate` 吸收 **0 拍**（前者只有 14 次传输、松弛 p50=6；后者松弛 p50=4，拍图本身就是紧的）。**松弛多的方案抗抖动，紧到界的方案不抗抖动——这与第 12 条是同一个 trade-off 的另一面。**

15. **「环比 mesh 快」这句话的真假取决于环站端口数与流水深度，不带口径就是错的。** 同一 T_avg 定义下 allgather：**1 端口**的环在 R=1 赢（0.885×）、R=5 打平（1.006×）、**R=13 输 1.19×**；**2 端口**的环三档全胜（0.708× / 0.676× / 0.754×）。
    同时 **mesh 自己的最优方案在 R=13 换人**：axis+CCW（T1=96、II_eff=42.25）→ Hamilton bi-tree（T1=210 但 **II_eff=1.0**）。深流水下 II_eff 支配一切。**只报一个 R、或不报端口数，都会随机挑出一个赢家。**

16. **拍图规模差距比 makespan 差距更大，这是控制存储的实际代价。** 同一个 allgather，`dim_2phase/T1` 导出 **768** 条环站记录，`flat/T0` 导出 **10320** 条（**13.4×**）。
    导出格式升到 `calendar-export/v2`：环站端口集按环分开（`row_board / row_leave / col_board / col_leave` + 四个环向），多播用现有 `out_port_mask` 天然表达，L1 累加用 `opcode=ADD`。**新增 `in_slot` 字段是必须的**——转环时输入端口比输出端口早 `t_turn` 拍被占用，用单个 `slot` 同时表达两者会造出不存在的端口冲突（node 43 slot 21 就是这样一个假警报）。

## 1. 口径：什么叫一次「被授权的传输」，多播怎么记账

沿用 `rg-noc-8x6.md` 的左闭右开区间语义。unicast 印记 `RingFootprint` 由 `(src, dst, ring, direction, t0, m, σ)` 完全决定。

多播新增 `RingMcastFootprint`：**一次 board、弧上多个 leave、可选 `op=ADD`**。记账规则是这套分析可信度的关键：

- **R2（上环互斥）**：一次多播只占**一个**上环点，这正是它省下来的东西。
- **R3（下环互斥）**：弧上**每个成员各占一个抽取点**——多播不能省下环带宽，只能省上环带宽和弧周期。
- **R4（转环原子）**：**每个转环副本各查一次**，不是整条弧查一次。
- **R1（弧互斥）**：覆盖弧的每条段各占一次，与 unicast 同。

`mcast_cover(ring, src, members, bidir)` 把成员集切成 ≤2 条弧（顺/逆时针各一），返回的覆盖是**成员集的严格划分**（验证第 7 项）。双向覆盖的两条弧会**在同一节点争同一个 board 端口**，这不是 bug 而是真实约束（每节点每环一个上环点），由 `port_lb` 如实记入下界，打包器靠错拍解决。

## 2. Part 1：paper 机制的六个集合通信

三条腿跑同一个流集、同一 m、同一 σ、同一 barrier 语义。完整表在 `report_ring_collectives_8x6.html`，此处摘每个 pattern 的最优行。

两列都取**各自腿的最优算法**（集合算法与 transport 是正交轴，拿同一个算法压基线会把基线做成稻草人），所以同一行的两个数字可能来自不同算法；逐算法的完整表见 HTML 报告。

**m = 1**（全部由 latency floor 绑定）

| collective | 最优拍图 | 拍图 | `ring_base` 最优 | 下界 | base/cal |
|---|---|---|---|---|---|
| allgather | dim_2phase / T1 | **85** | 126 | 68 | 1.48× |
| alltoall | flat / T0 | **86** | 163 | 59 | 1.90× |
| allreduce | halving_doubling / T0 | 112 | **100** | 112 | 0.89× |
| gather | flat / T0 | 59 | **56** | 59 | 0.95× |
| reduce | flat / T0 | 59 | **56** | 59 | 0.95× |
| broadcast | flat / T0 | 59 | 63 | 59 | 1.07× |

**m = 13**

| collective | 最优拍图 | 拍图 | `ring_base` 最优 | 下界 | 绑定下界 | cal/LB |
|---|---|---|---|---|---|---|
| broadcast | dim_2phase / T1 | **95** | 202 | 85 | latency | 1.12× |
| allreduce | dim_2phase / T1 | **284** | 218 | 170 | latency | 1.67× |
| gather | flat / T0 | **331** | 335 | 312 | port | 1.06× |
| reduce | dim_2phase / T0 | 189 | **107** | 91 | port | 2.08× |
| allgather | dim_2phase / T1 | **815** | 1034 | 520 | port | 1.57× |
| alltoall | flat / T0 | **1044** | 1912 | 637 | arc_load | 1.64× |

偏转是 `ring_base` 的真实代价而不只是 makespan：`alltoall` m=1 偏转率 **0.0168/flit**，m=13 升到 **0.198/flit**；维度树类流量下偏转率为 0（固定维序下所有转向同向，桥看不到互相转向，与 `islip2d-mesh-ring-8x6.md` §7 的结论一致）。乱序与重组峰值同表给出。

## 3. Part 1b：allgather 的 T_avg（R = 1 / 5 / 13）

定义与 mesh 侧完全一致：自由多轮 rigid pack 实测 `T_R`，`II_eff = (T_R − T1)/(R − 1)`，`T_avg = T1 + (R−1)/2 · II_eff = (T1 + T_R)/2`。R=1 时 `T_avg ≡ T1`。

| 方案 | ports | T1 | T₅ | T₁₃ | II_eff | T_avg(1) | T_avg(5) | T_avg(13) |
|---|---|---|---|---|---|---|---|---|
| dim_2phase / T1 | 2 | **68** | 176 | 413 | 28.75 | **68** | **122.0** | **240.5** |
| flat / T0 | 2 | 76 | 271 | 664 | 49.00 | 76 | 173.5 | 370.0 |
| dim_2phase / T1 | 1 | 85 | 278 | 674 | 49.08 | 85 | 181.5 | 379.5 |
| flat / T0 | 1 | 86 | 318 | 786 | 58.33 | 86 | 202.0 | 436.0 |
| dim_2phase / T0 | 1 | 93 | 328 | 813 | 60.00 | 93 | 210.5 | 453.0 |
| ring_rotate / T0 | 1 | 564 | 752 | 1128 | 47.00 | 564 | 658.0 | 846.0 |
| halving_doubling / T0 | 1 | 240 | 815 | 1958 | 143.17 | 240 | 527.5 | 1099.0 |

**环内部排序不随 R 翻转**：`dim_2phase/T1` 在 R=1、5、13 三档都最优。旋转虽然 `II_eff` 最小（47.0，等于弧负载下界），但 T1=564 的填充代价在 R ≤ 47 内都压不住——**II_eff 最优 ≠ T_avg 最优**，这正是需要同时报三档的原因。

### 环 vs 8×6 mesh（同一 T_avg 定义）

mesh 侧扫自己的设计变量（crossbar 写宽度 W、抽取率 E、FIFO 深度 B，378 个设计点），下表取该 R 下的 mesh 最优点。**环必须按环站端口数分开报，因为「每环站 1 个上/下环点」和「2 个」是两种硬件预算，而结论正好在这里翻转。**

| R | mesh 最优 | mesh T_avg | 环 T_avg（1 端口） | ring/mesh | 环 T_avg（2 端口） | ring/mesh |
|---|---|---|---|---|---|---|
| 1 | axis+CCW | 96 | **85** | **0.885×**（环胜） | **68** | **0.708×**（环胜） |
| 5 | axis+CCW | 180.5 | 181.5 | 1.006×（mesh 胜） | **122.0** | **0.676×**（环胜） |
| 13 | Hamilton bi-tree | 319.0 | 379.5 | **1.190×（mesh 胜）** | **240.5** | **0.754×**（环胜） |

两条必须一起说的翻转：

- **mesh 自己的最优方案在 R=13 换人**：axis+CCW（T1=96、II_eff=42.25）→ Hamilton bi-tree（T1=210 但 **II_eff=1.0**）。深流水下 II_eff 支配一切，T1 大一倍也无所谓。
- **1 端口的环在 R=1 赢、R=5 打平、R=13 输 1.19×**。环在 R=1 赢是**跨度优势**（数据跨度只有 mesh 直径的一半）；到 R=13 绑定资源变成环站那**一个**上/下环点，而 Hamilton bi-tree 用 II_eff=1.0 直接流水过去。**只有 2 端口的环三档全胜。**

因此「环比 mesh 快 1.3~1.5×」这句话**必须带上端口数**，否则它是本轮最容易误导人的一个数字。

## 4. Part 2：四个杠杆与它们各自的可 falsify 预测

| 杠杆 | 预测 | 实测 | 判定 |
|---|---|---|---|
| 弧多播 | broadcast 上环次数从 47 降到 1+行数、链路流量降约 47× | 上环 flit **611 → 182**（3.36×），makespan **323 → 95**（3.4×） | 方向对，**倍数远小于 47×**：下环端口与 ramp 不受多播影响，成为新瓶颈 |
| 双向半弧 | 跨度减半、峰值弧负载减半 | 多播方案流量比 **1.00×**、makespan 比 1.30~1.64× | **半条推翻**：只减跨度不减负载 |
| 满环旋转 | 恰好打到弧负载下界、链路利用率趋近 100% | II_eff **= 47.0 = 弧负载**（成立）；关键弧利用率 R=47 时 **81.0%**，闭式 `II·R/(T1+II·(R−1))` | **半条推翻**：打到界，但利用率只渐近 |
| L1 累加链 | RS+AG 流量是 reduce+bcast 的 (N−1)/N 倍但并发度高得多 | allreduce m=13：dim_2phase/**T1 284 拍（上环 793 flit）** < halving_doubling/T0 309 拍（3744 flit）< dim_2phase/T0 365 拍（1222 flit）；reduce 相对 gather 省 **1.74×** 流量、**3.48×** makespan | 成立，但**流量最少的不是 makespan 最小的**：halving_doubling 用 4.7× 的流量换并发度，仍比 dim_2phase/T0 快 |

打包顺序也有量：同一 allgather/T1/m=13 四种填充顺序的 makespan 差 **238 拍**（`arc_desc`/`flit_desc`/`pressure` 都是 815，`flowid` 是 1053）。**顺序不是自由参数，是必须扫的。**

## 5. 容错

场景集 = **27 个**：环特有的**绕回段失效**（5）+ 同环**分散死点**（4）+ 仓库既有的 link / node / quadrant 洞（18）。每场景报四件事：免重编译可行性、makespan 膨胀比、**做功归一化膨胀比**、是否需要追加修复相位。

| 方案 | 免重编译 | 可重编译 | 不可行 | 需修复相位 | 最坏做功归一化膨胀 |
|---|---|---|---|---|---|
| broadcast / dim_2phase / T1 | 9 | 17 | 1 | 2 | 1.35× |
| broadcast / flat / T0 | 6 | 20 | 1 | 0 | 1.00× |
| allgather / dim_2phase / T1 | 0 | 26 | 1 | 12 | 1.56× |
| allgather / flat / T0 | 0 | 26 | 1 | 0 | 1.14× |
| **allgather / ring_rotate / T0** | 7 | **0** | **20** | 0 | — |
| reduce / dim_2phase / T0 | 6 | 18 | 3 | 0 | 1.24× |
| reduce / flat / T0 | 5 | 21 | 1 | 0 | 1.03× |
| allreduce / dim_2phase / T1 | 6 | 20 | 1 | 2（最多 3 个相位） | 1.19× |

修复相位是显式构造的：行/列相位交替，**持有缺失数据且与需要它的节点同环**的那个节点补发；每个缺失项**只指派一个供给者**——对归约型集合通信这一点是必须的，两个供给者转发重叠的部分和会重复累加，而集合语义的 item-set 模型不会报错。

## 6. 抗抖动

三种模型（`uniform_jitter` / `distance_skew` / `burst`）× J ∈ {0,2,…,512}，三种再同步策略：

- `global_shift`（硬 barrier）：makespan **精确等于**原 makespan + 最迟释放量。吸收为 0。
- `phase_shift`（相间再同步）：**从不劣于** global_shift（3 模型 × 3 J 全部成立），但同样不让单条传输滑进松弛。
- `repack`（带释放约束重编译）：唯一能吸收的策略，吸收量 = 打包器留下的真实松弛，且重编译结果仍**逐条通过 D-R 复核**。

| 方案 | makespan | slack p50 | J=256 burst 下吸收 |
|---|---|---|---|
| allgather / flat / T0 | 1044 | 534 | **170** |
| allgather / dim_2phase / T1 | 815 | 97 | 70 |
| reduce / flat / T0 | 331 | 152 | 100 |
| allreduce / dim_2phase / T1 | 284 | 28 | 52 |
| broadcast / dim_2phase / T1 | 95 | 6 | **0** |
| allgather / ring_rotate / T0 | 1128 | 4 | **0** |

抖动注入后**交付集合恒等**（48 个节点收到的 item 集合逐项不变），这是断言而不是计数比较。

## 7. 与预期相反的结果（单列）

1. **旋转拍图链路利用率不到 1.0**（预测 1.0，实测 R=47 时 81.0%）。打到弧负载下界这半条成立，利用率那半条不成立；闭式解释了为什么：填充 T1 永远摊不掉。
2. **连续死点洞不需要环站 bypass mux**（预测「一个死点等于两处断环」）。2-连通环可以绕远路；mux 的价值在**分散死点**上，那才会真把环切段。
3. **一个死节点逼出一个额外相位，而不是一次重路由**（计划假设重路由足够）。维度切片的行列唯一交点是单点故障，81 次重编译中 16 次必须追加修复相位。
4. **双向半弧不减负载**（预测峰值弧负载减半）。多播弧无论朝哪边走都在同样站点丢副本，省的是 span。
5. **paper 机制在漏斗型集合通信上打赢静态拍图**（reduce m=13：107 vs 189 拍）。原因是下环端口的记账粒度而非偏转，端口敏感性一列量化了这个差距（放宽到 2 端口后 189 → 124 拍）。
6. **T1 硬件对 alltoall / gather / reduce 的收益精确为 0**。这不是负面结果的粉饰：验证套件对 17 个 (pattern, algo) 对断言 T1 与 T0 逐字段相同。
7. **1 端口的环在深流水下打不过 mesh**（R=13 输 1.19×），尽管它在单发时延上赢 0.885×。计划隐含假设环的优势会随 R 保持；实际是**绑定资源从跨度换成了环站端口**。同时 mesh 的最优方案自己也在 R=13 换人（axis+CCW → Hamilton bi-tree，II_eff 从 42.25 掉到 1.0），所以两边的排序都不能只在一个 R 上量。

## 8. 已知局限

1. **拍图模型的下环端口记账比 `ring_base` 粗。** 拍图把抽取点整段（m·σ 拍）独占给一次传输，`ring_base` 按 `RAMP_BW` 逐 flit 交错。端口敏感性一列**给出了差距的界，没有关上它**——要关上就得把拍图的资源粒度改成逐 flit，那是另一套打包器。
2. **归约建模为 item-set 并集 + 尺寸守恒折叠。** 流量与依赖序精确，但**不含算术**：L1 内加法器的时延折进 `RAMP`，没有单独建模。
3. **`ring_islip2d` 是 control 不是竞争者。** 它每节点每轮授一条 flit，2256 条消息要 63 轮，makespan 差一个数量级，读作同能力调度对照。
4. **抖动只注入在源端释放时刻。** 飞行中抖动需要 transport 模型，拍图 replay 做不到。
5. **故障重编译假设离线编译器拿到完整故障表。** 不声称重编译耗时，也不声称新表怎么分发。
6. **σ = 1 贯穿全部拍图工作。** 金属恒定口径（环 σ=2，依据环链路集与折叠 torus 相同）没有在本轮重跑；跨 fabric 比较必须回到 `islip2d-mesh-ring-8x6.md` §3 的双口径纪律。
7. **环 vs mesh 的对照只在「各自设计空间内取最优」这个口径下成立。** mesh 侧扫 W/E/B 三个设计变量（378 点），环侧只扫环站端口数，两边的设计空间不同构，所以表里同时给 1 端口和 2 端口两行而不是一个总结数。加 R=13 后 mesh 扫描约 **900 CPU-分钟**（已改多进程，5 进程约 2.5 小时）；并行版与串行版在抽样点上逐位相同，且复跑出的 axis+CCW T1=96 / T₅=265 与既有 `report_multi_area_makespan.html` 一致。

## 9. 复现

```bash
cd utils
python3 dse_ring_collectives_8x6.py            # Part 1 + 拍图  -> results/ring_collectives_8x6.json
python3 dse_ring_tavg_8x6.py                   # T_avg R=1/5/13 -> results/ring_tavg_8x6.json
python3 dse_ring_robust_8x6.py                 # 容错 + 抖动    -> results/ring_robust_8x6.json
python3 export_ring_calendars.py               # 拍图导出       -> results/calendars/ring_*.json
python3 verify_ring_collectives_8x6.py         # 56 项断言
python3 dse_multiflit_area_makespan.py --jobs 5 # mesh 侧 R=1/5/13（约 900 CPU-分钟，5 进程约 2.5 小时）
                                               # 必须在 dse_ring_tavg_8x6.py 之前跑，否则环 vs mesh 一列会显式标注缺失
python3 gen_ring_collectives_report.py         # -> results/report_ring_collectives_8x6.html
python3 gen_multi_area_report.py               # -> results/report_multi_area_makespan.html（§5.5 深度扫描）
```

新增代码：`utils/rg_ring_collectives.py`、`utils/rg_ring_calendar.py`、`utils/dse_ring_collectives_8x6.py`、`utils/dse_ring_tavg_8x6.py`、`utils/dse_ring_robust_8x6.py`、`utils/export_ring_calendars.py`、`utils/verify_ring_collectives_8x6.py`、`utils/gen_ring_collectives_report.py`；扩展：`utils/rg_ring_topo.py`（多播 footprint + `verify_dr` 多播分支）、`utils/dse_multiflit_area_makespan.py`（R 列表 + 多进程）。
