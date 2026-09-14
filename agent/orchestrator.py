from __future__ import annotations

import asyncio
import inspect
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from app.schemas import CategoryDecision, InspectionReport, Issue, ProductInput
from llm import DeepSeekClient
from rag import RuleRetriever
from skills import CategoryRouter
from skills.base import BaseSkill
from skills.copy_optimization import CopywritingOptimizationSkill, safe_copy
from skills.general import ReportGenerationSkill, RequiredAttributeSkill, RiskExpressionSkill, RiskScoringSkill, TitleQualitySkill
from skills.semantic import ReportSummarySkill, SemanticRiskSkill
from tools.quality_checks import risk_score_calculator


@dataclass
class InspectionState:
    task_id: str
    product: dict[str, Any]
    category: str = ""
    mode: str = "rules"
    issues: list[dict] = field(default_factory=list)
    rules: list[dict] = field(default_factory=list)
    retrieved_rules: list[dict] = field(default_factory=list)
    score_result: dict = field(default_factory=lambda: {"score": 0, "risk_level": "pass"})
    optimized_title: str = ""
    optimized_description: str = ""
    summary: str = ""
    rewrite_reason: str = ""
    selected_skills: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)
    retrieval_source: str = "local"
    model_used: bool = False
    required_failed: bool = False

    def context(self) -> dict:
        return {key: deepcopy(getattr(self, key)) for key in (
            "task_id", "product", "category", "mode", "issues", "rules", "score_result",
            "optimized_title", "optimized_description", "summary", "rewrite_reason",
            "warnings", "retrieval_source", "model_used", "required_failed",
        )}


def infer_category(product: dict) -> str:
    """Route strong structured signals; ambiguous input requires a user category."""
    attributes = product.get("attributes", {})
    groups = {"食品": {"shelf_life", "storage", "origin"},
              "美妆": {"skin_type", "usage", "precautions"},
              "3C": {"model", "specifications", "compatibility", "warranty"}}
    scores = {name: len(keys & attributes.keys()) for name, keys in groups.items()}
    best = max(scores, key=scores.get)
    if scores[best] >= 2 and list(scores.values()).count(scores[best]) == 1:
        return best
    words = {"食品": ("茶", "饼干", "燕麦", "牛奶", "果汁", "蜂蜜"),
             "美妆": ("洁面", "面膜", "面霜", "精华", "口红", "眼霜"),
             "3C": ("耳机", "硬盘", "充电器", "路由器", "键盘", "显示器")}
    matches = [name for name, tokens in words.items() if any(token in product["title"] for token in tokens)]
    if len(matches) == 1:
        return matches[0]
    raise ValueError("无法可靠识别商品类目，请明确选择食品、美妆或3C。")


