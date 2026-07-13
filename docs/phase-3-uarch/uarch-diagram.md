# Arch-A5 微架构图（易读版）

**架构：** Arch-A5 SparseCal-SharedPool-CalFork-ZB-NoCombine  
**要点：** 上蓝路径 = 日历零缓冲；下灰路径 = SharedPool BG；虚线框 = 明确不做。

交互式彩色总览（推荐）：Cursor Canvas `arch-a5-uarch.canvas.tsx`。

---

## 总览（一张图看懂）

```mermaid
flowchart TB
  subgraph ingress["入口"]
    IN["5× 入端口<br/>N E S W L · 512b"]
    PE["PE / NI"]
  end

  subgraph cal["日历路径 · 零缓冲"]
    SC["SparseCal<br/>2×128×23 事件表"]
    MATCH{"时隙匹配?<br/>slot == counter"}
    FORK["CalFork<br/>原子 out_port_mask"]
    SC --> MATCH
    MATCH -->|命中| FORK
  end

  subgraph bg["背景 / 降级路径"]
    POOL["SharedPool-BG<br/>池 28 + 预留 5×2 = 38"]
    XY["XY 路由<br/>先 X 后 Y"]
    SA["开关分配<br/>软优先级"]
    POOL --> XY --> SA
  end

  WD["Watchdog<br/>超时 / 错口 → 降级"]
  XB["5×5 × 512b 交叉开关"]
  OUT["5× 出端口"]
  NO["不做：combine / DCA<br/>归约在 PE"]

  IN --> MATCH
  PE --> POOL
  FORK --> XB
  SA --> XB
  MATCH -.->|未命中·违规| WD
  WD -->|escape 无损入池| POOL
  XB --> OUT
  FORK -.-> NO
```

**读图顺序：** ① 问「当前时隙有没有日历事件？」→ 有则走上排 CalFork；② 没有则走下排 SharedPool+XY；③ 出错走 Watchdog 降级，仍不丢包。

---

## 共享池怎么分配

```mermaid
flowchart LR
  REQ["某入端口要入队"] --> R{"该口占用 &lt; 2 ?"}
  R -->|是| RES["用本口预留"]
  R -->|否| S{"共享池已用 &lt; 28 ?"}
  S -->|是| SHR["用共享池"]
  S -->|否| BP["反压等待"]
```

日历路径**永不**入池。

---

## 参数速查

| 项 | 值 |
|---|---|
| SparseCal | 双 bank × 128 × 23 bit |
| SharedPool | 28 + 5×2 = **38** flit |
| 多播 | CalFork（非通用 stream_fork） |
| 归约 | Tier A · PE 本地 |
| 面积 / 功耗 | **0.746× / 0.90×** vs IQ-XY |

更细的模块说明见 [`uarch.md`](uarch.md)；架构级图见 [`../phase-2-architecture/architecture-diagram.md`](../phase-2-architecture/architecture-diagram.md)。
