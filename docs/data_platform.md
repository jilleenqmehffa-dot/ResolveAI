# ResolveAI 最小数据中台

本模块为零售测评提供数据目录、固定版本、质量检查、可重建的测试库、测评运行摘要和内部只读 API。Commerce 仍拥有商品、用户和订单的写入规则；Agent 仍拥有会话与执行记录。数据中台不跨库修改这些业务记录。

## 数据目录与版本

`data_platform/catalog.json` 列出数据来源、负责人、分类、敏感字段和使用方。`data_platform/manifest.json` 固定零售数据、任务和划分文件的 SHA-256 值。当前版本为 `retail-bench-v1`；版本名由团队维护，文件内容必须与清单一致。

```bash
.venv/bin/python -m data_platform.cli validate
```

检查商品、用户、订单与支付方式的引用、金额、任务 ID 和训练/测试划分。更改源文件后，先运行质量检查，再用新版本名更新清单，并将源文件和清单一起提交：

```bash
.venv/bin/python -m data_platform.cli pin --version retail-bench-v2
.venv/bin/python -m data_platform.cli validate
```

`pin` 不修改业务数据库。历史版本依靠 Git 提交保存；重跑历史版本时先检出对应提交。

## 接入与重建

对一个已迁移、空的 Commerce 数据库，验证后导入固定版本：

```bash
.venv/bin/python -m data_platform.cli ingest --database-url 'postgresql+psycopg://USER:PASSWORD@HOST:5432/resolveai_eval'
```

已有相同数据时跳过；数据不同则拒绝混入。需要恢复测试数据时使用独立的测评数据库，先停掉访问该库的 Agent 任务：
成功导入及跳过导入都会在 `evals/ingestions/` 留下包含时间、目标数据库名、数据版本和各表数量的收据；不记录数据库密码。
Compose 的 `commerce-seed` 将收据写到同一目录；非 1000 用户的宿主机可在 `.env` 中设置 `LOCAL_UID`、`LOCAL_GID`，以便容器写入该目录。

```bash
.venv/bin/python -m data_platform.cli rebuild --database-url 'postgresql+psycopg://USER:PASSWORD@HOST:5432/resolveai_eval' --confirm-database resolveai_eval
.venv/bin/python -m data_platform.cli rebuild --database-url 'postgresql+psycopg://USER:PASSWORD@HOST:5432/resolveai_eval' --confirm-database resolveai_eval --apply
```

第一条只预览；第二条在单个数据库事务内删除该 Commerce 库中的所有业务表记录，再导入固定快照。命令要求数据库名称精确匹配，避免误操作。不要将 Agent 执行库或生产 Commerce 库用作测评重建目标。表结构需先通过 Alembic 迁移到当前版本。

测评启动器可在每题开始前自动恢复快照；此模式要求 `concurrency: 1`，并且必须使用清单中的任务与零售数据：

```bash
export DATA_PLATFORM_TEST_DATABASE_URL='postgresql+psycopg://USER:PASSWORD@HOST:5432/resolveai_eval'
.venv/bin/python -m evals.runner --config evals/runner.example.yaml --all --output /tmp/resolveai-results.json --rebuild-each-task --confirm-database resolveai_eval
```

Agent 和 Commerce 必须连接同一个测试数据库。否则重建的数据不会被测评请求使用。每题使用独立 Agent 会话；同一业务库的并行测评不支持自动重建。

## 测评运行记录与查询

每次 `evals.runner` 运行后，`evals/runs/<run_id>.json` 保存不含客户详情的摘要：任务 ID、数据文件哈希、数据集版本、Git 修订号、评分器哈希、Agent 版本、执行状态数量和均分。设置 `AGENT_VERSION` 可以记录部署的 Agent 版本；未设置时该字段为空。原始逐题结果仍由 `--output` 写入指定文件。`evals/runs/` 默认不提交 Git；正式部署应将其放在有备份的持久存储上，并设置 `DATA_PLATFORM_RUNS_DIR` 指向该目录。

在本机启动只读服务：

```bash
.venv/bin/uvicorn data_platform.api:app --host 127.0.0.1 --port 8002
```

或使用 Compose 启动 `data-api`，它只监听宿主机 `127.0.0.1:8002`，读取仓库的 `evals/runs/`。除 `/health` 外，所有接口要求 `X-Agent-Token`，值取自 `AGENT_INTERNAL_TOKEN`：

| 接口 | 内容 |
| --- | --- |
| `GET /datasets` | 数据目录、当前版本、文件校验值 |
| `GET /runs` | 所有测评摘要 |
| `GET /ingestions` | 导入和重建收据 |
| `GET /runs/{run_id}` | 单次测评摘要 |
| `GET /runs/compare?left=...&right=...` | 两次均分差异、数据与任务是否相同、是否都逐题重建 |

API 不提供原始客户、地址或付款资料。比较结果中的 `same_dataset` 只说明输入文件和任务 ID 一致；只有 `both_isolated` 为真时，两个运行都记录了逐题重建。

## 范围

当前接入来源是版本化 JSON 和测评运行摘要；尚未持续同步线上 Commerce、Agent 执行数据库，也没有跨租户数据权限或报表界面。需要这些能力时，可在现有目录和质量契约上增加受控接入流程，避免把线上客户数据直接混入可重建的测评库。
