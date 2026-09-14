# 电商商品文案质检与优化 Agent

[简体中文](README.zh-CN.md) | [English](README.md)

界面支持中文 / English 切换，登录页、管理后台和质检工作台共用语言偏好。英文项目说明包含部署、架构、数据来源、测试及能力边界，供 GitHub 项目审阅。当前检测对象是中文商品文案；界面切换不改变原文、证据或检测结论。

![系统架构概览](docs/assets/architecture.svg)

**模拟数据** 入口可以生成并预览食品、美妆、3C 的合成商家商品，确认后投送管理后台，可自动进入规则质检队列。可选数量、随机种子、批次 ID 和正常／风险／缺失／冲突场景，重复投送同一批次不会覆盖已编辑商品。详见 [数据模拟说明](docs/data-simulation.md)。

基于 FastAPI、MySQL、Elasticsearch 和可选 DeepSeek 的软件工程演示项目，支持商品文案检查、规则依据引用、保守改写、报告摘要、执行链路查询和50条固定用例评测。管理后台提供登录、商品管理、版本历史、批量质检、批量审核发布、下架及人工审核记录。界面为原生 HTML/CSS/JavaScript，运行时无需单独构建前端。

Agent 使用自定义 Python Orchestrator、State、Skill 和 Tool 编排，按固定流程与条件分支执行；没有使用 LangChain、LangGraph、模型自主工具调用或基础模型训练。

## 快速部署：Docker（推荐）

启动 Docker Desktop，在本项目根目录执行：

```bash
# 在克隆后的项目根目录运行
test -f .env || cp .env.example .env
docker compose up --build -d --wait
docker compose ps
curl http://127.0.0.1:8000/health
```

打开管理后台 <http://127.0.0.1:8000/admin>，本地演示账号为 **admin / admin12345**；单商品质检工具在 <http://127.0.0.1:8000/>，接口文档在 <http://127.0.0.1:8000/docs>。首次拉取ES镜像可能较慢；建议为Docker配置至少4GB内存。API启动自动建表、幂等导入50个商品/评测样例、33条规则和12个后台演示商品，并尝试导入ES。ES不可用时API仍可使用MySQL规则。

后台可直接新增商品，或勾选演示商品后选择批量规则质检；无需配置大模型即可跑通。操作说明、发布条件和后台新增表见 [管理后台使用说明](docs/admin.md)。

批量质检完成后可直接“继续批量审核发布”，也可勾选最多20件商品进入发布预览。核对报告、填写审核说明并确认后逐件发布；不合格或版本已变化的商品会单独显示失败原因，不影响其他商品。发布仅更新本系统状态。

默认 Compose 启动 MySQL、Elasticsearch、API、worker 四个服务，并等待依赖健康。运行中 ES 故障时检索可降级到 MySQL；Compose 的 API 健康检查仍要求 ES 可用，因此降级运行与整套服务健康是两个不同概念。

主机 MySQL 端口默认3307，容器内部3306；Workbench连接127.0.0.1:3307，用户/密码/数据库默认均为quality_agent。Elasticsearch为127.0.0.1:9200，API为127.0.0.1:8000。

数据库和评测文件保留在命名卷中。普通停止命令会保留商品、版本、审核记录和历史结果：

```bash
docker compose down
```

## 启用 DeepSeek

在本地 `.env` 填写 `DEEPSEEK_API_KEY`，然后重新创建 API 和 worker 容器以注入配置：

```bash
docker compose up -d --force-recreate api worker
```

页面选择“完整模式”。密钥仅从环境变量读取，不输出、不打包进镜像。无密钥、连接失败或模型输出格式不合法时保留规则结果，并在warnings与Trace中说明。规则模式完全不调用模型；CLI规则模式还不访问数据库或ES。

完整模式默认将模型用于类目识别（仅类目缺失）和语义审核。改写与摘要默认使用确定性逻辑，额外的模型复述调用默认关闭。保守改写会删除命中风险的完整分句、保留原有信息；缺失配料、参数等事实仍由商家补充，不提供自由创作文案。本地 Elasticsearch 使用关键词检索，无需额外 RAG API Key；向量召回和重排尚未实现。

## 一键检查与批量评测

启动服务后验证三个风险等级、MySQL持久化、Trace与导出；此命令写入3个新任务：

```bash
docker compose exec api python -m scripts.verify_project --base-url http://127.0.0.1:8000
```

把50条用例全部通过API检查，并保存每条任务、报告、Trace和评测预测：

```bash
docker compose exec api python evaluator/run_eval.py --api-url http://127.0.0.1:8000 --mode rules --persist
```

页面也提供“一键评测”，需要管理员登录和有效 CSRF。上述 CLI 逐例调用单商品接口，是另一种运行方式。JSON/Markdown 报告在容器 `/app/evaluator/reports/`，取回本地：

```bash
docker compose cp api:/app/evaluator/reports ./output-evaluation
```

开发机离线验证（先按下一节安装 Python 依赖；不写数据库、不要求外部服务）：

```bash
RUN_MYSQL_TESTS=0 .venv/bin/pytest -q
.venv/bin/python -m scripts.check_product data/samples/demo_food.json --trace
.venv/bin/python evaluator/run_eval.py
```

前端语言切换及下拉菜单交互测试（Node.js 22 或更新版本）：

```bash
npm ci --ignore-scripts
npm test
```

下拉菜单保留原生表单值与校验，支持方向键、回车、Esc 和键盘搜索。测试覆盖选择事件、表单重置、必填校验及动态翻译同步；浮层定位与手机布局需在浏览器中检查。

