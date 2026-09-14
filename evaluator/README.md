# Evaluator

默认在本机离线跑 50 条合成 fixture，不连接 MySQL、不需要模型 API Key：

```bash
python evaluator/run_eval.py
python -m evaluator.run_eval --mode rules
```

默认输出 `evaluator/reports/latest.json` 和同名 `.md`，包含总体指标、分类目指标和具体失败案例。通过 `--output /path/report.json` 保存历史报告。固定数据说明见 `data/evaluation/README.md`；旧 30 条标注完整保留，新增 20 条需领域专家复核，评测结果不等于真实线上准确率。

```bash
# 使用真实 Agent 模型路径；需要配置相应环境变量，模型不可用时由 Agent 降级
python -m evaluator.run_eval --mode full --output evaluator/reports/full.json

# 每例经HTTP API创建真实任务、结果和Trace；API和MySQL需要先启动
python evaluator/run_eval.py --api-url http://127.0.0.1:8000 --mode rules

# 额外把预期答案、预测类型及每例指标写入evaluation_cases
python evaluator/run_eval.py --mode rules --persist
python evaluator/run_eval.py --api-url http://127.0.0.1:8000 --persist
```

`--api-url` 发送 `POST /api/products/inspect?mode=rules|full`，API 的正常持久化负责 `inspection_tasks`、`inspection_results`、`agent_traces`。`--persist` 独立负责 `evaluation_cases`，可与本地或 HTTP 模式组合；它更新本次的 `case_id`，不删除其他数据。每例 metrics 保存关联的 task_id、run_id 和 mode。数据库没有预期风险列，因此将 `expected_risk_level` 放入 `product_json`，构造 `ProductInput` 前显式过滤。

## 指标口径

问题以每条商品的 `issue_type` 集合精确匹配，重复证据只计一次。对不同商品的同类问题分别计数，再汇总为 micro-average。不会通过规则 ID、字符串近义词或合并缺失标签改变 gold。

| 指标 | 定义 |
| --- | --- |
| `issue_recall` | TP / (TP + FN)，检出预期问题的比例 |
| `issue_precision` | TP / (TP + FP)，所有预测问题中的正确比例 |
| `false_discovery_rate` | FP / (TP + FP)，所有预测问题中的误报比例。不是 FPR = FP / (FP + TN) |
| `high_risk_precision` | 预测 high 且预期 high 的样例数 / 预测 high 的样例数 |
| `high_risk_recall` | 预测 high 且预期 high 的样例数 / 预期 high 的样例数 |
| `risk_accuracy` | 风险等级与预期相同的样例数 / 全部样例数 |
| `structured_output_success_rate` | 可通过 InspectionReport Schema 验证的返回数 / 全部样例数 |
| `execution_success_rate` | 结构有效且 status=success 的返回数 / 全部样例数 |
| `strict_case_accuracy` | 问题集合和风险全对、且无调用错误的样例数 / 全部样例数 |
| `average_latency_ms` | 每例从输入验证、执行到输出验证的耗时均值；HTTP模式包括网络往返，失败调用也计入 |

空分母输出 JSON `null`、Markdown `N/A`，不会将空集记作100%。原始 TP/FP/FN、high TP/FP/FN 和其他分母计数都在报告中。未定义可枚举的真实阴性问题空间，因此不输出易混淆的 FPR，也不把 FDR 标成 FPR。

调用失败、HTTP错误或无效结构不会中断其余样例；该例预测集合为空，所有预期问题计为漏报，风险不匹配并记录错误。结构有效但 `status=failed` 仍算结构成功，执行失败且不采纳预测。默认仅按问题与风险比较；部分步骤降级但最终报告成功时应结合 Trace 解读 full 模式结果。

summary 和分类目指标还提供 `degraded_case_count` 与 `model_used_case_count`，来自报告的降级标记与成功使用模型标记。config 的 `model_evaluation_status` 区分 `not_requested`、`no_model_usage_reported`、`partial_model_usage`、`model_used_all_cases`，配有显式说明。full 模式没有观察到成功模型使用时，报告明确注明“不能作为真实LLM评测”；此时可能未配置模型或调用失败。模型使用标记也不代表所有模型步骤都成功，应继续核对 Trace。

## 报告格式与退出码

JSON 顶层为 `schema_version`、`run_id`、`generated_at`、`config`、`summary`、`by_category`、`cases`、`failures`。`by_category` 用“食品”“美妆”“3C”作键，值与 summary 同结构。cases 含完整结构化报告、预期/预测问题与风险、`missing_issues`、`false_positive_issues`、`risk_mismatch`、`error`、`latency_ms`；failures 是供界面展示的失败摘要。

退出码 0 表示评测执行完成，即使存在质量漏报或误报；1 表示存在调用失败或持久化失败（仍保存报告）；2 表示数据集或配置等启动错误。没有100%自动验收门槛。full 模式退出码0也不表示真实模型成功路径已验收，需要核对模型使用和降级字段。

HTTP 每例默认超时180秒，可通过 `--timeout` 修改，以容纳 full 模式多次有界模型调用。输出路径必须以 `.json` 结尾。JSON与Markdown各自以同目录临时文件原子替换，读取latest不会见到正在写入的半份JSON。
