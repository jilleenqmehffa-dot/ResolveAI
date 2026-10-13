# ResolveAI V1 开发 SPEC

## V1 目标

建立 ResolveAI 的基础业务后端，参考 Bench 已有的数据结构完成数据库模型迁移、数据访问层和基础业务 API，为后续 Agent 模块提供业务数据底座。

**范围约束：** V1 只迁移所需的 Bench 数据库结构，不导入完整 Bench 测试集；暂不开发 Agent、RAG、Celery、RabbitMQ、Redis、故障注入或前端。

## Module 0：项目初始化

- 初始化 Python 项目与 uv 虚拟环境
- 安装正式依赖与开发依赖
- 建立基础项目目录结构
- 初始化 FastAPI 应用
- 配置 PostgreSQL Docker 容器
- 配置环境变量、日志及 `.env.example`

## Module 1：Bench 数据模型迁移

- 分析 Bench 数据库 Schema
- 确定 V1 所需的业务实体及关系
- 使用 SQLAlchemy 2.x 构建 ORM Models
- 建立主键、外键、索引及约束
- 配置 Alembic 数据库迁移
- 验证迁移后数据库结构完整性

## Module 2：数据访问层

- 实现 Repository 层
- 实现数据库 Session 管理
- 实现基础业务 Service
- 实现事务管理
- 统一数据库异常处理

## Module 3：FastAPI 业务接口

- 实现 Customer API
- 实现 Order API
- 实现 Conversation API
- 实现 Message API
- 配置 Pydantic 请求及响应 Schema
- 实现统一 API 异常处理

> 具体接口以 Module 1 确认的 Bench 实际数据模型为准。

## Module 4：基础测试与验收

- 编写数据库模型测试
- 编写 Repository / Service 测试
- 编写 FastAPI 接口测试
- 使用少量独立构造的测试数据验证业务链路
- 验证从空数据库执行 Alembic 迁移并启动 API
- 完善 README 及项目启动说明

## V1 完成标准

- PostgreSQL 能通过 Docker 启动。
- Alembic 能从空数据库创建所需业务表。
- Service 能通过 Repository 正常访问业务数据。
- FastAPI 基础接口可用，核心测试通过。
- 无须运行 Agent，也无须导入完整 Bench 测试集。
