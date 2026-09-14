from app.schemas import SemanticOutput, SummaryOutput
from skills.base import BaseSkill


class SemanticRiskSkill(BaseSkill):
    name = "semantic_risk"
    description = "结合检索规则，分析当前类目的隐性语义风险。"

    def can_handle(self, context):
        return context.get("mode") == "full" and bool(context.get("llm") and context["llm"].enabled)

    async def run(self, context):
        rules = context.get("rules", [])
        output = self.model(context, "semantic", {
            "product": context["product"], "rules": rules, "known_issues": context["issues"],
            "requirements": "evidence必须为对应title或description的原文连续引用；rule_id必须来自给定规则；不重复已发现的问题。",
        }, SemanticOutput)
        by_id = {rule["rule_id"]: rule for rule in rules}
        issues = []
        for item in output.issues:
            rule = by_id.get(item.rule_id)
            if not rule or item.evidence not in context["product"].get(item.field, ""):
                raise ValueError("模型证据或规则引用不属于本次输入")
            if item.issue_type != rule["issue_type"] or item.risk_level != rule["risk_level"]:
                raise ValueError("模型问题类型或风险等级与引用规则不一致")
            issues.append({**item.model_dump(), "matched_text": item.evidence, "source": "llm"})
        return self.result(skill_name=self.name, passed=not issues, issues=issues)


class ReportSummarySkill(BaseSkill):
    name = "report_summary"
    description = "依据已确认问题生成报告摘要。"

    def can_handle(self, context):
        return context.get("mode") == "full" and bool(context.get("llm") and context["llm"].enabled
                                                     and getattr(context["llm"], "optional_generations", True))

    async def run(self, context):
        permitted = context.get("summary", "")
        output = self.model(context, "summary", {
            "category": context["category"], "issues": context["issues"], "score": context["score_result"],
            "permitted_summary": permitted,
            "requirements": "仅总结已确认的问题，不新增结论或改动风险等级。",
        }, SummaryOutput)
        if output.summary != permitted:
            raise ValueError("模型摘要改变了已确认的结论")
        return self.result(skill_name=self.name, passed=True, metadata={"summary": output.summary})
