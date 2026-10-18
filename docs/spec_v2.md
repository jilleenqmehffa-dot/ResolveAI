# ResolveAI V2 开发规格：Agent Service

## 1. 背景与目标

[架构说明](./architecture.md)依据 `docs/60ca69ff-446b-492f-9059-bcb2d1fbbede.png` 描述了双服务、异步执行和网络故障测试的目标形态。V1 已具备 Commerce Service 的零售数据、业务规则和基础 HTTP 路由；V2 聚焦新增 **Agent Service**，让 Benchmark 的零售任务能够经 Agent 决策、调用 Commerce 工具、记录可恢复的执行状态，并在并发与网络故障下可追踪地结束。

本规格是实施目标，不表示以下组件已经存在。V2 的交付范围包括 Agent Service、Agent 数据库迁移、RabbitMQ/Celery 执行通道、Commerce HTTP 工具适配、最小故障注入测试和运行文档。前端、语音、多租户、通用 RAG 平台及图片中可选的全部监控界面不作为 V2 完成条件。

## 2. 约束与设计原则

1. **业务所有权清晰**：Agent Service 不直接读写 Commerce 数据库；商品、订单和付款方式通过 Commerce HTTP API 访问。业务状态转换与金额结算仍在 Commerce Service 内完成。
2. **执行有持久身份**：每个请求拥有 `execution_id`、会话标识、客户标识、提交时间、状态、尝试次数和终态结果。API 重试不得创建重复执行。
3. **副作用可核对**：工具调用记录输入、业务操作键、结果和错误类别。修改订单、退换货等操作在结果未知时先核对业务状态；没有幂等保障时不得盲目重放。
4. **状态可恢复**：Worker 退出、消息重投或进程重启后，能根据数据库中的执行记录、事件与 LangGraph 检查点继续或安全终止。
5. **故障可观察**：网络超时、连接失败、业务拒绝和模型失败分别记录，客户端能查询最终状态及诊断事件。

## 3. 目标目录与职责

```text
services/agent_service/
├── app/
│   ├── api/             # chat、executions、health 路由
│   ├── agents/          # LangGraph 图、节点、提示词和 Tool 定义
│   ├── execution/       # 状态机、排队、重试、超时、检查点协调
│   ├── transport/       # Commerce HTTP Client、超时与错误分类
│   ├── messaging/       # RabbitMQ/Celery 发布与任务协议
│   ├── workers/         # Celery 任务入口
│   ├── maintenance/     # 超时扫描、异常执行恢复与数据清理
│   ├── models/          # Agent 持久化模型
│   ├── repositories/    # Agent 数据访问
│   ├── schemas/         # API 与任务载荷模型
│   ├── db/              # Agent 数据库连接与会话
│   └── core/            # 配置、日志、错误与请求追踪
├── alembic/             # Agent 数据库独立迁移链
├── alembic.ini
└── Dockerfile
```

`benchmark/clients/` 负责评测与负载客户端，`benchmark/adapters/` 负责任务格式映射，`benchmark/runners/` 负责任务批量提交，`benchmark/evaluators/` 负责结果评分。V2 只实现能跑通零售任务及最小并发/故障验收所需的部分；保留现有 `benchmark/datasets/` 与 JSON 导入 runner。

## 4. 外部接口契约

| 接口 | 输入 | 正常输出 | 关键规则 |
| --- | --- | --- | --- |
| `POST /chat` | `session_id`、`user_id`、`message`、`idempotency_key` | `202`，`execution_id` 与初始状态 | 在单个数据库事务中保存请求及待发布事件；同一客户与幂等键返回同一执行 |
| `GET /executions/{execution_id}` | 执行 ID | 状态、尝试次数、结果或错误摘要 | 只允许有权访问该会话的调用方读取；终态结果稳定 |
| `GET /executions/{execution_id}/events` | 执行 ID、可选游标 | 顺序事件列表 | 事件带递增序号，允许客户端断线后继续读取 |
| `POST /executions/{execution_id}/cancel` | 执行 ID | 当前或新的执行状态 | 排队任务可取消；运行中标记取消请求，由 Worker 在安全边界停止 |
| `GET /health`、`GET /ready` | 无 | 存活状态、依赖就绪状态 | 就绪检查覆盖 Agent DB、RabbitMQ、Commerce 连接 |

