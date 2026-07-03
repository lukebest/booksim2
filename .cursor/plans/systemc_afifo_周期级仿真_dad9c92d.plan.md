---
name: SystemC AFIFO 周期级仿真
overview: 在 4×4 mesh（4 个 2×2 reticle）上用 SystemC 2.3.4 建立周期级仿真，重点考察跨 reticle AFIFO 的行为与深度占用、两种读策略（贪婪读 vs 空时隙门控读）对全局 Hamilton 环与 hybrid B=2 vband 两方案 makespan 的影响。
todos:
  - id: trace-4x4
    content: 新增 utils/export_sc_trace.py：导出 4×4 两方案全网逐链路发送踪迹 JSON + golden makespan
    status: completed
  - id: sc-afifo
    content: 实现 sc/afifo.h：双时钟 Gray 同步 AFIFO（相位/抖动/S 级/反压/读策略接口）
    status: completed
  - id: sc-tb
    content: 实现 sc/mesh_tb.cpp + Makefile：4×4 四时钟域重放测试台，greedy/slot-gated 两策略
    status: completed
  - id: sc-sweep
    content: 新增 utils/run_sc_afifo_sweep.py：φ/σ/深度/策略/方案扫描 → results/sc_afifo_sweep.json
    status: completed
  - id: sc-verify
    content: 退化情形复现 4×4 golden；峰值占用与 Python 事件模型交叉核对
    status: completed
  - id: sc-report
    content: 生成 results/report_sc_afifo.html：两策略×两方案 AFIFO 深度/stall/makespan 对比与结论
    status: completed
isProject: false
---

# SystemC 周期级跨 Reticle AFIFO 仿真方案

## 背景
- 前一阶段的 Python 事件级模型（[utils/afifo_cdc.py](utils/afifo_cdc.py)）已给出 16×16 的解析/Monte-Carlo 结论；本阶段用 SystemC 做真正周期级（含时钟边沿、Gray 指针同步器）的验证与读策略研究。
- 拓扑缩小到 4×4 mesh，reticle = 象限 = 2×2，边界在 col/row 1|2，跨界链路两方向共 8 条（x 边界 4 + y 边界 4）。
- 环境已确认：系统安装 SystemC 2.3.4（`libsystemc-dev`，头文件 `/usr/include`，库 `libsystemc-2.3.4.so`），g++ 直接 `-lsystemc` 链接。

## 核心问题
1. AFIFO 占用时间序列与所需深度（同频不同相 + 抖动，Gray 同步 S 级）。
2. 读策略对比——这是新增重点：
   - **greedy**：AFIFO 非空即读出并注入下游链路（可能与本地环调度流量在同一链路上冲突，需要仲裁/暂存）。
   - **slot-gated**：仅当下游链路该周期存在空时隙（本地调度未占用）才读 AFIFO——回答“是否要根据空时隙才读 AFIFO”。
3. 两种策略下的 AFIFO 峰值深度、写侧 stall、makespan 相对 golden 的膨胀，按方案对比。

## 步骤

### 1. Python 侧：4×4 调度踪迹导出（新增 `utils/export_sc_trace.py`）
- 复用现有调度器在 4×4 下生成完整逐链路 flit 发送表（不只跨界链路，SystemC 要重放全网流量以模拟链路时隙占用）：
  - 全局环：`sim_hamilton_ring.simulate(hr.snake_cycle(4,4), True, 'bi', mx=4, my=4, ...)` 的 `collect=True` 边表。
  - hybrid B=2 vband：`sched_zerobuf_compare.cfg(4,4,4,6)` + `fp_hybrid_v(s, 2, True, 1)` + `export_events`。
- 输出 JSON：每条有向链路的 (send_cycle, flit_id, src) 列表 + golden makespan + 拓扑参数，写入 `results/sc_trace_ring_4x4.json`、`results/sc_trace_hybrid_4x4.json`。

### 2. SystemC 模型（新增 `sc/` 目录）
- `sc/afifo.h`：周期级双时钟 AFIFO 模块
  - 写域/读域各一个 `sc_clock` 等效的手工时钟进程（周期 1ns，读域相位偏移 φ + 每边沿抖动，用 `sc_time` 皮秒级建模）；
  - Gray 码写指针经 S 级触发器同步到读域（真实两拍同步器行为，非抽象延迟）；
  - 满信号（almost-full）经 S 级同步回写域产生反压 stall；
  - 参数：DEPTH、S、φ、σ、读策略端口（下游空时隙信号）。
- `sc/mesh_tb.cpp`：4×4 重放测试台
  - 每 reticle 一个时钟域（4 个域，相位独立）；
  - 域内链路按踪迹表逐周期重放（1 flit/cy 占用即为“时隙被本地调度占用”信号）；
  - 跨界链路插 AFIFO 实例（8 个）；读出端按策略（greedy / slot-gated）与本地重放流量复用下游链路；
  - 统计：每 AFIFO 每周期占用、峰值、stall 数、每 flit 端到端延迟、总 makespan；
  - VCD 输出可选（`--vcd`）便于查看波形。
- `sc/Makefile`：`g++ -O2 -lsystemc`。

### 3. 实验矩阵（新增 `utils/run_sc_afifo_sweep.py`，驱动 SystemC 可执行）
- 命令行参数化 SystemC 程序：`./mesh_tb --trace X.json --phase-seed N --sigma S --sync 2 --depth D --policy greedy|gated`。
- 扫描：φ Monte-Carlo（每 reticle 独立随机，30 seeds）× σ {0, 0.1, 0.2} × 深度 {1..6} × 策略 {greedy, gated} × 方案 {ring, hybrid}。
- 汇总 JSON → `results/sc_afifo_sweep.json`。

### 4. 验证
- 退化情形（φ=0 对齐、σ=0、S=0、深度充足、greedy）：SystemC makespan 须等于 Python golden（4×4 下先用第 1 步的调度器算出 golden 值）。
- AFIFO 峰值占用与 Python 事件模型（`afifo_cdc.sim_afifo` 在同参数下）逐链路一致（±1 内，因周期级相位处理更精细）。

### 5. 报告（扩展 `utils/gen_afifo_cdc_report.py` 或新增 `gen_sc_afifo_report.py` → `results/report_sc_afifo.html`）
- 内容：
  - 4×4/2×2-reticle 拓扑与 8 条跨界 AFIFO 位置图；
  - greedy vs slot-gated：AFIFO 峰值深度、stall、makespan 膨胀对比表（按方案 × σ）；
  - 占用时序波形示例（最忙 AFIFO，两策略并排）；
  - 结论：空时隙门控读是否必要（预期：ring 跨界链路时隙近满 → gated 更能保护环的 0-buffer 时序但增加 AFIFO 深度需求；hybrid 树阶段时隙较空 → 两策略差异小），以及推荐深度。

## 验证方式
- SystemC 编译零警告，退化情形 makespan == Python golden（两方案）。
- 周期级峰值占用 vs Python 事件模型交叉核对。