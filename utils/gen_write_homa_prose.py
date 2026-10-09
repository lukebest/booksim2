"""Prose blocks for gen_write_homa_report.py (Chinese).

PROSE_ABSTRACT / MESH / RING / SUMMARY / LIMITS / PARAM / V1 are str.format
templates over the names in build(); literal braces must be doubled.
MECH / MAP / SETUP / FILES are inserted verbatim.
"""

PROSE_ABSTRACT = """
<div class="box">
<p><b>做了什么。</b>把上一版「HA 回 DBID 时的逐包令牌池」换成与 Homa 类似的接收端驱动调度：
消息的前 U 个包不等授权直接发（unscheduled），接收端 HA 从首包得知消息长度后，按剩余长度最短优先
（SRPT）在最多 D 条消息之间发 grant，每条消息「已授权但未收到」的包数不超过窗口 W，grant 由包到达来驱动。
负载改为<b>消息</b>（1/8/64 包，占比 50/30/20%），目的地均匀 alltoall 写。</p>
<p><b>结论（单种子，误差未量化）。</b></p>
<ul>
<li><b>Homa 类比基线好，但远不如集中式。</b>过载区 goodput 峰值（flit/节点/周期）：
mesh 基线 {gm[base]:.3f} → Homa 均衡点 {gm[homa]:.3f}（{relm[homa]}），集中式 {gm[central]:.3f}（{relm[central]}）；
ring 基线 {gr[base]:.3f} → Homa {gr[homa]:.3f}（{relr[homa]}），集中式 {gr[central]:.3f}（{relr[central]}）。
把 W 压到 1 的吞吐优先点可到 mesh {gm[tp]:.3f}、ring {gr[tp]:.3f}，代价是 64 包消息 FCT 恶化一个数量级。</li>
<li><b>集中式的峰值被高估。</b>集中式在过载区的 goodput（mesh {gm[central]:.3f}、ring {gr[central]:.3f}）超过了均匀流量的解析上限
（mesh {anc[mesh_xy]:.3f}、ring {anc[ring_fixed]:.3f}），说明过载时被调度出去的流量构成偏向了「更容易送」的流，
不是稳定吞吐。按<b>最大稳定注入率 λ*</b>比：mesh 基线 {lsm[base]} / 集中式 {lsm[central]} / Homa {lsm[homa]}；
ring 基线 {lsr[base]} / 集中式 {lsr[central]} / Homa {lsr[homa]}。λ 网格步长 0.05，mesh 上 Homa 与基线的 λ* 差异在分辨率以内。</li>
<li><b>Homa 的真正优势是短消息延迟。</b>λ=0.3 时 1 包消息平均 FCT：
mesh {f1m[base]:.0f}（基线）/ {f1m[central]:.0f}（集中式）/ {f1m[homa]:.0f}（Homa）；
ring {f1r[base]:.0f} / {f1r[central]:.0f} / {f1r[homa]:.0f}。在 λ=0.3（三者均稳定）Homa 的短消息比集中式还快（不用等 REQ→grant 往返；过载后优势消失，如 mesh λ=0.5 时 Homa 的 1 包 FCT 反而高于集中式），
但 64 包消息 FCT 变差（mesh {f64m[base]:.0f} → {f64m[homa]:.0f}，ring {f64r[base]:.0f} → {f64r[homa]:.0f}）。</li>
<li><b>goodput 的提升来自「窗口限流」，不是 SRPT。</b>均衡点上 SRPT 与 FCFS 的 goodput 与 FCT 几乎一致（SRPT 在 U=W=8 时没有机会起作用），
去掉 unscheduled 前缀不改变 goodput，只让 1 包消息 FCT 变慢。SRPT 的作用要在 W 很小时才看得见（见消融）。</li>
</ul>
</div>
<div class="box warn"><p><b>需要先知道的两点：</b>(1) 规范 Homa 的窗口是 RTT×带宽（这里约 100 个包以上），超过了最大消息长度（64 包），
规范参数下所有消息都是 unscheduled，调度形同虚设；本文用 W≤16 的<b>亚 BDP 窗口</b>，是对 Homa 的有意偏离。
(2) 优先级队列没有建模，Homa 的另一半收益（网内抢占）不在这些数字里。</p></div>
"""

