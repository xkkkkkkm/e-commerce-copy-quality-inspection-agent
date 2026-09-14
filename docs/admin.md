# 商品文案管理后台

入口：<http://127.0.0.1:8000/admin>。本地演示登录名`admin`，密码`admin12345`。支持食品、美妆、3C三类商品。原来的单条质检演示仍在`/`。

## 启动与检查

在项目根目录执行：

```bash
docker compose up --build -d --wait
docker compose ps
```

启动时自动补建缺失的后台表，并幂等插入12个演示商品，均以待审核状态创建，不伪造质检或发布历史。重启不会覆盖已编辑的商品。首次进入可勾选商品做批量规则质检，也可以执行：

```bash
docker compose exec api python -m scripts.seed_catalog --inspect
```

此命令只为`catalog_`编号的演示商品补跑尚无当前成功结果的规则质检，不会发布商品。每次检查都会保存真实报告与Trace。

## 管理员操作流程

1. 登录后查看商品总数、待审核、已发布、高风险等统计。按商家/商品编号/标题搜索，并按类目、状态、风险筛选、翻页。
2. 新增商品时填写商家、商品编号、类目、标题、详情及属性。属性以中文表单展示，选择类目后自动显示配料/保质期、肤质/用法或型号/兼容性等字段；额外字段可展开“更多属性 · 高级编辑”。保存后为待审核状态；商品编号创建后不可修改。
3. 打开商品详情，执行规则质检或完整质检。可以在列表勾选最多20条批量执行，单条出错不影响后续条目。规则模式无需大模型密钥。
4. 查看问题、原文证据、规则依据、改写建议、历史质检和执行Trace。修改文案后会增加版本号，并立即使旧质检失效；历史报告和原文仍可查看。
5. 确认报告后发布、退回修改或下架。发布操作会检查当前版本，审核理由、操作者、状态变化与关联质检记录会写入日志。

### 批量审核发布

列表勾选最多20件商品后点击“批量审核发布”，或在批量质检完成后点击“继续批量审核发布”。系统重新读取每件商品的当前状态和完整报告，列出可审核商品及拦截原因；相同标题的商品同时显示商品编号，避免混淆。

“通过”的商品默认勾选；中低风险商品需展开“查看文案与质检问题”，核对原文、属性和问题后手动勾选。高风险、未完成、完整模式降级、过期、缺失或不一致的报告不能用于发布。规则模式仅因检索回退产生的降级提示不单独阻止发布，仍需满足其他完整性条件。填写统一审核说明并勾选复核确认后提交，说明、操作者和报告编号会逐件写入 `product_audits`。

每件商品独立事务，提交时再次使用行锁校验版本、状态和报告编号。若预览后被编辑、重新质检或改变状态，该件失败而其他合格商品仍可发布。结果逐件展示成功、失败与未提交，并可查看商品。重复请求不会重复发布仍处于已发布状态的商品或增加审核日志；网络中断时需重新检查状态，不自动重试发布。此功能不是持久批次任务，不保证整批同时成功，不调用外部上架接口。

| 当前状态 | 可用流程 |
|---|---|
| 待审核 pending | 质检、修改、发布或退回修改 |
| 已发布 published | 下架；编辑后转待审核 |
| 已退回 rejected | 修改或重新提交审核 |
| 已下架 offline | 重新提交审核；符合质检条件也可重新发布 |

新商品采用待审核状态，`draft`作为兼容状态预留。界面根据状态显示可执行操作。

发布必须有当前版本成功且完整的质检与原始任务/报告记录。高风险禁止发布；中低风险必须填写人工审核说明；检查未完成、失败、过期或完整模式发生降级时禁止发布。没有大模型密钥时选择规则模式即可走通整个流程。规则模式只提供规则覆盖范围内的检查，发布仍由管理员确认。

已发布商品开始重新检查时，系统立即撤回至待审核并记日志，检查完成后需再次确认发布，避免检查中断或新的风险绕过人工审核。并发编辑或重复质检时，旧结果仅进入历史，不覆盖最新版本或较晚开始的质检。

“发布/下架”是本系统的商品状态，尚未调用淘宝、天猫等外部平台接口。本版为单管理员后台，未包含商家自助登录、多角色权限、订单交易或图片审核。页面批量质检仍同步逐项执行；需要持久后台执行时可调用 `POST /api/admin/jobs`，携带唯一 `idempotency_key`、`items` 和 `mode`，通过 `GET /api/admin/jobs/{job_id}` 查看进度，使用 `POST /api/admin/jobs/{job_id}/cancel` 取消未开始的条目。独立 worker 自动领取任务、续租、恢复过期租约和有界重试。

## 模拟商家投递与中英文界面

原始数据来自人工编写的30条商品样例、包含这些样例的50条固定评测用例，以及从样例中选出的12件后台演示商品，没有连接真实电商平台数据流。

登录后点击“模拟数据”，设置数量（1–100）、随机种子、批次ID及场景，点击“预览数据”检查生成内容，再点击“投递此预览”。可选择混合、合规文案、风险文案、信息缺失或信息冲突，覆盖食品、美妆和3C以及9个模拟商家。“合规”等场景名称仅描述输入构造，最终是否通过由真实质检决定。

勾选自动规则质检后，整批商品进入持久任务队列。点击“刷新任务状态”查看每件商品进度及质检报告。相同参数重复投递会复用未编辑的同批记录和任务；内容已被修改或同批参数冲突时返回409，不覆盖商品。逐件提交允许中断后以相同请求恢复，不保证全批事务。数据来源标记保存在 `attributes._simulation`，生成模块不伪造报告，也不自动发布。命令行及API示例见 [模拟数据说明](data-simulation.md)。

