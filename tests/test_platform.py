"""Offline architecture contracts: no credentials or paid requests."""
import asyncio
import json
import logging
from services.observability import JsonFormatter
from scripts.generate_load import chunks


def test_ten_thousand_generation_is_bounded_and_balanced():
    plan = list(chunks(12001, ["a", "b", "c"], "scale"))
    assert sum(size for _, _, size in plan) == 12001
    assert max(size for _, _, size in plan) == 100
    assert len({batch for _, batch, _ in plan}) == len(plan)
    assert {tenant for tenant, _, _ in plan} == {"a", "b", "c"}


def test_json_logs_drop_messages_secrets_and_exception_text():
    record = logging.LogRecord("httpx", logging.ERROR, __file__, 1,
                               "Authorization: Bearer secret-key password=private", (), None)
    record.event, record.task_id = "test_event", "task_123"
    raw = JsonFormatter().format(record)
    assert "secret-key" not in raw and "private" not in raw
    assert json.loads(raw)["task_id"] == "task_123"


def test_llm_leads_and_cannot_veto_rule_findings():
    from agent.orchestrator import AgentOrchestrator
    from app.schemas import SemanticOutput
    from rag import RuleRetriever
    class Model:
        enabled = True
        optional_generations = False
        def generate_json(self, purpose, payload, schema):
            assert purpose == "semantic"
            assert payload["known_issues"] == []
            return SemanticOutput(issues=[])
    product = {"product_id": "test", "category": "食品", "title": "燕麦",
               "description": "本品治疗失眠", "attributes": {}}
    trace = []
    report = AgentOrchestrator(trace.append, mode="full", retriever=RuleRetriever(backend="local"), llm=Model()).run(product, "task_test")
    steps = [row["step_name"] for row in trace]
    assert steps.index("semantic_risk") < steps.index("risk_expression")
    assert report.risk_level == "high"


def test_tenant_context_is_task_local(monkeypatch):
    from services.tenancy import tenant_scope, tenant_id
    monkeypatch.setattr("services.tenancy.configurations", lambda: {"east": {}, "west": {}})
    async def call(name):
        with tenant_scope(name):
            await asyncio.sleep(0)
            return await asyncio.to_thread(tenant_id.get)
    async def both():
        return await asyncio.gather(call("east"), call("west"))
    assert asyncio.run(both()) == ["east", "west"]
    assert tenant_id.get() == "default"


def test_chunked_body_limit_rejects_before_downstream(monkeypatch):
    from app.body_limit import BodyLimitMiddleware
    monkeypatch.setenv("APP_MAX_BODY_BYTES", "8")
    called, sent = [], []
    async def downstream(*args):
        called.append(True)
    messages = [{"type": "http.request", "body": b"12345", "more_body": True},
                {"type": "http.request", "body": b"67890", "more_body": False}]
    async def receive():
        return messages.pop(0)
    async def send(message):
        sent.append(message)
    asyncio.run(BodyLimitMiddleware(downstream)({"type": "http"}, receive, send))
    assert not called and sent[0]["status"] == 413
