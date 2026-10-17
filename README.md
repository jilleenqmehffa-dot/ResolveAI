# ResolveAI

## 启动 API

安装依赖：

```bash
uv sync
```

复制 `.env.example` 为 `.env`，并设置 `DATABASE_URL`。API 与 Alembic 使用同一连接配置。

启动开发服务器：

```bash
uv run uvicorn services.commerce_service.app.main:app --reload
```

服务默认运行在 `http://127.0.0.1:8000`。访问 `/health` 检查应用状态，访问 `/docs` 查看交互式 API 文档。

当前零售 API 路由：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/products`、`/products/{product_id}`、`/items/{item_id}` | 查询商品和规格 |
| GET | `/customers/{user_id}`、`/customers/{user_id}/payment-methods`、`/customers/{user_id}/orders` | 查询用户、付款方式和订单列表 |
| GET | `/orders/{order_id}?user_id=...` | 查询指定用户的订单详情 |
| POST | `/orders/{order_id}/items/modify` | 修改待处理订单商品 |
| POST | `/orders/{order_id}/exchange` | 申请已送达订单换货 |
| POST | `/orders/{order_id}/return` | 申请已送达订单退货 |

修改和换货请求体包含 `user_id`、`item_ids`、`new_item_ids`、`payment_method_id`；退货请求体不需要 `new_item_ids`。业务错误分别返回 404、409 或 422。当前 API 用请求中的 `user_id` 校验订单和付款方式归属，尚未接入登录认证。

运行测试：

```bash
uv run pytest
```

## 数据库迁移

先确认 `.env` 中的 `DATABASE_URL` 能连接 PostgreSQL。若数据库运行在 Docker 中，容器需要将 5432 端口发布到 URL 指定的宿主机端口。

修改 `services/commerce_service/app/models/` 中的 ORM 模型后，生成并检查迁移脚本：

```bash
uv run alembic revision --autogenerate -m "describe schema change"
```

检查 `alembic/versions/` 中生成的 `upgrade()` 和 `downgrade()`，确认变更符合预期后执行：

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

首次迁移是 `1d3a8191a431_create_retail_schema.py`。最新迁移 `8c2f0a4d91b7_match_benchmark_retail_csv.py` 将零售业务结构调整为可容纳 `benchmark/datasets/db.json` 的 12 张关系表。该迁移负责转换数据库中已有的旧表数据，不会读取 JSON 文件。Alembic 用 `alembic_version` 表记录数据库已执行到哪个版本；`check` 用于检查当前 ORM 是否还有未生成的结构变更。

## Benchmark 零售数据

实际业务数据位于 `benchmark/datasets/db.json`，顶层包含 `products`、`users`、`orders`。JSON 中的嵌套内容对应以下数据表：

| JSON 内容 | 对应数据表 |
| --- | --- |
| 商品及规格 | `products`、`product_variants` |
| 用户、付款方式及订单列表 | `users`、`user_payment_methods`、`user_order_links` |
| 订单及明细 | `orders`、`order_items`、`order_fulfillment_tracking`、`order_fulfillment_items`、`order_payments`、`order_exchange_items`、`order_return_items` |

地址字段展开到用户和订单表，商品选项保存为 `options_json`，列表顺序保存为 `position`。先执行数据库迁移，再从仓库根目录运行导入 runner：

```bash
uv run alembic upgrade head
uv run python -m benchmark.runners.import_retail_json
```

runner 默认读取 `benchmark/datasets/db.json`；也可在命令后传入其他 JSON 文件路径。它在一次事务中导入全部数据，重复导入相同数据时跳过；如果表中已有不同数据，会报错并保留原数据。运行 `uv run pytest tests/integration/test_benchmark_schema.py` 可检查实际 JSON 的结构和导入行为。

`tasks.json` 和 `split_tasks.json` 是评测任务定义，不属于零售业务表。