最小响应字段：`execution_id`、`session_id`、`status`、`created_at`、`updated_at`、`attempt_count`、`result`、`error_code`。HTTP 层对输入错误返回 422，对不存在或不可访问资源返回 404，对状态冲突返回 409；提交后异步执行失败通过执行状态与事件表示。认证机制需明确；当前 Commerce 路由只凭传入的 `user_id` 校验归属，V2 对外启用前必须建立可信客户身份与服务间鉴权。

## 5. Agent 决策与 Commerce Tool

LangGraph 图至少包含：加载会话上下文、规划下一步、调用工具、解释工具结果、生成回复和终态写回。提示词及工具版本写入 execution 元数据，便于复现评测。图运行时只通过定义好的工具访问商品服务，工具返回结构化结果，不把 HTTP 异常直接混入模型上下文。

首批 Commerce Tool 对应现有路由：商品列表/详情、规格详情、客户及付款方式、客户订单与订单详情、待处理订单商品修改、已送达订单换货与退货。`transport/` 统一设置连接与读取超时、请求追踪 ID、错误分类和测试环境代理地址。Toxiproxy 只放在 Agent 到 Commerce 的 HTTP 链路上，不代理 Agent 数据库或 RabbitMQ。

业务写工具需要 `operation_id` 或等效幂等键。Commerce API 尚无这项契约时，V2 可以先实现只读工具的自动重试；写工具遇到超时或断连时应查询订单状态与操作记录并将无法判断的执行标记为待核对，直到 Commerce 端提供可验证的幂等操作。不得因 Celery 重投重复提交换货、退货或付款。

## 6. 执行状态与异步流程

状态集合：`created` → `queued` → `running` → `succeeded`；可恢复故障允许 `running` → `retry_wait` → `queued`；不可恢复故障进入 `failed`，取消进入 `cancelled`，超过总时限进入 `timed_out`。副作用结果无法判断时进入 `needs_review`，等待人工或专门的核对流程处理。`succeeded`、`failed`、`cancelled`、`timed_out` 和 `needs_review` 都是自动执行的终态，不能被旧任务消息覆盖；`needs_review` 仅能由显式核对操作重新启动。

1. API 持久化 execution、用户消息和待发布事件，提交事务后由发布器向 RabbitMQ 发送任务。通过事务性 outbox 或等效机制避免“数据库已有执行但队列没有任务”的空窗。
2. Worker 消费任务，使用 `execution_id` 抢占执行租约；重复投递先检查状态与租约。Execution Manager 将状态推进到 `running`，并写入开始事件。
3. Worker 运行 LangGraph；每次工具调用前后记录 invocation，必要时写入检查点。成功时原子写入助手消息、结果和 `succeeded` 事件。
4. 网络临时故障按配置重试，采用指数退避与随机抖动，并限制单次工具超时、最大尝试次数和执行总时限。业务 4xx、数据校验失败和明确拒绝不自动重试。
5. Worker 崩溃或租约过期由维护任务扫描；检查点可用且副作用安全时恢复，否则结束为可诊断的失败或待核对状态。状态更新采用版本号或条件更新，防止旧尝试覆盖新尝试。

## 7. Agent 数据模型

目标使用独立于 Commerce 业务库的 Agent 数据库；若复用同一 PostgreSQL 实例，也应采用独立逻辑数据库、连接账号和迁移链。最低持久化实体如下：