PROSE_MECH = """
<p>以 Homa 的五个要素为蓝本，映射到「写事务：REQ → DBID → 数据」的 NoC 流程：</p>
<table><tr><th>Homa 要素</th><th>本仿真的实现</th></tr>
<tr><td>Unscheduled 前缀</td><td>消息到达即发出前 U=min(U, L) 个包，不等 grant。接收端从任一首包得知总长度 L。U=0 时先发 REQ，REQ 到达 HA 后才开始 grant。</td></tr>
<tr><td>接收端驱动</td><td>每个目的 HA 自己维护活跃消息表，grant 只由接收端发出，grant 沿控制通道回源，延迟等于 Topology 的 wire_distance（+RAMP）。</td></tr>
<tr><td>SRPT</td><td>按 (剩余未收包数, 到达时间, id) 排序，取前 D 条；<code>homa_sched=fcfs</code> 时按到达时间。</td></tr>
<tr><td>Overcommit 度 D</td><td>同一时刻最多对 D 条消息发 grant；D 大于同时活跃消息数则等于不限。</td></tr>
<tr><td>窗口 / 自时钟</td><td>每条消息「已授权但未收到」包数 ≤ W；每个包送达后释放窗口，立即触发该 HA 重新发 grant。</td></tr>
<tr><td>优先级</td><td><b>未建模</b>。底层基线网络（mesh：VC+credit+iSLIP，ring：E-tag/I-tag+偏转）原样不动，Homa 只在注入前决定「哪个包何时放进去」。</td></tr>
</table>
"""

PROSE_MAP = """
<p><b>与 CHI 写事务的对应关系。</b>HA 回 DBID 就是「grant」：每个 DBID 授权源端发送一个包的数据。
这里有一个与标准 CHI WriteNoSnp 不同的地方：unscheduled 前缀等价于<b>预先发放的 DBID 信用</b>，在标准 CHI 里源端必须先收到 DBID 才能发数据，
所以 unscheduled 需要协议扩展（例如在 REQ 里携带预授权，或者对 HA 做 credit 预配置）。去掉 unscheduled（U=0）的变体就是标准 CHI 的合法映射，其结果见消融表。</p>
<p>控制报文（REQ、DBID）被建模成<b>无争用的专用控制通道</b>，时延等于数据通道的几何距离，所以控制面的拥塞效应没有计入——这对 Homa 与集中式同样有利。</p>
"""

PROSE_SETUP = """
<ul>
<li><b>拓扑</b>：8×6 共 48 节点，H=7，V=9，RAMP=2，RAMP_BW=2；mesh 为 VC+credit+iSLIP（buf_depth=20），ring 为无缓冲二维环（E-tag/I-tag+偏转）。</li>
<li><b>负载</b>：开环泊松到达的消息写，长度混合 1/8/64 包 = 50/30/20%（平均 {mean_len:.1f} 包/消息），目的地在其余 47 个节点上均匀。
注入率 λ 的单位是 <b>包/节点/周期</b>，因此消息到达率为 λ / 平均长度。</li>
<li><b>goodput</b>：measure 窗口内<b>送达</b>的 flit 数 / (48 × measure)，单位 flit/节点/周期。
旧报告的 <code>accepted</code> 是按注入侧统计的，会低估基线（源端 FIFO 里排队尚未注入的包），因此这里改用送达口径。</li>
<li><b>稳定</b>：measure 窗口内送达 flit ≥ 同窗口实际到达 flit 的 97%（用实际负载而非名义 λ，以免泊松涨落把低载点误判为发散）。<b>λ*</b> = 扫描中最大的稳定点。</li>
<li><b>FCT</b>：消息从到达到最后一个包送达的周期数，分 1/8/64 包三类报均值与 p99。过载点只统计「已完成」的消息，存在幸存者偏差。</li>
<li><b>p50</b>：逐包延迟，口径含消息内排队和等待授权，只作参考；Homa 与基线之间的比较以 FCT 为准。</li>
<li><b>时长与种子</b>：warmup 3000、measure 12000 周期，一个种子，无置信区间。</li>
</ul>
"""

