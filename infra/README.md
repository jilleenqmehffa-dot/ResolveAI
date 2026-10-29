# 本地基础设施

从仓库根目录运行以下命令。先复制 `.env.example` 为 `.env`，设置有效的
`AGENT_INTERNAL_TOKEN`；运行真实 Agent 任务还需要 `DEEPSEEK_API_KEY`。

```bash
docker compose --env-file .env -f infra/docker-compose.yml up --build -d
docker compose --env-file .env -f infra/docker-compose.yml --profile monitoring up -d
```

第一条启动业务服务、两个逻辑数据库、RabbitMQ、Worker、beat 和 Toxiproxy。
第二条启动 Flower、Prometheus、Blackbox Exporter 与 Grafana。监控界面仅绑定
宿主机回环地址：Flower `http://127.0.0.1:5555`、Prometheus
`http://127.0.0.1:9090`、Grafana `http://127.0.0.1:3000`。若宿主机 3000
端口被占用，可在 `.env` 中设置 `GRAFANA_PORT=3001` 等空闲端口。
Flower 默认账号为 `admin` / `resolveai`，Grafana 也是；可通过 `.env` 中的
`FLOWER_USER`、`FLOWER_PASSWORD`、`GRAFANA_USER`、`GRAFANA_PASSWORD` 修改。
数据库和 RabbitMQ 的密码分别由 `POSTGRES_PASSWORD`、`RABBITMQ_PASSWORD` 设置。
这些本地默认密码仅适合可信开发机；自定义密码应使用 URL 安全字符。

## 网络故障

Agent 工具调用使用 `COMMERCE_BASE_URL=http://toxiproxy:18080`，代理唯一上游是
`commerce:8000`。PostgreSQL、RabbitMQ 和 Commerce 对客户端的访问不会经过代理。
未注入故障时代理直接转发。管理 API 只在宿主机的 `127.0.0.1:8474` 开放。

```bash
python3 infra/toxiproxy/control.py status
python3 infra/toxiproxy/control.py latency --ms 1000
python3 infra/toxiproxy/control.py timeout --ms 12000
python3 infra/toxiproxy/control.py disconnect
python3 infra/toxiproxy/control.py down
python3 infra/toxiproxy/control.py reset
```

每次设置场景先清除上一个场景。`latency` 延迟响应，`timeout` 丢弃响应数据直到
指定毫秒数后断开，`disconnect` 立即重置连接，`down` 停止代理监听；`reset`
恢复正常。故障只应用于实验中的 Agent→Commerce 请求。注入故障时 Agent 的
`/ready` 可能变为不健康，这是预期现象；使用 `reset` 恢复后会重新健康。

## 监控范围

Prometheus 收集 Flower 的 Worker 与任务事件、RabbitMQ 队列指标、Toxiproxy
指标，并通过 Blackbox Exporter 探测 Agent `/ready`、Commerce `/health` 及
Agent→Commerce 代理链路。Grafana 自动配置 Prometheus 数据源和
`ResolveAI Overview` 仪表盘。Flower 依赖 Worker 的 Celery 事件，Worker 在
Compose 中使用 `-E` 启动。当前监控覆盖服务可用性、任务事件及队列；业务执行
状态分布和工具调用耗时尚未导出为 Prometheus 指标。
