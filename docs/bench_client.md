# Bench 并发客户端

`evals/clients/benchmark_client.py` 从 `evals/datasets/tasks.json` 读取场景、从
`evals/datasets/split_tasks.json` 选择任务，并用 `evals/datasets/db.json` 将场景中的邮箱、
用户 ID、订单号或姓名加邮编解析为 Commerce 客户 ID。发送给 Agent 的消息只包含
`known_info` 和 `reason_for_call`；评测标准中的预期动作不会进入请求。

`load_client.py` 为每条任务创建独立会话，再并发提交 `/chat`。每次运行使用新的批次
ID 生成幂等键；`--repeat` 会重复场景，但每次重复仍使用独立会话和幂等键。
默认提交 `test` 划分的全部 40 条任务，最大同时处理 10 条。提交成功只表示 Agent
接受任务；加 `--wait` 才会轮询最终状态。

从仓库根目录运行：

```bash
.venv/bin/python -m evals.clients.load_client --split test --limit 3 --dry-run
.venv/bin/python -m evals.clients.load_client --split test --concurrency 10 --wait --output /tmp/bench-results.jsonl
```

默认读取根目录 `.env` 中的 `AGENT_INTERNAL_TOKEN`，Agent API 地址为
`http://127.0.0.1:8000`。可使用 `--base-url`、`--env-file` 或 `--token` 覆盖。
`--split` 可选 `train`、`test`、`base`；`--limit` 限制任务数，`--repeat` 增加
负载，`--concurrency` 限制同时进行的请求。`--output` 将每条任务的会话 ID、执行
ID、状态和耗时写成 JSONL。

运行前先导入 Bench 零售数据。场景包含可能修改订单的请求，重复运行时应使用可重建的
测试数据库。该客户端统计提交和执行状态，不计算 Bench 动作正确率。

## Commerce 查询覆盖

Bench 的客户身份线索可由 Commerce 内部接口解析：
`GET /customers/lookup/by-email?email=...` 和
`GET /customers/lookup/by-name-zip?first_name=...&last_name=...&zip=...`。
两者都要求 `X-Agent-Token`，未找到时返回 404，只返回 `user_id`。
`GET /orders/{order_id}` 还返回 `fulfillments`，每个包裹包含
`tracking_id` 和 `item_ids` 列表，与 `db.json` 的结构一致；该接口继续按
`X-Customer-ID` 校验订单归属。

## 读取 Agent trace

`evals/adapter/trace_adapter.py` 按 Agent API 返回的 `execution_id` 查询 LangSmith。
Agent 图使用该 ID 作为 `thread_id`。适配器返回同一执行的所有根 trace（包括重试），
每条 trace 包含根节点及其子 run 的输入、输出、错误、时间、元数据和用量字段。
LangSmith 上传是异步的，评测器可用 `wait_for_traces` 等待记录出现：

```python
from evals.adapter.trace_adapter import TraceAdapter

traces = TraceAdapter().wait_for_traces(execution_id, timeout=30)
```

读取需要具有项目和 run 读取权限的 Key。可设置 `LANGSMITH_READ_API_KEY`；
未设置时适配器回退使用 `LANGSMITH_API_KEY`。仅有 trace 写入权限的 Key 无法读取。
项目名读取 `LANGSMITH_PROJECT`，默认从仓库根目录 `.env` 获取。