PROSE_PARAM = """
<p>W 与 D 是 Homa 类调度的两个主要旋钮。在 W∈{{1,2,4,8,16}}、D∈{{2,4,8,16,48}} 的 25 格网格上、λ∈{{0.3, 0.5, 1.0}} 上扫描（图中每点 = 一格）：</p>
<ul>
<li><b>只按 goodput 选会选到 W=1（stop-and-wait）</b>：每条消息一次只放行一个包，把网络变成近乎「每源每宿一个包」的极强限流，goodput 最高，
但 64 包消息要等 64 次 grant 往返（mesh 约 {tp_f64m:.0f} 周期，是基线的 {tp_ratio_m:.0f} 倍）。这不是可接受的工作点。</li>
<li><b>均衡点</b>（本文主结果）：在 λ=0.3 时 64 包消息 FCT ≤ 基线的 2 倍的约束下取平均 goodput 最大的格。mesh、ring 都选到 <b>{wdm}</b> / <b>{wdr}</b>。
</li>
<li><b>吞吐优先点</b>：不加约束的最优，mesh {tpm}，ring {tpr}，作为 Homa 在吞吐上的上界单独画出。</li>
<li>W 增大时 goodput 单调下降并收敛到基线（W=16 时 mesh 约 0.32，与基线相同）：窗口一旦不小于消息长度，所有包都是 unscheduled，调度退化为无调度。
D 增大到 8 以上之后基本饱和——同一目的端同时活跃的消息数本来就不多（λ=0.3 时 HA 同时活跃的消息峰值 mesh {ctrl[mesh][ha_active_max]}、ring {ctrl[ring][ha_active_max]}）。</li>
</ul>
"""

PROSE_MESH = """
<p><b>基线</b>：λ* = {lsm[base]}，峰值 {gm[base]:.3f}，λ=1.0 时下降到 {e1m[base]:.3f}（比峰值低 {mesh_collapse:.0f}%）。
这是过载下的拥塞回落（本负载下信用反压与源队列队头阻塞叠加）。</p>
<p><b>集中式</b>：λ* = {lsm[central]}，λ=0.35 时 goodput {mesh_c035:.3f}（受解析上限 {anc[mesh_xy]:.3f} 约束）；过载区 {gm[central]:.3f} 超过解析上限，是流量构成漂移，不能当稳定吞吐。
在稳定区它没有明显劣势，且 λ=0.5（基线已发散）时 1 包 FCT 仍只有 {mesh_c05_f1:.0f} 周期，而基线 {mesh_b05_f1:.0f}。</p>
<p><b>Homa 均衡点</b>（{wdm}）：λ* = {lsm[homa]}，过载区平台 {gm[homa]:.3f}（{relm[homa]}），λ=1.0 为 {e1m[homa]:.3f}，坍塌比基线轻。
λ=0.3 时 1 包消息 FCT {f1m[homa]:.0f}（基线 {f1m[base]:.0f}），64 包消息 {f64m[homa]:.0f}（基线 {f64m[base]:.0f}）。
</p>
<p><b>Homa 吞吐优先点</b>（{tpm}）：平台 {gm[tp]:.3f}（{relm[tp]}），但在 λ=0.1 就已不稳定（该点 goodput 比到达率低约 6%），64 包消息 FCT 在 λ=0.3 达 {f64m[tp]:.0f}。</p>
<p><b>消融（均衡点）</b>：FCFS 与 SRPT 的 goodput、FCT 完全重合（U=W=8，8 包消息整条 unscheduled，调度只作用于 64 包消息，SRPT 无可排序之物）。
U=0 时 goodput 不变，λ=0.3 的 1 包 FCT 从 {mesh_fct1_main:.0f} 升到 {mesh_fct1_nounsched:.0f}——这就是「等一轮 REQ→grant」的代价。
控制时延减半只改善 64 包消息 FCT（{mesh_fct64_main:.0f} → {mesh_fct64_half:.0f}），对 goodput 无影响。
W=2、D=4 时 SRPT 才显现：8 包消息 FCT（λ=0.5）{mesh_srpt8:.0f} 对 FCFS {mesh_fcfs8:.0f}，goodput {mesh_srpt_g:.3f} 对 {mesh_fcfs_g:.3f}。</p>
<p><b>集中式的控制往返 T_rtt</b>：把 16 改成 64，goodput 逐点完全相同，1 包 FCT 恰好多出约 48 周期（= T_rtt 的增量），说明集中式的吞吐不依赖往返时延，只是延迟加上一个固定量。</p>
"""

