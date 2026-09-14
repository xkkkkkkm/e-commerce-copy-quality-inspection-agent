# Skills

Skill 是面向业务能力的编排单元，统一继承 `BaseSkill`：

```python
class BaseSkill:
    name: str
    description: str
    def can_handle(self, context: dict) -> bool: ...
    async def run(self, context: dict) -> dict: ...
```

Skill 组合确定性 Tools 或受约束的模型调用，不把具体检测规则散落到 Orchestrator 中。返回值统一包含 `skill_name`、`passed`、`issues` 和 `metadata`。

当前通用 Skills：

- `TitleQualitySkill`：标题长度、关键词堆砌、核心属性。
- `RiskExpressionSkill`：违禁词和高风险表达。
- `RequiredAttributeSkill`：按食品、美妆、3C 检查必填属性。
- `RiskScoringSkill`：输出 `pass`、`low`、`medium`、`high`。
- `ReportGenerationSkill`：生成并校验统一 JSON 报告。

`CategoryRouter` 根据 `category` 选择食品、美妆、3C Skill；`FoodQualitySkill`、`BeautyQualitySkill`、`ElectronicsQualitySkill` 是可导入名称，也保留早期 CategorySkill 名称兼容。

`SemanticRiskSkill` 在完整模式调用模型，输出只能引用适用规则与商品原文。`CopywritingOptimizationSkill` 提供保守候选；改写和 `ReportSummarySkill` 的额外模型复述调用默认关闭，摘要使用确定性逻辑。无模型配置时语义节点跳过并提示，必需规则 Skill 失败时输出 partial 待复核报告。

Skill用BaseSkill.tool调用确定性函数，BaseSkill.model调用模型，两者都在实际执行前后记录skill_name/tool_name、输入输出摘要、状态和耗时。can_handle控制是否执行；run返回统一结构，经Orchestrator校验后才合并回State。
