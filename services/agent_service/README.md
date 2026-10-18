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
当前图尚未接入数据库 Checkpoint、执行租约和持久化事件。

## 数据库

Agent 表使用独立 PostgreSQL 数据库。设置 `AGENT_DATABASE_URL`，指向与 Commerce 的
`DATABASE_URL` 不同的数据库，然后从仓库根目录执行：

```bash
uv run alembic -c services/agent_service/alembic.ini upgrade head
uv run alembic -c services/agent_service/alembic.ini check
```

迁移链从 `a1f3c6d8e920` 开始，独立于 Commerce 迁移链。Agent 模型复用项目已有的
`Base`；Agent Alembic 只读取六张 Agent 表的 metadata。迁移不会创建 Commerce 表。

对一个已升级的独立测试库设置 `AGENT_TEST_DATABASE_URL` 后，可运行：

```bash
uv run pytest -q tests/integration/test_agent_orm.py
```

`customer_id` 是 Commerce 的外部标识，无跨库外键。写入 `AgentRun` 时还需在后续
Service 层校验触发消息的角色为 `USER`；写入 `CustomerMemory` 时应校验来源消息
所属会话的客户 ID 与记忆的客户 ID 一致。更新时间由 ORM 更新时维护；绕过 ORM 的
直接 SQL 更新须自行维护 `updated_at`。LangGraph 官方 PostgreSQL Checkpointer
后续需在 Agent 数据库单独初始化，它不属于本次六张业务表。
