# Agent Service

## DeepSeek 模型

在仓库根目录的 `.env` 中设置 `DEEPSEEK_API_KEY`（参考 `.env.example`），或设置同名环境变量。
可选的 `DEEPSEEK_MODEL` 默认为 `deepseek-flash`。模型由
`services.agent_service.app.agents.llm.create_deepseek_model()` 创建；创建时不会发起 API 请求，
调用模型时才需要有效的 Key。图节点可对返回的模型调用 `bind_tools(...)`。

## Agent 图

`create_agent_graph()` 按 `prepare_context → agent → tool_guard → tools/rejected → agent`
组装五个节点。无 Tool Call 的 Agent 回复直接结束。`tool_guard` 使用 Tool 的输入
Schema 校验参数；同一消息的多个 Tool Call 会整批拒绝。Commerce Client 只负责 HTTP。

```python
from langchain_core.messages import HumanMessage

from services.agent_service.app.agents.graph import create_agent_graph
from services.agent_service.app.transport.commer_client import CommerceClient

async with CommerceClient(commerce_base_url, authenticated_user_id) as client:
    graph = create_agent_graph(client)
    result = await graph.ainvoke({
        "messages": [HumanMessage(content=user_text)],
        "tool_call_count": 0,
        "last_error": None,
        "guard_result": None,
    })
```

`context_loader` 可在开始时加载此前的会话消息；调用方须按当前用户和会话限制其范围。
Worker 按会话内 `Message.seq` 读取本轮触发消息之前最近 10 轮已完成的问答；
`AgentRun.response_message_id` 将成功运行与客服答复关联，未完成的并发提问不进入历史。
Worker 为每个运行使用 PostgreSQL Checkpointer，并以运行 ID 作为 LangGraph thread ID。
只读 Commerce 工具在临时故障时受次数和时限约束重试；明确的业务错误不自动重试。
`mark_after_sales_refunded` 可把当前客户的退款类售后单结案为 `CLOSED`、`REFUNDED`；
Commerce Service 通过 Repository 和 Service 在事务内执行状态转换，重复请求返回同一结果。
新写接口要求 `X-Agent-Token` 和 `X-Customer-ID`；Agent Worker 使用配置中的
`AGENT_INTERNAL_TOKEN` 与已认证的客户 ID 发送这两个请求头。
这个操作不发起资金退款。写请求结果不明时，Worker 先重新查询售后单状态，再决定是否重试。
售后单状态通过 `list_after_sales_cases` 和 `get_after_sales_case` 实时读取；
`CLOSED` 表示流程结束，不单独证明退款已经支付。

## 异步运行

设置 `.env.example` 中的 Agent 数据库、RabbitMQ、Commerce 地址和内部 API Token。
Agent 数据库必须与 Commerce 数据库分开。迁移 Agent 表后，Worker 首次执行任务时
会初始化 LangGraph 的检查点表。以下命令分别在独立终端运行：

```bash
docker compose -f infra/agent-broker.compose.yml up -d
uv run --env-file .env uvicorn services.agent_service.app.main:app --host 127.0.0.1 --port 8000
uv run --env-file .env celery -A services.agent_service.app.workers.tasks:celery_app worker --loglevel=info
uv run --env-file .env celery -A services.agent_service.app.workers.tasks:celery_app beat --loglevel=info
```

Commerce Service 应运行在 `COMMERCE_BASE_URL` 指向的地址。`POST /conversations`
创建会话，`POST /chat` 提交消息并返回执行 ID，`GET /executions/{id}` 查询状态，
`POST /executions/{id}/cancel` 取消执行。业务接口要求 `X-Agent-Token` 和
`X-Customer-ID` 请求头；Token 仅供可信内部调用方使用，不能代替面向终端用户的认证。
同一会话的相同 `idempotency_key` 返回同一运行。API 发布任务失败时，已提交的
`QUEUED` 记录由 Celery beat 的周期扫描重新投递。Worker 用数据库租约避免重复运行，
过期租约由扫描器重新投递。中断的只读工具可重试；中断的退款结果写工具必须先核验状态。

运行状态包括 `PENDING`、`QUEUED`、`RUNNING`、`RETRY_WAIT`、`SUCCEEDED`、
`FAILED`、`CANCELLED` 和 `TIMED_OUT`。检查点保存在 Agent 数据库中；
`ToolExecution` 记录同一工具调用的尝试次数、结果和错误。

## 数据库

Agent 表使用独立 PostgreSQL 数据库。设置 `AGENT_DATABASE_URL`，指向与 Commerce 的
`DATABASE_URL` 不同的数据库，然后从仓库根目录执行：

```bash
uv run --env-file .env alembic -c services/agent_service/alembic.ini upgrade head
uv run --env-file .env alembic -c services/agent_service/alembic.ini check
```

迁移链从 `a1f3c6d8e920` 开始，独立于 Commerce 迁移链。Agent 模型复用项目已有的
`Base`；Agent Alembic 只读取六张 Agent 表的 metadata。迁移不会创建 Commerce 表。

对一个已升级的独立测试库设置 `AGENT_TEST_DATABASE_URL` 后，可运行：

```bash
uv run pytest -q tests/integration/test_agent_orm.py tests/integration/test_agent_execution.py
```

`customer_id` 是 Commerce 的外部标识，无跨库外键。提交运行时，Service 层会创建
`USER` 触发消息；写入 `CustomerMemory` 时仍须校验来源消息所属会话的客户 ID。
更新时间由 ORM 更新时维护；绕过 ORM 的直接 SQL 更新须自行维护 `updated_at`。
LangGraph 检查点表由 Checkpointer 管理，不属于 Agent Alembic 的六张业务表。