后台和工作台右上角可切换“简体中文 / English”，弹窗内也提供切换入口。选择会保存，并在同源页面间同步；未保存的表单、审核理由与当前报告视图会保留。界面、状态、内置规则说明和建议提供英文；商品原文、原文证据、人工备注及下载的JSON保持原始内容。英文界面不意味着已经支持英文商品检测。面向国外审查者的部署与演示指南见 [English README](../README.md)。

## MySQL存储

Workbench仍连接`127.0.0.1:3307`，数据库`quality_agent`。刷新Schemas下的Tables，可看到原六张表和七张后台/队列表：

| 表 | 存储内容 |
|---|---|
| managed_products | 当前商品文案、商家、类目、发布状态、版本号和当前质检指针 |
| product_revisions | 每一版完整商品输入及编辑人；修改不覆盖历史 |
| product_inspections | 商品版本对应的质检任务、风险、状态、报告快照和失败原因 |
| product_audits | 新建、编辑、检查、发布、下架、退回等操作记录与理由 |
| admin_sessions | 登录会话的SHA-256摘要、账号、创建和到期时间；不存明文会话token |
| inspection_jobs | 持久批次、幂等键与请求摘要、状态、租约、取消标记和重试计数 |
| inspection_job_items | 批次商品版本、状态、持久 task_id、结果关联和每条重试次数 |

检测出问题的文案在`managed_products`与`product_revisions`中；检测结果在`product_inspections.report_json`，并关联原`inspection_results`与`agent_traces`。主商品表以数据库数字`id`供后台API定位，业务商品编号是`product_id`，两者不同。

后台 ORM 定义在 `db/catalog_models.py`、`db/auth_models.py` 和 `db/job_models.py`；商品/认证初始 SQL 定义在 `scripts/init_catalog.sql` 与 `scripts/init_auth.sql`。队列表由 ORM 创建。应用启动创建缺失表并执行有限的列升级钩子。部署无需清空原库，MySQL 数据卷和评测报告卷均持久化。

## 接口与认证

| 方法/路径 | 功能 |
|---|---|
| POST /api/admin/auth/login | 用户名密码登录，返回username、csrf_token，并设置HttpOnly会话cookie |
| GET /api/admin/auth/me | 当前会话及CSRF token，供刷新页面后恢复登录 |
| POST /api/admin/auth/logout | 撤销当前会话 |
| POST /api/admin/auth/logout-all | 撤销全部管理员会话，要求登录和 CSRF |
| GET /api/admin/summary | 汇总统计 |
| GET /api/admin/products | 列表；q/category/status/risk/page/page_size筛选 |
| POST /api/admin/products | 新建商品 |
| GET /api/admin/products/{id} | 商品、历史版本、历史检查和审核日志 |
| PUT /api/admin/products/{id} | 完整更新商品输入；带expected_version |
| POST /api/admin/products/{id}/inspect | 检查指定版本；expected_version、mode=rules/full |
| POST /api/admin/products/batch-inspect | items=[{id,expected_version}]、mode；最多20条、编号不重复 |
| POST /api/admin/products/batch-publish/preview | items=[{id,expected_version}]；只读校验，返回eligible、拦截原因、当前报告及snapshot；点击“重新检查”时省略expected_version，读取最新版本重新审核 |
| POST /api/admin/products/batch-publish | items使用预览snapshot：id、expected_version、expected_status、expected_inspection_id；reason必填，最多20条，逐件返回结果 |
| POST /api/admin/products/{id}/actions/{action} | publish/offline/reject/submit；expected_version、reason，可带expected_status；publish 还必须携带当前已复核报告的 expected_inspection_id |
| POST /api/admin/jobs | 创建持久批次质检；items、mode、唯一idempotency_key |
| GET /api/admin/jobs | 按游标查看批次；limit、before |
| GET /api/admin/jobs/{job_id} | 查看批次、每条商品状态、task_id和结果关联 |
| POST /api/admin/jobs/{job_id}/cancel | 请求取消未开始的批次条目 |
| POST /api/admin/simulation/preview | 生成合成商品预览；count、seed、scenario、batch_id，不写入数据库 |
| POST /api/admin/simulation/deliver | 投递同参数批次；enqueue_inspection可选，默认进入规则质检队列 |
| GET /api/admin/inspections/{task_id}/report | 历史质检报告 |
| GET /api/admin/inspections/{task_id}/traces | 历史执行链 |

后台API均要求有效登录；除登录外，写请求还须携带`X-CSRF-Token`。浏览器自动发送会话cookie。`expected_version`防止多人页面或多个窗口覆盖新内容，版本冲突返回409；修改前刷新详情即可获取最新版本。失败时显示具体原因，不将检查失败视为合格。

单商品发布时，`expected_inspection_id` 使用详情中已打开并复核的 `product.latest_inspection_id`（`product_inspections` 的数字 ID，不是字符串 `task_id`）。即使版本未变化，重新质检也会改变当前报告；缺少该字段或报告已变化均返回409，需重新复核。其他状态操作不要求该字段。

账号配置及HTTPS cookie选项见 [配置说明](configuration.md)。当前为本机演示环境；需要外部访问时应先更换默认凭据，并为完整应用安排HTTPS、接口权限和部署策略。

## 自动测试

完整命令见 [测试指南](testing.md)，包含无需外部服务的测试和独立 MySQL 测试容器。不要直接在日常后台数据库中启用整个 MySQL 测试套件；队列测试会领取所在库的任务。

后台测试在 `tests/test_admin_auth.py`、`tests/test_admin_api_mysql.py`、`tests/test_catalog.py` 和 `tests/test_batch_publication.cjs`，覆盖会话、CSRF、版本、状态流转、发布门槛、历史记录和批量失败隔离。原质检 API 集成测试会留下独立测试任务，测试容器可在完成后单独清理。