class AgentOrchestrator:
    def __init__(self, trace_sink: Callable | None = None, *, mode: str = "rules", retriever=None, llm=None):
        if mode not in ("rules", "full"):
            raise ValueError("mode必须是rules或full")
        self.mode = mode
        self.trace_sink = trace_sink
        self.retriever = retriever or RuleRetriever(backend="local" if mode == "rules" else "auto")
        self.llm = llm or DeepSeekClient()
        self.category_router = CategoryRouter()
        self.general_skills = (TitleQualitySkill(), RiskExpressionSkill(), RequiredAttributeSkill())
        self.scoring_skill = RiskScoringSkill()
        self.report_skill = ReportGenerationSkill()

    def _trace(self, state, step_name, *, skill_name=None, tool_name=None, input_summary="",
               output_summary="", status="success", started=None):
        entry = dict(step_name=step_name, skill_name=skill_name, tool_name=tool_name,
                     input_summary=input_summary or f"product_id={state.product.get('product_id')}",
                     output_summary=output_summary, status=status,
                     latency_ms=max(0, int((time.perf_counter()-started)*1000)) if started is not None else 0)
        state.traces.append(entry)
        if tool_name and tool_name.startswith("deepseek.") and status == "success":
            state.model_used = True
        if self.trace_sink:
            try:
                # Choose the sink convention before calling; never retry an executed sink.
                try:
                    inspect.signature(self.trace_sink).bind(**entry)
                    keyword_sink = True
                except TypeError:
                    keyword_sink = False
                if keyword_sink:
                    self.trace_sink(**entry)
                else:
                    self.trace_sink(entry)
            except Exception as exc:
                warning = f"Trace写入失败（{type(exc).__name__}）。"
                if warning not in state.warnings:
                    state.warnings.append(warning)

    def _context(self, state):
        context = state.context()
        context["llm"] = self.llm
        context["emit"] = lambda step, **kwargs: self._trace(state, step, **kwargs)
        return context

    async def _execute_skill(self, state, skill: BaseSkill):
        started = time.perf_counter()
        try:
            context = self._context(state)
            if not skill.can_handle(context):
                self._trace(state, skill.name, skill_name=skill.name, status="skipped",
                            output_summary="当前模式或条件不需要执行", started=started)
                return None
            state.selected_skills.append(skill.name)
            result = await skill.run(context)
            if not isinstance(result, dict) or not {"skill_name", "passed", "issues", "metadata"} <= result.keys():
                raise ValueError("Skill返回格式无效")
            if not isinstance(result["metadata"], dict) or not isinstance(result["passed"], bool):
                raise ValueError("Skill元数据格式无效")
            issues = [Issue.model_validate(issue).model_dump() for issue in result["issues"]]
            if skill is self.scoring_skill:
                metadata = result["metadata"]
                if type(metadata.get("score")) is not int or metadata["score"] < 0 or metadata.get("risk_level") not in ("pass", "low", "medium", "high"):
                    raise ValueError("评分结构无效")
            if skill is self.report_skill:
                InspectionReport.model_validate(result["metadata"]["report"])
            for issue in issues:
                if issue["issue_type"] == "医疗功效暗示":
                    issue["issue_type"] = "医疗功效宣称"
                duplicate = any(existing["field"] == issue["field"] and existing["issue_type"] == issue["issue_type"]
                                and (existing["evidence"] == issue["evidence"] or
                                     (existing.get("matched_text") and issue.get("matched_text") and
                                      (existing["matched_text"] in issue["matched_text"] or issue["matched_text"] in existing["matched_text"])))
                                for existing in state.issues)
                if not duplicate:
                    state.issues.append(issue)
            state.warnings.extend(result["metadata"].get("warnings", []))
            self._trace(state, skill.name, skill_name=skill.name, started=started,
                        output_summary=f"passed={result['passed']}, issues={len(issues)}",
                        status="degraded" if result["metadata"].get("warnings") else "success")
            return result
        except Exception as exc:
            if skill in self.general_skills or skill in (self.scoring_skill, self.report_skill) or skill.name.endswith("_category"):
                state.required_failed = True
            state.errors.append(f"{skill.name}:{type(exc).__name__}")
            state.warnings.append(f"{skill.name}执行失败（{type(exc).__name__}），本次结果可能不完整。")
            self._trace(state, skill.name, skill_name=skill.name, output_summary=type(exc).__name__,
                        status="failure", started=started)
            return None

    def _bind_rules(self, state):
        for issue in state.issues:
            candidates = [r for r in state.rules if r["issue_type"] == issue["issue_type"]]
            candidates.sort(key=lambda r: r["category"] != state.category)
            ids = {r["rule_id"] for r in candidates}
            if issue.get("rule_id") not in ids:
                if candidates:
                    issue["rule_id"] = candidates[0]["rule_id"]
                elif issue.get("rule_id") in {r.get("rule_id") for r in state.rules}:
                    # Category checks may use an existing rule id whose wording
                    # differs from the local issue label; retain the valid ref.
                    continue
                else:
                    # Do not manufacture references when a retriever returned no
                    # applicable rules. Such reports remain valid and un-degraded.
                    issue["rule_id"] = None

    async def arun(self, product: ProductInput | dict, task_id: str) -> InspectionReport:
        started = time.perf_counter()
        normalized = product if isinstance(product, ProductInput) else ProductInput.model_validate(product)
        state = InspectionState(task_id, normalized.model_dump(), normalized.category or "", self.mode)
        state.optimized_title, state.optimized_description = normalized.title, normalized.description
        self._trace(state, "normalize_input", output_summary="输入校验通过", started=started)
        if self.mode == "full" and not self.llm.enabled:
            state.warnings.append("未配置DeepSeek API Key，本次使用确定性规则；语义判断和模型摘要未执行。")
        started = time.perf_counter()
        if not state.category:
            if self.mode == "full" and self.llm.enabled:
                try:
                    decision = await asyncio.to_thread(self.llm.generate_json, "category", {"product": state.product}, CategoryDecision)
                    state.category = decision.category
                    self._trace(state, "category_identification", tool_name="deepseek.category",
                                output_summary=state.category, started=started)
                except Exception as exc:
                    state.warnings.append(f"模型类目识别失败（{type(exc).__name__}），改用明确属性与关键词。")
                    self._trace(state, "category_identification", tool_name="deepseek.category",
                                output_summary=type(exc).__name__, status="failure", started=started)
            if not state.category:
                state.category = infer_category(state.product)
            state.product["category"] = state.category
        category_skill = self.category_router.route(state.context())
        self._trace(state, "category_router", output_summary=f"selected={category_skill.name}", started=started)

        started = time.perf_counter()
        query = f"{normalized.title} {normalized.description}"
        try:
            state.retrieved_rules = await asyncio.to_thread(self.retriever.retrieve, state.category, query, top_k=5)
            retrieval_warning = self.retriever.last_warning
            state.rules = await asyncio.to_thread(self.retriever.list_rules, state.category)
            state.retrieval_source = self.retriever.last_source
            for warning in (retrieval_warning, self.retriever.last_warning):
                if warning and warning not in state.warnings:
                    state.warnings.append(warning)
        except Exception as exc:
            state.retrieval_source = "unavailable"
            retrieval_warning = f"规则依据检索失败（{type(exc).__name__}），保留内置规则结果，部分问题可能缺少依据引用。"
            state.warnings.append(retrieval_warning)
        self._trace(state, "rule_retriever", tool_name=f"rules.{state.retrieval_source}", started=started,
                    output_summary=f"top_k={[r['rule_id'] for r in state.retrieved_rules]}, applicable={len(state.rules)}",
                    status="degraded" if retrieval_warning or getattr(self.retriever, "last_warning", "") else "success")
        # LLM leads the semantic decision from original input and policy. It
        # cannot see/merely echo a pre-computed rules report. Deterministic
        # checks subsequently add mandatory safeguards; they cannot be vetoed.
        await self._execute_skill(state, SemanticRiskSkill())
        for skill in self.general_skills:
            await self._execute_skill(state, skill)
        await self._execute_skill(state, category_skill)
        # A category-specific claim covers its generic medical token; score it once.
        state.issues = [issue for issue in state.issues if not (
            issue["issue_type"] == "医疗功效宣称" and any(
                other["issue_type"] == "误导性功效" and other["field"] == issue["field"]
                and issue.get("matched_text") and issue["matched_text"] in (other.get("matched_text") or "")
                for other in state.issues))]
        self._bind_rules(state)
        self._bind_rules(state)
        scoring = await self._execute_skill(state, self.scoring_skill)
        state.score_result = scoring["metadata"] if scoring else risk_score_calculator(state.issues)["metadata"]
        copy = await self._execute_skill(state, CopywritingOptimizationSkill())
        rewritten = copy["metadata"] if copy else safe_copy(state.context())
        for key in ("optimized_title", "optimized_description", "rewrite_reason"):
            setattr(state, key, rewritten[key])
        state.summary = f"{state.category}商品共发现{len(state.issues)}个问题，风险等级{state.score_result['risk_level']}，风险分{state.score_result['score']}。"
        if state.required_failed:
            state.summary = f"质检未完成：部分必需检查失败，已发现{len(state.issues)}个问题，当前评分仅供复核，不能视为通过。"
        summary = await self._execute_skill(state, ReportSummarySkill())
        if summary:
            state.summary = summary["metadata"]["summary"]
        report_result = await self._execute_skill(state, self.report_skill)
        if report_result:
            report = InspectionReport.model_validate(report_result["metadata"]["report"])
        else:
            context = state.context()
            fallback = await ReportGenerationSkill().run(context)
            report = InspectionReport.model_validate(fallback["metadata"]["report"])
        self._trace(state, "persist_trace", output_summary=f"events={len(state.traces)}, warnings={len(state.warnings)}")
        # Include warnings from the final trace sink as well.
        report.warnings = list(dict.fromkeys(state.warnings))
        if state.required_failed:
            report.status = "partial"
            report.summary = f"质检未完成：部分必需检查失败，已发现{len(state.issues)}个问题，当前评分仅供复核，不能视为通过。"
        report.degraded = bool(report.warnings)
        report.model_used = state.model_used
        return report

    def run(self, product, task_id):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.arun(product, task_id))
        raise RuntimeError("异步上下文请使用 await orchestrator.arun(product, task_id)")


def inspect_product(product: ProductInput, task_id: str, trace_sink: Callable | None = None,
                    *, mode: str = "rules", retriever=None, llm=None) -> InspectionReport:
    return AgentOrchestrator(trace_sink, mode=mode, retriever=retriever, llm=llm).run(product, task_id)


AgentState = InspectionState
