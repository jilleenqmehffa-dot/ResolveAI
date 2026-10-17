# Agent Service 数据库

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
