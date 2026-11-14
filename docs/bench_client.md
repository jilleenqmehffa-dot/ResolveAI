# 统一测评入口

`evals.runner` 是统一测评 CLI，使用 `--mode` 选择 Agent 任务测评、
并发测试或网络故障测试。`scenarios/` 定义数据、参数和前置条件；
`executors/` 执行测试并返回原始观察；`evaluators/` 根据观察评分；
`runner.py` 负责选择场景、调度、写出结果和运行摘要。
`benchmark_client.py` 和 `load_client.py` 是 executor 使用的内部客户端。
运行前启动 Agent API、Worker、Commerce、RabbitMQ，并导入零售样例数据。
配置 `AGENT_INTERNAL_TOKEN`；真实模型还需要 `DEEPSEEK_API_KEY`，
Agent 任务评分还需要可读取 trace 的 `LANGSMITH_API_KEY`。

## Agent 任务测评

从仓库根目录运行：

```bash
.venv/bin/python -m evals.runner --mode agent-task --task-id 12
```

在不调用 DeepSeek 的情况下验证完整任务链路：

```bash
.venv/bin/python -m evals.runner --mode agent-task --task-id 12 --llm-backend mock
```

`--llm-backend` 可选 `mock` 或 `deepseek`（默认）。该选项随 `/chat` 请求保存到
Agent 执行记录，Worker 据此选择模型。`mock` 固定调用一次只读商品查询后
回复固定文字，因此业务正确率通常很低；它用于检查执行、网络故障和测评链路。
完整评分仍需 LangSmith trace。

`--task-id` 只测指定 ID 的一题，即使该题不在配置的 `split` 中也能运行。
启动器必须明确指定 `--task-id` 或 `--all`，避免误跑整批任务。
批量测评时运行：

```bash
.venv/bin/python -m evals.runner --mode agent-task --all
```

Agent 场景配置位于 `evals/scenarios/agent_task/config.yaml`，指定
`tasks.json`、`split_tasks.json` 的 `test` 划分、业务数据快照、模型、
超时、LangSmith 项目和可选的逐题重建设置。因此 `--all` 会测评测试集 40 题。
`--config` 可覆盖当前模式的整个 YAML 配置；旧的 `evals/runner.example.yaml`
仍可作为自定义配置模板。
测评运行会额外写入 `evals/runs/<run_id>.json` 摘要，记录数据文件哈希、评分器哈希和分数。
需要每题使用相同业务初始状态时，按[数据中台说明](data_platform.md)配置独立测试库并使用 `--rebuild-each-task`。
`--tasks` 可覆盖配置，直接测评指定 JSON 数组中的全部任务，此时不应用配置的划分。
Bench 任务字段从
`user_scenario.instructions` 映射为客户与消息；自定义任务也可直接提供
`id`、`customer_id`、`message`、`evaluation_criteria`。Bench 身份解析使用
配置的 `database` JSON。运行前需启动 Agent、Commerce，导入基准数据，并配置
`.env` 的 `AGENT_INTERNAL_TOKEN` 和可读取 trace 的 LangSmith Key。

Agent 任务调用链为：场景加载题目 → `executors/agent_task.py` 采集执行前业务状态、
提交 Agent 请求、等待终态、采集执行后状态和 trace →
`evaluators/agent_task.py` 使用两个 Normalizer 及现有的
`TaskSuccessEvaluator`、`TrajectoryEvaluator` 评分。executor 返回的原始对象
只在运行中传递；`--output` 保存评分后的结构化结果。`concurrency` 默认 1；
对共享同一业务库且会写入的任务，保持 1 并从一致的初始数据运行，以免题目之间
相互影响。启动器按输入顺序写出 JSON 数组；每项包含 `task_id`、`customer_id`、
`execution_id`、执行 `status`、`scores`、`reasons`、`error`，成功评分时另有
`details` 保存两个评分器的原始结构化结果。单题失败仍保留对应位置的错误项。