模型评测使用 `--mode full`。报告会记录成功模型使用数和降级数；full但没有模型成功调用不能作为模型效果验证。问题召回/FDR按每个样例的问题类型集合精确匹配，不会更改gold来追求满分。详见 [评测说明](docs/evaluation.md)。

完整工程测试、独立 MySQL 测试容器、测试覆盖与验证边界见 [测试指南](docs/testing.md)。不要在日常演示库中直接开启全部 MySQL 集成测试，队列测试会领取所在数据库中的任务。

## 本地开发

Docker运行数据库和检索服务，API在本机运行：

```bash
test -f .env || cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
docker compose stop api worker
docker compose up -d --wait mysql elasticsearch
.venv/bin/python -c "from app.main import app; from db.session import Base, engine; Base.metadata.create_all(engine)"
.venv/bin/python -m scripts.migrate
.venv/bin/python -c "from scripts.seed_data import seed; seed(import_rules=False)"
.venv/bin/python -m scripts.seed_catalog
.venv/bin/python -m scripts.index_rules
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

若已有 Docker API/worker，先 `docker compose stop api worker` 再使用本机进程。在另一个同目录终端执行 `.venv/bin/python -m scripts.worker` 消费持久队列。ES 索引失败时仍可启动 Uvicorn 使用 MySQL 降级，服务恢复后重新运行 index_rules。Shell 已导出的同名变量优先于 `.env`；本机 API 和 worker 应使用同一配置。

## API 示例

```bash
curl -s 'http://127.0.0.1:8000/api/products/inspect?mode=rules' \
  -H 'Content-Type: application/json' \
  --data-binary @data/samples/demo_food.json
```

用响应中的task_id查询 `/api/tasks/{task_id}`、`/api/results/{task_id}`、`/api/tasks/{task_id}/traces`，下载 `/api/results/{task_id}/export`。每次POST会创建新任务，不按product_id覆盖结果。

## 数据来源与存储

仓库数据均为合成数据，没有爬取真实商家数据：30条人工样例、包含这些样例的50条固定评测用例、33条演示规则；初始化时从样例中选取12件商品建立后台目录。模拟器可生成9个模拟商家的新投递。评测标签由项目编写，尚未经过领域专家独立复核，合成数据分数不等于线上准确率。

数据库包含13张表。样例与评测库、后台商品目录、每次质检的输入/报告/Trace、审核历史、会话以及持久任务队列分别存储。完整 API 请求原文保存在 `inspection_tasks.input_json`，问题和报告保存在 `inspection_results`；后台文案当前版在 `managed_products`、历史版在 `product_revisions`，通过 `product_inspections` 关联报告。运行中产生的数据库内容不属于 GitHub 上传文件。

## 工程说明

- [文档导航](docs/README.md)：阅读顺序和各文档语言。
- [API与数据库设计](docs/api_database.md)：字段、接口、13张表及兼容升级。
- [管理后台使用说明](docs/admin.md)：账号、商品审核流程、批量发布和后台 API。
- [架构与实现说明](docs/architecture.md)：流程、模块职责、Tool/Skill/Agent边界。
- [配置说明](docs/configuration.md)：.env.example每一项用途。
- [评测说明](docs/evaluation.md)：指标、批量命令、错误案例与标注局限。
- [当前能力范围](docs/scope.md)：已实现功能、数据来源与能力边界。
- [测试指南](docs/testing.md)：离线测试、独立 MySQL 集成测试和 CI。
- [规则与数据说明](data/evaluation/README.md)：规则版本、50例构成及gold来源。

项目包含文本自检工具和单管理员商品审核后台，规则属于教学演示规范。“发布/下架”管理本系统的商品状态，尚未对接外部电商平台的上架接口。图片审核、多角色权限、商家自助入驻、订单交易、向量召回、重排、多轮改稿和 MCP 尚未实现。本项目是辅助审核原型，不是法律意见或经过认证的合规系统。

## 运维与升级

Compose 会同时启动 API 和持久任务 worker；worker 从 MySQL 队列表领取批量质检项，过期租约会自动恢复。查看队列日志：

```bash
docker compose logs --tail=100 worker
```

`quality_rules` 的维护事实源是 MySQL。启动仅补齐新库缺失的演示规则，不覆盖已有行；需要显式覆盖演示 fixture 时运行 `docker compose exec api python -m scripts.seed_data`，之后运行 `docker compose exec api python -m scripts.index_rules` 将当前 MySQL 规则同步到 Elasticsearch 版本 alias。生产环境请在迁移前备份：

```bash
mkdir -p backups
docker compose exec -T mysql sh -c 'exec mysqldump --single-transaction --no-tablespaces -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > backups/quality-agent.sql
docker compose exec api python -m scripts.migrate
```

备份命令会覆盖同名备份，请为要保留的历史备份另取文件名。先在隔离实例验证恢复，再考虑切换运行数据库。应用启动执行幂等迁移/建表，以兼容旧演示卷；目前是有限列升级钩子，没有完整的版本化迁移框架或自动回滚。共享部署前设置独立管理员和 MySQL 凭据，配置 HTTPS；`APP_ENV=production` 会拒绝示例管理员密码，不能替代完整的部署审查。详见 [配置与运维](docs/configuration.md)。

当前限制包括单管理员、进程内及可选文件锁登录限流、规则检索仍为 Elasticsearch/MySQL/本地回退链路，以及未接入外部电商发布接口。API `/health` 会报告 MySQL 与 Elasticsearch 能力；完整模式没有模型成功调用时只能作为规则基线参考。
