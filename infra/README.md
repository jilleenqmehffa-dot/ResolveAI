# 本地基础设施

从仓库根目录运行以下命令。先复制 `.env.example` 为 `.env`，设置有效的
`AGENT_INTERNAL_TOKEN`；运行真实 Agent 任务还需要 `DEEPSEEK_API_KEY`。

```bash
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
docker compose --env-file .env -f infra/docker-compose.yml --profile monitoring up -d
```

第一条启动业务服务、两个逻辑数据库、RabbitMQ、Worker、beat、Toxiproxy 和 LLM HTTPS 转发层。
第二条启动 Flower、Prometheus、Blackbox Exporter、cAdvisor、Docker 容器元数据导出器与 Grafana。监控界面仅绑定
宿主机回环地址：Flower `http://127.0.0.1:5555`、Prometheus
`http://127.0.0.1:9090`、Grafana `http://127.0.0.1:3000`。若宿主机 3000
端口被占用，可在 `.env` 中设置 `GRAFANA_PORT=3001` 等空闲端口。
Flower 默认账号为 `admin` / `resolveai`，Grafana 也是；可通过 `.env` 中的
`FLOWER_USER`、`FLOWER_PASSWORD`、`GRAFANA_USER`、`GRAFANA_PASSWORD` 修改。
数据库和 RabbitMQ 的密码分别由 `POSTGRES_PASSWORD`、`RABBITMQ_PASSWORD` 设置。
这些本地默认密码仅适合可信开发机；自定义密码应使用 URL 安全字符。

## Locust 可视化压测

先按上文启动业务服务并导入零售样例数据，再从仓库根目录启动可选的 Locust 服务：

```bash
docker compose --env-file .env -f infra/docker-compose.yml --profile load up -d locust
```

打开 `http://127.0.0.1:8089`，在界面中设置虚拟用户数、每秒启动用户数和运行时长，
然后开始测试。容器通过 Compose 网络访问 `agent-api:8000`，Web UI 只绑定宿主机
回环地址。若 8089 已被占用，在 `.env` 中设置 `LOCUST_PORT`。

容器默认从 `test` 划分随机选题，使用 `mock` 模型和 `fast` 模式；每次迭代创建
独立会话、提交任务并等待执行终态。界面显示创建会话、提交、轮询和完整执行流程
的请求量、失败率与耗时。可在 `.env` 中设置 `LOCUST_SPLIT`、
`LOCUST_LLM_BACKEND`、`LOCUST_MOCK_MODE`、`LOCUST_REQUEST_TIMEOUT`、
`LOCUST_POLL_INTERVAL`、`LOCUST_WAIT_TIMEOUT`；修改后重新创建容器。
使用真实模型时需要为 Agent Worker 配置有效的 `DEEPSEEK_API_KEY`。
结束后运行：

```bash
docker compose --env-file .env -f infra/docker-compose.yml --profile load stop locust
```