`evals/clients/benchmark_client.py` 从 `evals/scenarios/agent_task/datasets/tasks.json` 读取场景、从
`evals/scenarios/agent_task/datasets/split_tasks.json` 选择任务，并用 `evals/scenarios/agent_task/datasets/db.json` 将场景中的邮箱、
用户 ID、订单号或姓名加邮编解析为 Commerce 客户 ID。发送给 Agent 的消息只包含
`known_info` 和 `reason_for_call`；评测标准中的预期动作不会进入请求。

## 并发与网络故障

先预览要执行的任务，不发请求：

```bash
.venv/bin/python -m evals.runner --mode concurrency --all --limit 3 --concurrency 3 --dry-run
```

再用少量任务验证并发执行：

```bash
.venv/bin/python -m evals.runner --mode concurrency --all --limit 3 --concurrency 3
```

并发场景配置在 `evals/scenarios/concurrency/config.yaml`，默认使用 mock 模型、
`mock_mode: fast`、并发数 10、每题执行一次，并指定任务数据和请求超时。
用 `--mock-mode latency` 可让 MockLLM 的每次调用随机等待 3～6 秒；一次正常任务
通常调用模型两次（发出工具调用、收到工具结果后回复），因此人为等待合计通常为
6～12 秒。`--mock-mode fast` 不添加人为延迟。CLI 参数优先于场景配置，例如：

```bash
.venv/bin/python -m evals.runner --mode concurrency --all --limit 3 --concurrency 3 --llm-backend mock --mock-mode latency
```

Runner 将模式传给 Executor，后者通过 `/chat` 发送给 Agent API；API 将模式保存到
execution，Celery Worker 从数据库读取并创建对应配置的 MockLLM。修改代码后需重建
Agent API 和 Worker 容器并执行 Agent 数据库迁移。
命令行参数可临时覆盖其他场景参数。
去掉 `--limit` 可运行测试集 40 题。`--repeat 2` 会让每题重复两次。
`executors/concurrency.py` 按场景参数并行调用 Agent API，等待终态，
收集每次提交的状态和耗时；此模式不计算任务正确率。
共享业务库中的写操作可能影响其他题；正确率评估应使用 `agent-task` 串行模式和
可重建测试库。

网络故障模式先设置 Toxiproxy，再运行任务，并在退出时恢复代理：

```bash
.venv/bin/python -m evals.runner --mode network-fault --task-id 12
```

网络场景配置在 `evals/scenarios/network_fault/config.yaml`，默认注入 1000 毫秒
延迟、并发数 1，并指定 Toxiproxy 管理地址。`--fault` 可覆盖为 `latency`、
`timeout`、`disconnect`、`down`；`--fault-ms` 和 `--toxiproxy-api` 也可覆盖。
mock 模型固定调用一次
只读商品查询，可验证 Agent→Commerce 的故障链路而不产生模型 token。
`executors/network_fault.py` 自行设置故障、逐次提交请求并等待终态，
收集每次请求的状态、耗时、故障类型和持续时间，结束时恢复代理；
此模式不计算任务正确率。

`load_client.py` 负责为每次请求创建独立会话、生成幂等键及轮询终态；
`--repeat` 的每次重复都有独立会话。场景模块从各自 YAML
读取任务、划分、服务 URL 和超时配置；`--dry-run` 会显示任务 ID、模型、并发、
故障设置和所需服务及环境变量，不发送请求。`--task-id` 选单题，
`--all` 选配置中的划分；`--limit` 限制题数。三种模式的结果都是 JSON 数组；
省略 `--output` 时，自动写入 `evals/reports/agent-task/`、
`evals/reports/concurrency/` 或 `evals/reports/network-fault/`，文件名由 UTC 时间和
随机 ID 组成，避免覆盖旧结果。显式 `--output` 仍可指定其他位置。
`evals/runs/` 另外保存包含模式、模型和故障场景的运行摘要。

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

读取使用 `LANGSMITH_API_KEY`，该 Key 需要具有项目和 run 读取权限。
仅有 trace 写入权限的 Key 无法读取。
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