PROSE_RING = """
<p><b>基线</b>：λ* = {lsr[base]}，峰值 {gr[base]:.3f}，λ=1.0 时为 {e1r[base]:.3f}，比峰值低 {ring_collapse:.0f}%，与偏转吃掉环带宽的解释一致，但本文没有单独量化偏转。</p>
<p><b>集中式</b>：λ* = {lsr[central]}（稳定区 goodput {ring_c05:.3f}，接近解析上限 {anc[ring_fixed]:.3f}）；过载区 {gr[central]:.3f} 同样含流量构成漂移。
ring 上集中式的收益比 mesh 更大；可能的原因是无缓冲环没有缓冲来吸收冲突，事先避开冲突比事后偏转划算（推测，未做隔离实验）。</p>
<p><b>Homa 均衡点</b>（{wdr}）：λ* = {lsr[homa]}，平台 {gr[homa]:.3f}（{relr[homa]}）；λ=0.3 的 1 包消息 FCT {f1r[homa]:.0f}（基线 {f1r[base]:.0f}，集中式 {f1r[central]:.0f}），
64 包消息 {f64r[homa]:.0f}（基线 {f64r[base]:.0f}）。</p>
<p><b>Homa 吞吐优先点</b>（{tpr}）：平台 {gr[tp]:.3f}（{relr[tp]}），λ*={lsr[tp]}，64 包消息 FCT {f64r[tp]:.0f}。</p>
<p><b>消融</b>：与 mesh 一致——SRPT/FCFS 在均衡点重合，U=0 使 1 包 FCT 从 {ring_fct1_main:.0f} 升到 {ring_fct1_nounsched:.0f}，控制时延减半让 64 包 FCT 从 {ring_fct64_main:.0f} 降到 {ring_fct64_half:.0f}。
W=2、D=4 时 SRPT 的 8 包 FCT（λ=0.5）{ring_srpt8:.0f} 对 FCFS {ring_fcfs8:.0f}。集中式 T_rtt 16→64 同样只加固定延迟、不改 goodput。</p>
"""

PROSE_SUMMARY = """
<ul>
<li><b>排名（按稳定区 λ* 与过载平台一起看）</b>：集中式 &gt; Homa（吞吐优先 ≳ 均衡） &gt; 基线。
ring 上相对差距更大（集中式 {relr[central]}，Homa {relr[homa]}），mesh 上更小（{relm[central]} / {relm[homa]}）；集中式的数字含漂移，应偏保守地看。</li>
<li><b>Homa 不能代替集中式的原因（推测，未单独验证）</b>：Homa 的 grant 只看接收端自己的入口，不知道路径上哪条链路已被占用；
集中式的 interval 冲突域则按链路预约。仿真里 Homa 的 goodput 在 D≥8 后对 D 基本不敏感、主要由 W 决定，且 λ* 只比基线提高 0 到 0.1，与「只能限流、不能避冲突」一致，
但要证实需要对照一个「接收端限流 + 路径感知」的变体。</li>
<li><b>延迟上的取舍</b>：Homa 的 unscheduled 前缀让短消息不付 REQ/grant 往返，延迟优于集中式；长消息则因窗口小而变慢。
如果业务以小写为主，Homa 类更合适；若以大块写和吞吐为主，集中式更好。</li>
<li><b>代价（粗估，未综合）</b>：集中式需要全局 interval 表，旧报告给出 mesh 99,840 bit、ring 184,320 bit。
Homa 类的状态在每个 HA 内：每条活跃消息约 (源, 剩余, 已授权, 已收) ≈ 48 bit（假设值）；按 D=48 的最坏配置 48×48×48 ≈ 110 kbit，与集中式同量级，
但实测同时活跃的消息峰值只有约 {ctrl[mesh][ha_active_max]}，按实测规模约 {homa_state_kbit:.0f} kbit。状态是分布的，没有全局串行化点；
控制报文约 {ctrl[mesh][ctrl_msgs_per_cy]:.1f} 条/周期（λ=0.3，全网）。</li>
<li><b>统计口径提示</b>：旧报告用 <code>accepted</code>（按注入侧统计）在单包负载下给出基线过载回落 mesh −42%、ring −16%；换成送达口径 goodput 后同一数据是 mesh −5%、ring 0%（v1 报告 §6 的结论，本轮扫描未重复）。本报告的消息负载下基线回落更明显（mesh {mesh_collapse:.0f}%、ring {ring_collapse:.0f}%），不能与单包负载的数字直接比较。</li>
</ul>
"""

