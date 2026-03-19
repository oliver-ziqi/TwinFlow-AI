# JaamSim Logistics Incident RCA (Kafka → LangGraph → Gemini → UI)

This repo wires your pipeline end-to-end:

1) Alerts are **produced to Kafka** (`alert-events`) either by:
   - `services/influx_alert_watcher.py` (poll InfluxDB alert bucket) **or**
   - `api/webhook_server.py` (FastAPI endpoint that forwards to Kafka)

2) `services/alert_event_consumer.py` consumes `alert-events`, runs the **LangGraph workflow** (PG configs + Influx metrics + Gemini),
   stores results to **Postgres**, and also publishes to Kafka (`analysis-results`).

3) `ui/streamlit_app.py` shows a **trace viewer** (per-node updates) and the final RCA output.

4) `services/mqtt_influx_bridge.py` can ingest JaamSim MQTT telemetry (for example `factory/f1`) into InfluxDB bucket `factory_data` for Grafana dashboards.

> **Important:** set `GEMINI_API_KEY` in env; do not hardcode API keys.

---

## 1) Project layout

```
jaamsim-ai-rca/
  api/
    webhook_server.py
  services/
    influx_alert_watcher.py
    alert_event_consumer.py
  ai/
    workflow.py
    pg.py
    influx.py
    utils.py
    types.py
  ui/
    streamlit_app.py
  db/
    init.sql
  docker-compose.yml
  requirements.txt
  .env.example
```

---

## 2) Event schema (Kafka message)

Topic: `alert-events`

```json
{
  "time": "2026-02-02T08:10:00Z",
  "thingId": "my_logistics_car2",
  "alert_type": "motion_stopped",
  "message": "Car has been stationary for 1 minute",
  "car_position": [-10.9, -13.8, 0.01],
  "branch_key": "branch_3",
  "branch_value": 2,
  "source": "influx-alerts-watcher"
}
```

`car_position`, `branch_key`, `branch_value` should be included by your alert producer (watcher/webhook).

---

## 3) Quick start

### 3.1 Start infra (Kafka + Postgres + InfluxDB)
```bash
docker compose up -d
```

