# ResolveAI

## 启动 API

安装依赖：

```bash
uv sync
```

复制 `.env.example` 为 `.env`，并设置 `DATABASE_URL`。API 与 Alembic 使用同一连接配置。

启动开发服务器：

```bash
uv run uvicorn backend.app.main:app --reload
```

服务默认运行在 `http://127.0.0.1:8000`。访问 `/health` 检查应用状态，访问 `/docs` 查看交互式 API 文档。

运行测试：

```bash
uv run pytest
```

## 数据库迁移

先确认 `.env` 中的 `DATABASE_URL` 能连接 PostgreSQL。若数据库运行在 Docker 中，容器需要将 5432 端口发布到 URL 指定的宿主机端口。

修改 `backend/app/models/` 中的 ORM 模型后，生成并检查迁移脚本：

```bash
uv run alembic revision --autogenerate -m "describe schema change"
```

检查 `alembic/versions/` 中生成的 `upgrade()` 和 `downgrade()`，确认变更符合预期后执行：

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

首次迁移是 `1d3a8191a431_create_retail_schema.py`，创建 11 张零售业务表。Alembic 用 `alembic_version` 表记录数据库已执行到哪个版本；`check` 用于检查当前 ORM 是否还有未生成的结构变更。