PROSE_V1 = """
<p>上一版（v1）的分布式方案是在 HA 的 DBID 发放处放一个 K 个令牌的池，按「服务最少者优先」发放，对每个包都要先 REQ→DBID 再发数据。
它与 Homa 类的区别是：v1 对每个包都限流且没有 unscheduled，没有按消息剩余长度排序，也没有按消息的窗口。</p>
<ul>
<li><b>单包负载</b>（下表）：v1 的峰值与基线几乎相同（mesh {v1_mesh_pk:.3f} 对 {v1_mesh_base:.3f}），低载延迟翻了约 3 倍。逐包 REQ/DBID 往返没有带来吞吐，只带来延迟。</li>
<li><b>消息负载</b>：K=64 的池峰值 mesh {gm[pool]:.3f}（{relm[pool]}）、ring {gr[pool]:.3f}（{relr[pool]}）；
与 Homa 均衡点 mesh {gm[homa]:.3f}、ring {gr[homa]:.3f} 相近（ring 上池还略高）。
所以<b>在 goodput 上 Homa 并没有比池明显更强</b>，差别在于 1 包消息 FCT（λ=0.3：池 mesh {pool_f1m:.0f} 对 Homa {f1m[homa]:.0f}；ring {pool_f1r:.0f} 对 {f1r[homa]:.0f}）。</li>
<li><b>退化检查</b>：在单包负载下 Homa 没有任何调度可做（消息长度 1 ≤ U），结果与基线逐点完全相同、grant 数为 0，这也是实现的一条正确性检查。</li>
</ul>
"""

PROSE_LIMITS = """
<ul>
<li><b>单种子、单次运行</b>，没有置信区间。λ 网格步长 0.05，λ* 的分辨率有限；mesh 上 Homa 与基线的 λ* 相同不代表没有差别。</li>
<li><b>亚 BDP 窗口</b>：规范 Homa 的窗口约 RTT×带宽，超过最大消息长度（64 包），那样所有消息都是 unscheduled。W≤16 是为了让调度起作用，不是 Homa 的原始设置。</li>
<li><b>没有建模优先级</b>：网内的优先级队列和抢占是 Homa 低延迟的另一半来源，这里缺失；mesh 的 VC 与 iSLIP 没有按剩余长度排序。</li>
<li><b>Unscheduled 不是合法的 CHI 写流程</b>：需要 REQ 携带预授权或预配 DBID 信用；U=0 的变体才是标准映射。</li>
<li><b>控制通道无争用</b>：REQ/grant 的时延按几何距离计算，没有排队。对集中式与 Homa 类都乐观。</li>
<li><b>集中式过载区 goodput 偏高</b>：流量构成漂移导致超过解析上限，比较时应看稳定区。</li>
<li><b>FCT 的幸存者偏差</b>：过载点只统计已完成的消息，长消息被排除，数值偏低。</li>
<li><b>突发负载带来噪声</b>：消息长度 64 包的突发使 p99 与均值波动大，W/D 网格中差 0.01 以内的 goodput 差异不应解读。</li>
<li><b>均衡点依赖选择准则</b>：「FCT₆₄ ≤ 2×基线」是人为约束，换约束会换点。</li>
</ul>
"""

PROSE_FILES = """
<ul>
<li><code>utils/rg_homa_dist.py</code>：Homa 类 HA 调度仿真（<code>HomaSim</code>）</li>
<li><code>utils/rg_steady_des.py</code>：稳态仿真，新增消息注入器 <code>MsgInjector</code>、送达口径 goodput、FCT 统计、<code>mesh_homa</code>/<code>ring_homa</code> 配置</li>
<li><code>utils/rg_dbid_dist.py</code>：v1 逐包 DBID 池（作为对照）</li>
<li><code>utils/dse_write_homa_8x6.py</code>：扫描（网格、主曲线、消融）→ <code>results/write_homa_8x6.json</code>、<code>results/write_homa_grid.json</code></li>
<li><code>utils/verify_write_homa.py</code>：24 项校验 → <code>results/verify_write_homa_8x6.json</code></li>
<li><code>utils/gen_write_homa_report.py</code>、<code>utils/gen_write_homa_prose.py</code>：生成本报告</li>
<li><code>results/report_write_homa_8x6.html</code>：本报告（自包含）</li>
</ul>
"""