| 表 | 最低字段与约束 | 用途 |
| --- | --- | --- |
| `messages` | `message_id`、`session_id`、`execution_id`、`role`、`content`、`created_at` | 会话输入输出；按会话和时间索引 |
| `agent_executions` | `execution_id`、`session_id`、`user_id`、`status`、`attempt_count`、`version`、`created_at`、`updated_at`、`result`、`error_code`、`idempotency_key` | 执行主记录；`user_id + idempotency_key` 唯一 |
| `execution_events` | `event_id`、`execution_id`、`sequence`、`event_type`、`payload`、`created_at` | 客户端可读取的有序生命周期事件；`execution_id + sequence` 唯一 |
| `tool_invocations` | `invocation_id`、`execution_id`、`tool_name`、`operation_id`、`attempt`、`request`、`response`、`status`、`started_at`、`finished_at` | 工具审计和副作用核对 |
| `checkpoints` | LangGraph 线程/检查点 ID、`execution_id`、状态快照、版本、创建时间 | 持久恢复；可使用 LangGraph PostgreSQL saver 的正式表结构 |
| `execution_logs` | `log_id`、`execution_id`、级别、错误类别、摘要、时间 | 运维诊断；敏感字段脱敏 |
| `outbox_messages` | `message_id`、`execution_id`、任务类型、载荷、发布状态、时间 | 保证创建执行与任务发布之间的可恢复交接 |

数据保留期限由配置指定。日志和 Tool 请求不得保存完整付款卡号或凭据；付款方式只保留业务 ID 和允许展示的掩码字段。`tool_invocations`、事件与日志使用 `execution_id` 建索引，便于定位单次任务。

## 8. 配置、部署与监测

- Agent API、Celery Worker、RabbitMQ、Commerce Service 和 PostgreSQL 作为独立进程/容器部署。Agent API 目标端口 8000，Commerce API 目标端口 8001；本地开发需调整当前 Commerce 默认 8000 的启动命令，避免冲突。
- 配置至少包含 Agent DB URL、RabbitMQ URL、Commerce Base URL、模型提供方配置、单次工具超时、执行总时限、最大重试次数、并发度和日志级别。密钥只通过环境或密钥服务注入。
- 核心指标：请求提交量、排队时间、运行时间、终态分布、重试次数、工具调用延迟、Commerce 错误分类、Worker 租约过期数。日志携带 `execution_id`、`session_id`、`tool_invocation_id` 与跨服务 trace ID。
- 图片中的 Flower、Grafana/Prometheus、Locust 和 pgAdmin 可逐步接入；V2 最小验收先要求结构化日志、核心指标导出和可重复的故障测试脚本。

## 9. 实施顺序与验收

| 阶段 | 交付物 | 验收检查 |
| --- | --- | --- |
| A. 数据与 API | Agent 模型迁移、会话/执行 API、错误模型 | 提交后可按 ID 查询；同一幂等键只创建一个 execution；权限校验生效 |
| B. Agent 与工具 | LangGraph 图、提示词版本、Commerce HTTP Client、只读与写工具 | Benchmark 的零售任务可调用真实 Commerce API；工具输入输出可追踪 |
| C. 异步与恢复 | RabbitMQ、Celery Worker、outbox、检查点、租约和重试 | Worker 重启及消息重投后没有重复执行结果；所有测试执行最终进入终态或明确待核对 |
| D. 故障与并发 | Toxiproxy 场景、负载客户端、事件查询、指标 | 至少覆盖延迟、连接中断和超时；并发提交时无丢任务、无重复业务副作用 |

自动化测试至少覆盖：状态机合法/非法转移、API 幂等、工具错误分类、Commerce 所属用户校验、数据库与队列交接、Worker 重投、检查点恢复、写操作结果未知、取消和超时。端到端评测使用 `benchmark/datasets/split_tasks.json` 的 `test` 划分（当前 40 条）记录任务完成率和动作正确率；`train` 划分用于开发，不用测试划分调提示词。首次评测建立基线，后续改动必须报告与基线的差异。

**V2 完成标准**：Agent API 可提交、查询和取消执行；零售任务通过 LangGraph 与 Commerce HTTP 工具运行；队列/Worker 运行及故障恢复可复现；执行和工具链路可审计；写操作在不确定结果下不会被无条件重放；部署和测试命令写入 README。
