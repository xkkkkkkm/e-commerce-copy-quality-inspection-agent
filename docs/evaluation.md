# 评测与规则数据说明

完整运行说明与指标定义见 [evaluator/README.md](../evaluator/README.md)，标注来源、固定样例分布与人工复核限制见 [data/evaluation/README.md](../data/evaluation/README.md)。

## 一键执行

在已安装项目依赖的环境中运行：

```bash
python evaluator/run_eval.py --mode rules
# 等价模块入口
python -m evaluator.run_eval --mode rules
```

默认本地离线执行 50 例，食品 20、美妆 15、3C 15；读取本地 33 条规则（通用 9，三个专项类目各 8），不要求 MySQL、ES 或模型密钥。输出 `evaluator/reports/latest.json` 和 `latest.md`，Web Demo 可读取此 JSON 展示总体、分类目和逐例结果。

## 真实链路验收

```bash
python -m scripts.seed_data
python evaluator/run_eval.py --api-url http://127.0.0.1:8000 --mode rules --persist
```

HTTP 模式每条都走正式质检 API，创建真实任务、结果、Trace；`--persist` 同时更新 `evaluation_cases` 的预测和指标。seed 可重复运行，更新项目规则与 gold，但保留历史任务、结果、Trace 和已有评测预测。

`--mode full` 使用 Agent 的规则检索与模型路径，受模型密钥、网络和服务状态影响；应同时查看报告中的 `model_used`、warnings、retrieval_source 和 Trace，不能把降级结果当作已验证了真实模型效果。

## 如何解读分数

报告同时给出原始计数与比例：问题召回为 TP/(TP+FN)，误报比例 FDR 为 FP/(TP+FP)，high 样例精确率/召回率分开统计，另有风险级别准确率、结构成功率和平均耗时。空分母是 `null`/N/A。调用失败仍保留在分母，且在每例 `error` 字段说明，不会删除失败样例美化成绩。

失败列表明确区分 `missing_issues`、`false_positive_issues`、`risk_mismatch` 和调用 `error`。问题按每例 issue_type 集合精确匹配，不将“兼容性说明缺失”自动改成“关键信息缺失”；重复证据只计一次。

旧 30 条标签完整保留，存在少数标签粒度和标题长度边界不一致。新增 20 条专门覆盖语义、否定、占位属性和阈值边界，尚需领域专家复核。应分别分析规则能力缺口、误触发和 gold 分歧；不能按检测器输出修改预期标签，不能以合成集分数推断线上质量。

## 工程检查

`pytest tests/test_evaluator.py -q` 覆盖问题分母与重复证据、空集、调用异常后继续执行、无效结构与失败状态区别、分类目统计、原始标注保留、标注字段过滤及 HTTP 请求模式。数据库持久化需要额外的运行中 MySQL 验证；单测不以 SQLite 替代 MySQL。
