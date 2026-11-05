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
`GET /customers/{user_id}` 返回原始 `users[*].orders` 顺序及支付方式；
`GET /customers/{user_id}/orders` 保持这个顺序，并通过 `order_position`
给出从 0 开始的序号。测试数据没有下单时间字段，不能据此生成真实时间戳；
涉及“最近订单”的任务使用原始订单列表中的最后一笔。

`evals/adapter/commerce_adapter.py` 从 Commerce HTTP API 抓取题目所需的业务状态，
供评测器与预期结果比较。它读取客户资料、支付方式及余额、该客户全部订单详情、
预期动作引用的商品和款式，以及身份查询结果。不存在的订单或商品记为 `None`；
认证或服务错误会直接报错。`capture_task` 只发送 GET 请求，不执行题目的预期修改动作。
适配器还提供与 Bench 写动作同名的显式方法，分别调用 Commerce 的订单商品修改、
换货、退货、取消订单、订单地址修改、订单付款方式修改和用户地址修改 API。
写请求携带 `X-Agent-Token` 与 `X-Customer-ID`；归属和业务规则由 Commerce 校验，
失败时保留 Commerce 返回的状态码及错误详情。

```python
from evals.adapter.commerce_adapter import CommerceAdapter

async with CommerceAdapter(customer_id) as adapter:
    before = await adapter.capture_task(task)
    # 等 Agent 运行结束后，再捕获一次用于比较。
    after = await adapter.capture_task(task)
```

`CommerceAdapter` 默认从根目录 `.env` 读取 `COMMERCE_BASE_URL` 和
`AGENT_INTERNAL_TOKEN`。快照由多个 HTTP 请求组成，不保证数据库事务级一致性；
评测时应在 Agent 结束后、下一题开始前读取，并使用可重置的测试数据。

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

## 规范化评测输入

`TraceNormalizer` 将 Agent 最终回复、工具调用、结果、Guard 决策以及每个
Run 的时间、父子关系、元数据、token 和费用整理为 `NormalizedTrace`。
`CommerceNormalizer` 将快照中的客户、订单、商品、支付方式和身份查找结果
整理为 `NormalizedCommerce`。规范化器不发请求，也不判定任务是否成功。

两边共用同一套业务字段映射：`status/order_status → order_status`（订单），
`status/case_status → case_status`（售后），`user_id/customer_id → customer_id`，
`id/order_id → order_id`（订单），`id/payment_method_id → payment_method_id`
（支付方式），`product_name/name → name`（商品项），
`tracking_id/tracking_ids → tracking_ids`，`zip_code/zip → zip`。
所有业务 ID 转为字符串，金额转为两位小数字符串。订单明细、支付流水、
履约、换货和退货列表逐项递归规范化；售后工具结果也使用统一字段。
未识别字段放入对应对象的 `_unmapped`，便于发现新增的接口字段。
工具调用保留 `raw_inputs/raw_outputs`，所有 Run 也保留原始输入和输出。

```python
from evals.normalizer.commerce_normalizer import CommerceNormalizer
from evals.normalizer.models import EvalRecord
from evals.normalizer.trace_normalizer import TraceNormalizer

record = EvalRecord(
    task_id=str(task["id"]),
    customer_id=customer_id,
    execution_id=execution_id,
    trace=TraceNormalizer().normalize(traces),
    commerce=CommerceNormalizer().normalize(after),
    commerce_before=CommerceNormalizer().normalize(before),
)
```

`EvalRecord` 会检查三个来源的任务、客户和执行 ID 是否一致。比较动作结果、
前后状态或自然语言要求的逻辑由后续 Evaluator 执行。

## Task success 评分

`evals/evaluation/task_success.py` 使用任务的预期写动作和前后 Commerce 快照
检查订单、用户资料及无关资源是否按预期变化。只有 `communicate_info` 非空时才检查
最终回复中的必需信息；金额按数值比较，文字按忽略大小写和标点的词序列比较。
`nl_assertions` 中仅描述换货、改地址等数据库动作的文字不计入回复分。
总分为 0 或 1：数据库检查通过，且需要时回复检查也通过，才记为 1。

```python
from evals.evaluation.task_success import TaskSuccessEvaluator

result = TaskSuccessEvaluator().evaluate(record, task)
print(result.score, result.db_success, result.nl_success)
```

评测要求 `record.commerce_before` 存在。当前规则检查核心业务结果；商品价格
结算后的支付流水不参与 task success 判分。

`evals/evaluation/trajectory.py` 将可用的 Bench 预期动作与符合预期结果的工具调用按名称和参数
匹配，合并同一工具调用 ID 的重试，并单列缺失动作、非预期写入和当前 Agent 不支持的
动作。轨迹分数按“匹配数 ÷（可评动作数 + 非预期写入数）”计算；不支持的动作
不进入分母，但保留在结果中。
对测试集里有意查询不存在资源的动作，`expected_outcome: "not_found"` 要求工具名、
参数和 `not_found`／HTTP 404 结果同时匹配；普通动作仍要求成功结果。当前标记
覆盖任务 2～4 的缺失商品查询，以及任务 46、47 中用户逐步纠正的错误订单号查询。
预期的未找到结果计入匹配，不列为异常失败；其他网络或业务错误仍不计分。