### 3.2 Create venv + install deps
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
```

### 3.3 Configure env
Copy `.env.example` → `.env` and fill values (especially `GEMINI_API_KEY`).

### 3.4 Run producer (choose ONE)
**A) Influx watcher → Kafka**
```bash
python services/influx_alert_watcher.py
```

**B) Webhook → Kafka**
```bash
uvicorn api.webhook_server:app --host 0.0.0.0 --port 8001
```

Then POST an event:
```bash
curl -X POST http://localhost:8001/webhook/alerts -H "Content-Type: application/json" -d @sample_event.json
```

### 3.4.1 Optional: MQTT -> Influx bridge (factory inventory)
If you want Grafana to show live factory inventory from MQTT topics:
```bash
python services/mqtt_influx_bridge.py
```

Defaults:
- MQTT topic: `factory/+`
- Influx bucket: `factory_data`
- Measurement: `factory`
- Tags: `factory_id`, `thingId`, `sub_topic`, `topic`
- Field: `count` (for numeric payload)

Important envs:
- `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC`
- `INFLUX_URL`, `INFLUX_TOKEN`, `INFLUX_ORG`
- `INFLUX_FACTORY_BUCKET` (default `factory_data`)
- `FACTORY_ID_MAP_JSON` (default maps `f1..f4` to `my_factory_factoryA..D`)

### 3.5 Run consumer (Kafka → analysis → Postgres + Kafka)
```bash
python services/alert_event_consumer.py
```

### 3.6 Run UI
```bash
streamlit run ui/streamlit_app.py
```

Open http://localhost:8501

---

## 4) How it “assembles”

- **Watchers / webhook** produce alert JSON to Kafka (`alert-events`)
- **Consumer**:
  1. reads one alert
  2. calls `ai.workflow.build_workflow().stream(..., stream_mode="updates")`
  3. merges updates into a final state
  4. persists to Postgres `incident_analysis`
  5. publishes a compact result to Kafka `analysis-results`
- **UI** reads `incident_analysis` table and renders:
  - trace updates (per node)
  - final analysis markdown/json

---

## 5) Common issues

- If Influx tags/fields differ, update `ai/influx.py` filters (measurement/field/tag key).
- If your alerts don’t have `car_position`/`branch_*`, add them in producer.
- Gemini keys: use env `GEMINI_API_KEY` only.

---

## 6) Grafana dashboard for 4 factories

- Dashboard template file: `ui/grafana/factory_inventory_dashboard.json`
- Import it in Grafana, then select your InfluxDB datasource.
- The template expects:
  - Bucket: `factory_data`
  - Measurement: `factory`
  - Field: `count`
  - Tag: `factory_id` (`f1`,`f2`,`f3`,`f4`)

---

## 7) 项目整体架构

当前项目是一个“仿真数据 + 流处理 + 智能分析 + 可视化”的闭环系统：

1. 仿真与数据源层  
   - JaamSim 产生物流状态（小车位置、工厂/队列状态、分支状态）
   - 运行配置位于 `simulation/logistics/`

2. 事件采集与分发层  
   - `services/influx_alert_watcher.py`：从 Influx 告警桶轮询并推送到 Kafka
   - `api/webhook_server.py:/webhook/alerts`：接收外部事件并推送到 Kafka
   - Kafka 主题：`alert-events`（输入）、`analysis-results`（输出）

3. Agentic 分析层  
   - `services/alert_event_consumer.py` 消费 `alert-events`
   - 调用 `ai/workflow.py`（路由 + 事实收集 + RCA + 分支修复建议）
   - 结果写入 Postgres（`incident_analysis`）并回发 Kafka

4. UI 与控制层  
   - 新前端：`ui/index_new.html`（主操作界面）
   - 后端 UI API：`api/webhook_server.py` 下 `/ui-api/*`
   - 支持运行时启停 watcher/consumer/simulation、查看日志、触发分析、应用分支修改

---

## 8) 数字孪生面板设计

数字孪生画板位于 `ui/threejs-demo/index.html`，通过 iframe 集成到 `ui/index_new.html`。

主要设计点：

1. 坐标与场景
   - 使用统一世界缩放（`WORLD_SCALE`），保证“显示放大”不影响仿真原始坐标
   - 工厂节点、车辆、路径均按同一坐标映射规则渲染

2. 路网表达
   - 当前采用“工厂两点直连”的道路绘制
   - 灰色道路 + 中央白色虚线，便于区分运输通道

3. 运动更新
   - 小车位置来自 WS 事件流
   - 采用插值/外推策略平滑运动，降低低频发布带来的跳变感

4. 诊断能力
   - 面板内提供连接状态、包计数、最后一包时间，便于排查“卡顿是渲染问题还是上游断流”

---

## 9) Dashboard 设计

当前 Dashboard 以 `ui/index_new.html` 的 Live 视图为主，布局为：

1. 左侧（主区）
   - 上：Digital Twin（约 2/3 高度）
   - 下：Factory Inventory 折线图 + 四工厂当前值卡片（约 1/3 高度）

2. 右侧（AI Chat）
   - 聊天分析、修复建议确认、日志反馈
   - 输入框支持动态扩展（点击后升高，多行输入）

3. 图表体验
   - 支持 1m / 5m / 15m 窗口
   - 时间轴采用短时刻度显示（`HH:MM:SS`）并自动抽样，避免标签拥挤

备注：Grafana 模板仍保留在 `ui/grafana/factory_inventory_dashboard.json`，可作为独立监控大盘方案。

---

## 10) Agentic Workflow 决策链路设计

核心链路从“异常事件”到“可执行动作”：

1. 事件输入
   - 来源：Kafka 告警、Webhook 手工事件、UI 聊天事件

2. 路由与事实采集
   - `prepare_workflow_preview(event)`：识别意图与模式
   - 结合 Influx 指标、PG 配置、文件分支状态，形成事实集

3. RCA 推理与建议
   - `execute_planned_workflow(preview)`：执行规则 + 模型分析
   - 输出 `analysis`、`branch_fix_proposal`、证据链与下一步检查项

4. 人在回路（Human-in-the-loop）
   - 前端显示“Pending branch change”
   - 用户确认后调用 `/ui-api/branch/apply` 修改分支文件值

5. 异常兜底
   - 当 LLM 不可用（如 429 quota）时，`/ui-api/analyze` 会返回可读错误信息而非静默失败，保证交互可观测。

---

## 11) Reliability and Reasoning Improvements (English)

### 11.1 Exception Message Idempotency

**Problem**  
In this project, alerts are ingested by Watcher/Webhook, sent to Kafka, and then processed by the Consumer workflow. Because there is a delay between event emission and analysis completion, the same abnormal event can be reported multiple times in a short period (for example, repeated `motion_stopped` events). This causes duplicate consumption, duplicate RCA execution, and duplicate database writes.

**Root Cause**  
Alert generation is continuous/polling-based. Without a deduplication window, one business incident is treated as multiple independent events.

**Solution**  
Add time-window idempotency at ingress and consumer stages:
- Process only one event of the same class within a fixed window (for example, 60 seconds).
- Build an idempotency key from `event_type + entity_id + time_window`.
- Skip duplicated messages that hit the same key/window.

**Outcome**  
This reduces redundant workflow executions and repeated notifications, lowers Kafka/LLM/DB load, and improves result consistency and system stability.

### 11.2 AI Reasoning Logic Optimization

**Problem**  
Early reasoning tended to over-focus on local symptoms (for example, an empty queue) and could misidentify the wrong branch as root cause. In the logistics scenario, the model previously suggested fixing `branch2`, while the real issue was that `branch3=1` trapped carriers on the `p23` route, preventing vehicles from returning to transport `p2`.

**Root Cause**  
Local metric signals alone are insufficient. The model needs global constraints from the logistics topology (factories, products, branches, and transport edges) plus carrier state.

**Solution**  
Use a hybrid “rules-first + LLM-summary” pipeline:
- Convert `jaamsim_config` into a structured dependency graph (produce/assemble/logistics edges/branch mapping).
- Collect deterministic facts first (branch values, required assembly inputs, queue metrics, car-park location).
- Distinguish true material shortage vs. transport-coverage shortage under topology constraints.
- Use LLM only for concise RCA narrative and action explanation.
- Keep Human-in-the-loop confirmation before applying branch changes.

**Outcome**  
The workflow moves from symptom-based guesses to explainable causal reasoning, reducing misclassification and generating more actionable branch decisions.

### 11.3 Real-Time Rendering Pitfalls: Data Freshness + Embedded Runtime Throttling

**Problem**  
The digital twin sometimes appeared frozen or laggy, even though backend services were running. Two issues overlapped:
- **Data freshness mismatch**: simulation state was published at a lower cadence (for example, every 0.5s), while the frontend attempted higher-frequency rendering.
- **Embedded runtime throttling**: when the twin was hosted inside an iframe, browser scheduling behavior could throttle animation/update loops under specific focus/visibility conditions.

**Why it was misleading**  
This looked like a pure rendering-performance problem, but the root cause was a combination of update cadence mismatch and runtime scheduling constraints.

**Mitigation implemented in this project**
- Matched interpolation delay to source publish cadence.
- Increased controlled extrapolation window to bridge short data gaps.
- Switched to stable frame limiting for predictable refresh behavior.
- Added in-panel diagnostics (packet count, last-packet age) to distinguish “no new data” from “render loop issue”.

**Outcome**  
The twin behavior became smoother and, more importantly, diagnosable. The team can now separate upstream data stalls from frontend rendering issues during troubleshooting.