命令行的 `evals.runner --mode concurrency` 仍用于固定任务量的测评，结果写入
`evals/reports/concurrency/`；加 `--web-ui` 可在 `http://127.0.0.1:8090` 查看
同一次 runner 运行的 Locust 统计，详见[统一测评入口](../docs/bench_client.md#并发与网络故障)。
Docker 中的 `8089` Web UI 是独立的交互式持续压测服务，不显示 runner 的结果。

## 网络故障

执行带网络故障的 Agent 任务使用[统一测评入口](../docs/bench_client.md)；
以下命令用于单独检查或手动控制 Toxiproxy。

Compose 默认直连原服务。仅在故障测试时，在 `.env` 中按目标设置下表对应的
`COMPOSE_*` 变量，再重新创建使用该连接的容器。客户端使用的 `.env` 中
`DATABASE_URL`、`AGENT_DATABASE_URL` 等宿主机地址与 Compose 容器地址分开配置。

| 目标 | Compose 变量 | 代理地址 | 默认直连地址 |
| --- | --- | --- | --- |
| Commerce 工具 | `COMPOSE_COMMERCE_BASE_URL` | `http://toxiproxy:18080` | `http://commerce:8000` |
| Agent LLM | `COMPOSE_DEEPSEEK_BASE_URL` | `http://toxiproxy:18081` | `https://api.deepseek.com` |
| RabbitMQ | `COMPOSE_RABBITMQ_URL` | `amqp://resolveai:<密码>@toxiproxy:15672//` | `rabbitmq:5672` |
| PostgreSQL Commerce 库 | `COMPOSE_DATABASE_URL` | `postgresql+psycopg://resolveai:<密码>@toxiproxy:15432/resolveai` | `postgres:5432` |
| PostgreSQL Agent 库 | `COMPOSE_AGENT_DATABASE_URL` | `postgresql+psycopg://resolveai:<密码>@toxiproxy:15432/resolveai_agent` | `postgres:5432` |
| 仅 Worker 的 Agent 库 | `COMPOSE_WORKER_AGENT_DATABASE_URL` | `postgresql+psycopg://resolveai:<密码>@toxiproxy:15432/resolveai_agent` | 同 Agent 库 |

LLM 代理先转发到容器内的 `llm-gateway`，由它校验 DeepSeek 的 HTTPS 证书并连接
`api.deepseek.com`；这样 Agent 仍可将 HTTP 地址交给 Toxiproxy。测试进程若在宿主机
调用模拟用户 LLM，可设置 `USER_SIMULATOR_BASE_URL=http://127.0.0.1:18081`。
PostgreSQL 两个逻辑库共用一个代理端口，故障会同时影响所有经该端口连接的服务。
RabbitMQ 代理端口 `15672` 在这里是 AMQP TCP，不是管理页面。

例如只测试 Commerce 故障，在 `.env` 设置 `COMPOSE_COMMERCE_BASE_URL=http://toxiproxy:18080`，
重新创建 `agent-api` 和 `agent-worker`，然后运行：

```bash
.venv/bin/python -m evals.runner --mode network-fault --task-id 12 --fault-target commerce --fault timeout --fault-ms 12000
```

上例只检查故障下的提交与终态。若要检查 Agent 工具重试、LangSmith 轨迹和最终
业务结果，使用 `--mode agent-task` 并传入相同的故障参数；该模式会采集 Commerce
前后快照并评分，详细命令见[统一测评入口](../docs/bench_client.md#agent-任务测评)。

`--fault-target` 还可选 `llm`、`rabbitmq`、`postgres` 和 `client-api`。选择目标时必须
先把对应调用方的地址切到代理；mock 模型不会发出 LLM 网络请求。切换数据库或 RabbitMQ
地址后需重新创建相应的迁移、API、Worker、beat 或 Commerce 容器。恢复日常运行时
删除相应 `COMPOSE_*` 覆盖值并重新创建容器。

Client→Agent API 入口由测试客户端控制。要测试它，将场景 YAML 的
`agent_base_url` 改为 `http://127.0.0.1:18082`，并选 `--fault-target client-api`；
服务端配置无需改动。所有代理端口仅绑定宿主机回环地址；Toxiproxy 管理 API 为
`127.0.0.1:8474`。

五种命名恢复场景使用 `--fault-case`，说明和命令见[统一测评入口](../docs/bench_client.md#五种恢复场景)。
`client-disconnect` 自动让测试客户端通过 `agent_proxy_url` 连接（默认
`http://127.0.0.1:18082`）；其他场景沿用 `agent_base_url`。`postgres-disconnect`
建议只设置 `COMPOSE_WORKER_AGENT_DATABASE_URL`，让 API、beat 和测试器继续直连
Agent 库，便于观察 Worker 断连后状态。`rabbitmq-disconnect` 要把 API、Worker 和
beat 的 RabbitMQ 连接都切到代理，再重建这些容器。`worker-crash` 会强停并重启
`agent-worker`，只在隔离的测试环境执行。

```bash
python3 infra/toxiproxy/control.py status
python3 infra/toxiproxy/control.py latency --ms 1000
python3 infra/toxiproxy/control.py timeout --ms 12000
python3 infra/toxiproxy/control.py disconnect
python3 infra/toxiproxy/control.py down
python3 infra/toxiproxy/control.py reset
python3 infra/toxiproxy/control.py status --target rabbitmq
```

每次设置场景先清除上一个场景。`latency` 延迟响应，`timeout` 丢弃响应数据直到
指定毫秒数后断开，`disconnect` 立即重置连接，`down` 停止代理监听；`reset`
恢复正常。以上命令默认操作 Commerce 代理，`--target` 可选择其他入口；一次只运行
一个故障场景，重置操作会清除 Toxiproxy 中所有 toxic。注入故障时 Agent 的
`/ready` 可能变为不健康，这是预期现象；使用 `reset` 恢复后会重新健康。

## 监控范围

Prometheus 收集 Flower 的 Worker 与任务事件、RabbitMQ 队列指标、Toxiproxy
指标及 cAdvisor 的 Docker 容器 CPU、内存等资源指标，并通过 Blackbox Exporter 探测 Agent `/ready`、Commerce `/health` 及
Agent→Commerce 代理链路。Grafana 自动配置 Prometheus 数据源和
`ResolveAI Overview` 仪表盘。Flower 依赖 Worker 的 Celery 事件，Worker 在
Compose 中使用 `-E` 启动。cAdvisor 的 `/metrics` 仅在 Compose 网络内供 Prometheus 抓取；
Docker 元数据导出器通过 Docker socket 提供容器 ID、名称和 Compose 服务名，供 Prometheus
按容器 ID 与 cAdvisor 指标关联。可在 Prometheus 的 Targets 页面检查 `cadvisor` 和
`docker_metadata` 任务。Grafana 的 `ResolveAI Overview` 包含按容器名称显示的内存用量面板。
当前监控覆盖服务可用性、任务事件、队列及容器资源；业务执行
状态分布和工具调用耗时尚未导出为 Prometheus 指标。
