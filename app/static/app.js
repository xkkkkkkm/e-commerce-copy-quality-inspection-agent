"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const t = (source, params) => window.I18n.t(source, params);
  const liveText = new Map();
  const errorMessages = new Map();
  const riskNames = { high: "高风险", medium: "中风险", low: "低风险", pass: "通过" };
  const statusNames = { success: "成功", completed: "完成", partial: "未完成", failed: "失败", failure: "失败", error: "失败", skipped: "已跳过", degraded: "已降级", running: "进行中", fallback: "备用处理", passed: "通过" };
  const samples = {
    high: { product_id: "demo_food_high", category: "食品", title: "清风草本花茶 失眠克星", description: "本产品可治疗失眠，纯天然无副作用。每盒 20 袋，独立包装。", attributes: { brand: "清风", origin: "杭州", shelf_life: "12个月", ingredients: "菊花、枸杞", storage: "阴凉干燥处" } },
    medium: { product_id: "demo_beauty_medium", category: "美妆", title: "润颜保湿面膜 5片装", description: "贴合面部肌肤，日常保湿护理。每盒 5 片，独立包装。", attributes: { brand: "润颜", ingredients: "水、甘油", skin_type: "干性肌肤", usage: "洁面后敷于面部约15分钟" } },
    pass: { product_id: "demo_3c_pass", category: "3C", title: "星点便携充电器 65W 双口快充", description: "品牌：星点。型号：C65。双 USB-C 接口，额定总输出功率 65W。适用于支持 PD 协议的设备，具体充电功率以设备支持情况为准。", attributes: { brand: "星点", model: "C65", specifications: "65W，双 USB-C 接口", compatibility: "支持 PD 协议的设备", warranty: "12个月" } },
  };
  let lastReport = null;
  let lastProduct = null;
  let lastTraces = null;
  let lastEvaluation = null;
  let traceTaskId = null;
  let tracePromise = null;
  let inspectBusy = false;
  let evaluationBusy = false;
  let toastTimer = null;
  let lastReportMode = "rules";
  let lastEvaluationMode = "rules";

  function localized(id, source, params) {
    const render = typeof source === "function" ? source : () => t(source, params);
    liveText.set(id, render);
    $(id).textContent = render();
  }
  function errorText(error) {
    const message = error && error.message ? error.message : error;
    const requestFailure = /^请求未完成（HTTP (\d+)），请稍后重试。$/.exec(message || "");
    if (requestFailure) return t("请求未完成（HTTP {status}），请稍后重试。", { status: requestFailure[1] });
    return window.I18n.error(error && error.message ? error.message : error, error && error.status);
  }
  function categoryLabel(value) { return t(window.I18n.category(value || "自动识别")); }
  function explanation(source, issue) {
    if (!source) return "";
    if (window.I18n.getLocale() !== "en") return source;
    const templates = [
      [/^(.+)执行失败（([^）]+)），本次结果可能不完整。$/, "{skill}执行失败（{error}），本次结果可能不完整。", ["skill", "error"]],
      [/^Trace写入失败（([^）]+)）。$/, "Trace写入失败（{error}）。", ["error"]],
      [/^模型类目识别失败（([^）]+)），改用明确属性与关键词。$/, "模型类目识别失败（{error}），改用明确属性与关键词。", ["error"]],
      [/^规则依据检索失败（([^）]+)），保留内置规则结果，部分问题可能缺少依据引用。$/, "规则依据检索失败（{error}），保留内置规则结果，部分问题可能缺少依据引用。", ["error"]],
      [/^文案优化模型不可用或输出未通过校验（([^）]+)），已使用保守删除方案。$/, "文案优化模型不可用或输出未通过校验（{error}），已使用保守删除方案。", ["error"]],
      [/^(\d+)\/(\d+)条报告标记了成功模型使用；需结合degraded和Trace判断其余模型步骤是否成功。$/, "{used}/{count}条报告标记了成功模型使用；需结合degraded和Trace判断其余模型步骤是否成功。", ["used", "count"]],
    ];
    for (const [pattern, template, keys] of templates) {
      const match = pattern.exec(source);
      if (match) return t(template, Object.fromEntries(keys.map((key, index) => [key, match[index + 1]])));
    }
    if (/^(?:elasticsearch|mysql|local) 规则检索不可用(?:; (?:elasticsearch|mysql|local) 规则检索不可用)*$/.test(source)) {
      return source.split("; ").map((part) => t("{source} 规则检索不可用", { source: part.split(" ")[0] })).join("; ");
    }
    return /[\u3400-\u9fff]/u.test(source) ? window.I18n.suggestion(source, issue) : source;
  }

  function text(value, fallback = "—") {
    if (value === null || value === undefined || value === "") return fallback;
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = text(content);
    return node;
  }
  function riskTag(risk) {
    const knownRisk = Object.prototype.hasOwnProperty.call(riskNames, risk) ? risk : "";
    return element("span", `risk-tag ${knownRisk}`, window.I18n.risk(risk) || text(risk));
  }
  function setError(id, message) {
    errorMessages.set(id, message);
    $(id).textContent = typeof message === "function" ? message() : message ? errorText(message) : "";
    $(id).hidden = !message;
  }
  function showToast(message) {
    localized("toast", message);
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $("toast").hidden = true; }, 2800);
  }
  function updateCounter() { $("title-counter").textContent = `${$("product-title").value.length} / 500`; }
  function markEdited() {
    updateCounter();
    if (lastReport) $("stale-notice").hidden = false;
    document.querySelectorAll("[data-sample]").forEach((button) => {
      button.classList.remove("selected");
      button.setAttribute("aria-pressed", "false");
    });
  }
  function applySample(key) {
    const sample = samples[key];
    $("product-id").value = sample.product_id;
    $("category").value = sample.category;
    window.SelectControls?.refresh($("product-form"));
    $("product-title").value = sample.title;
    $("description").value = sample.description;
    $("attributes").value = JSON.stringify(sample.attributes, null, 2);
    markEdited();
    document.querySelectorAll("[data-sample]").forEach((button) => {
      const selected = button.dataset.sample === key;
      button.classList.toggle("selected", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
    setError("form-error", "");
  }
  function parseAttributes() {
    let attributes;
    try { attributes = JSON.parse($("attributes").value || "{}"); }
    catch { throw new Error("商品属性的 JSON 格式有误，请检查双引号、逗号和括号。"); }
    if (!attributes || Array.isArray(attributes) || typeof attributes !== "object") {
      throw new Error("商品属性需要是 JSON 对象，例如 {\"brand\": \"品牌名称\"}。");
    }
    return attributes;
  }
  function readProduct() {
    const product = { product_id: $("product-id").value.trim(), category: $("category").value || null, title: $("product-title").value.trim(), description: $("description").value.trim(), attributes: parseAttributes() };
    if (!product.product_id) throw new Error("请填写商品 ID。");
    if (!product.title) throw new Error("请填写商品标题。");
    return product;
  }
  function apiError(body, status) {
    if (body && Array.isArray(body.detail)) {
      return body.detail.map((item) => `${(item.loc || []).filter((part) => part !== "body").join(".")}：${text(item.msg, "输入无效")}`).join("；");
    }
    if (body && typeof body.detail === "string") return body.detail;
    if (body && typeof body.message === "string") return body.message;
    return `请求未完成（HTTP ${status}），请稍后重试。`;
  }
  async function request(path, options = {}, timeoutMs = 180000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      if (options.method && !["GET", "HEAD"].includes(options.method.toUpperCase()) && !options.headers?.["X-CSRF-Token"]) {
        const sessionResponse = await fetch("/api/admin/auth/me", { signal: controller.signal });
        if (sessionResponse.status === 401) throw new Error(t("请先登录管理后台"));
        const session = await sessionResponse.json();
        options.headers = { ...options.headers, "X-CSRF-Token": session.csrf_token };
      }
      const response = await fetch(path, { ...options, signal: controller.signal, headers: { Accept: "application/json", ...options.headers } });
      const raw = await response.text();
      let body = null;
      try { body = raw ? JSON.parse(raw) : null; } catch { /* HTTP errors may be HTML. */ }
      if (!response.ok) {
        const error = new Error(apiError(body, response.status));
        error.status = response.status;
        throw error;
      }
      if (body === null) throw new Error("服务返回了无法识别的结果，请稍后重试。");
      return body;
    } catch (error) {
      if (error.name === "AbortError") throw new Error("等待响应超时，服务可能仍在处理。请稍后重试，或选择规则模式。");
      if (error instanceof TypeError) throw new Error("暂时无法连接服务，请检查网络和服务状态后重试。");
      throw error;
    } finally { clearTimeout(timer); }
  }
  function setInspectBusy(busy) {
    inspectBusy = busy;
    $("inspect-button").disabled = busy;
    $("reset-form").disabled = busy;
    $("format-json").disabled = busy;
    document.querySelectorAll("#product-form input, #product-form textarea, #product-form select, [data-sample]").forEach((node) => { node.disabled = busy; });
    localized("inspect-button-label", busy ? "质检中…" : "开始质检");
    $("download-report").disabled = busy || !lastReport;
    $("loading-state").hidden = !busy;
    $("empty-state").hidden = busy || Boolean(lastReport);
    $("report-content").hidden = busy || !lastReport;
    document.querySelector(".report-panel").setAttribute("aria-busy", String(busy));
  }
  function activateTab(name) {
    document.querySelectorAll("[data-tab]").forEach((button) => {
      const selected = button.dataset.tab === name;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      $(`panel-${button.dataset.tab}`).hidden = !selected;
    });
  }
  function renderIssues(issues, { partial = false, limited = false } = {}) {
    const list = $("issues-list");
    list.replaceChildren();
    if (!issues.length) {
      const incomplete = partial || limited;
      const empty = element("div", `no-issues${incomplete ? " incomplete" : ""}`);
      empty.append(element("span", "check-circle", incomplete ? "!" : "✓"), element("strong", "", t(partial ? "检查未完成，请人工复核" : limited ? "已完成的规则检查未发现问题" : "本次检查未发现问题")), element("p", "", t(incomplete ? "部分检查未完成，请查看提示与执行过程，不能据此判断完整检查已通过。" : "请继续核实商品信息与实际情况是否一致。")));
      list.append(empty);
      return;
    }
    issues.forEach((issue, index) => {
      const card = element("article", "issue-card");
      const heading = element("div", "issue-heading");
      heading.append(element("h4", "", `${String(index + 1).padStart(2, "0")}  ${window.I18n.issueLabel(issue.issue_type || "文案问题")}`), riskTag(issue.risk_level));
      card.append(heading, element("p", "issue-field", t(window.I18n.field(issue.field || "商品信息"))), element("p", "issue-evidence", issue.evidence), element("p", "issue-suggestion", t("建议：{suggestion}", { suggestion: explanation(issue.suggestion || "结合商品实际情况核对后修改。", issue) })));
      if (issue.rule_id) card.append(element("p", "issue-rule", t("规则 {id}", { id: issue.rule_id })));
      list.append(card);
    });
  }
  function renderRules(rules) {
    const list = $("rules-list");
    list.replaceChildren();
    if (!rules.length) {
      list.append(element("p", "no-issues", t("本次报告未返回规则引用。可查看问题建议与执行过程。")));
      return;
    }
    rules.forEach((rule) => {
      const card = element("article", "rule-card");
      const heading = element("div", "rule-heading");
      heading.append(element("h4", "rule-id", rule.rule_id || rule.id || t("规则依据")), element("span", "rule-category", categoryLabel(rule.category || "通用")));
      card.append(heading, element("p", "", window.I18n.ruleText(rule.rule_text || rule.content || rule.description || rule.rule_name)));
      if (rule.rewrite_hint) card.append(element("p", "rule-hint", t("修改参考：{hint}", { hint: explanation(rule.rewrite_hint) })));
      list.append(card);
    });
  }
  function renderReport(report, product, mode, preserveView = false) {
    const issues = Array.isArray(report.issues) ? report.issues : [];
    const risk = Object.prototype.hasOwnProperty.call(riskNames, report.risk_level) ? report.risk_level : "";
    const partial = report.status === "partial";
    const ruleUnavailable = report.retrieval_source === "unavailable" || !Array.isArray(report.rules) || report.rules.length === 0;
    const limited = partial || ruleUnavailable || ((report.mode || mode) === "full" && report.degraded);
    $("report-banner").className = `report-banner ${partial || (limited && risk === "pass") ? "medium" : risk}`;
    $("risk-label").textContent = limited ? t("检查受限，请复核") : t(riskNames[risk] || "检查完成");
    $("risk-score").textContent = text(report.score);
    $("risk-score-caption").textContent = t(partial ? "暂定风险分 · 需复核" : "风险分 · 越低越好");
    $("report-summary").textContent = `${limited ? t("规则依据或必需检查未完整可用，当前结果需复核。") : ""}${window.I18n.reportSummary(report)}`;
    $("report-category").textContent = t("类目：{category}", { category: categoryLabel(report.category || product.category) });
    $("report-mode").textContent = [t((report.mode || mode) === "full" ? "完整模式" : "规则模式"), report.degraded ? t("使用备用处理") : "", report.rule_version ? t("规则版本 {version}", { version: report.rule_version }) : ""].filter(Boolean).join(" · ");
    $("report-issue-count").textContent = t("{count} 项问题", { count: issues.length });
    $("issue-count").textContent = String(issues.length);
    $("task-id").textContent = text(report.task_id);
    $("optimized-title").textContent = text(report.optimized_title, t("未返回优化标题"));
    $("optimized-description").textContent = text(report.optimized_description, t("未返回优化描述"));
    $("rewrite-reason").textContent = explanation(report.rewrite_reason || (issues.length ? "根据本次发现的问题，调整风险表述并提示补充缺失信息。请结合真实商品信息核对。" : "本次检查未发现问题，文案可保留客观商品描述。"));
    const warnings = Array.isArray(report.warnings) ? [...report.warnings] : [];
    if (report.degraded && !warnings.length) warnings.push("部分分析暂时不可用，本报告已使用可用能力完成检查。可稍后重试完整模式。");
    $("report-warnings").replaceChildren();
    $("report-warnings").hidden = !warnings.length;
    if (warnings.length) {
      const list = element("ul");
      warnings.forEach((warning) => list.append(element("li", "", explanation(warning))));
      $("report-warnings").append(list);
    }
    renderIssues(issues, { partial, limited });
    renderRules(Array.isArray(report.rules) ? report.rules : []);
    if (preserveView) return;
    $("stale-notice").hidden = true;
    $("traces-list").replaceChildren();
    localized("trace-count", "按需加载");
    $("trace-details").open = false;
    $("reload-traces").disabled = false;
    setError("trace-error", "");
    activateTab("issues");
  }
  async function inspect(event) {
    event.preventDefault();
    if (inspectBusy) return;
    setError("form-error", "");
    let product;
    try { product = readProduct(); }
    catch (error) { setError("form-error", error); return; }
    const mode = document.querySelector('input[name="mode"]:checked').value;
    localized("loading-note", mode === "full" ? "正在结合规则依据与语义分析，完整模式可能需要更长时间…" : "正在核对表达、属性与规则依据…");
    setInspectBusy(true);
    try {
      const report = await request(`/api/products/inspect?mode=${encodeURIComponent(mode)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(product) });
      if (report.status === "failed") throw new Error(report.summary || "本次质检未完成，请稍后重试。");
      if (!report.task_id || !Array.isArray(report.issues)) throw new Error("服务返回的报告不完整，请稍后重试。");
      lastReport = report;
      lastProduct = product;
      lastReportMode = mode;
      lastTraces = null;
      traceTaskId = null;
      tracePromise = null;
      renderReport(report, product, mode);
      showToast(report.status === "partial" ? "已返回部分结果，检查未完成，请复核" : "质检完成，报告已更新");
      if (window.matchMedia("(max-width: 850px)").matches) document.querySelector(".report-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) { setError("form-error", error); }
    finally { setInspectBusy(false); }
  }
  function renderTraces(traces) {
    const list = $("traces-list");
    list.replaceChildren();
    localized("trace-count", "{count} 个步骤", { count: traces.length });
    if (!traces.length) { list.append(element("li", "", t("暂未记录执行步骤。"))); return; }
    const stepNames = { normalize_input: "标准化输入", classify_category: "识别商品类目", category_identification: "识别商品类目", category_router: "选择类目检查", retrieve_rules: "检索规则依据", rule_retriever: "检索规则依据", persist_trace: "保存执行过程", inspection_failed: "质检失败", TitleQualitySkill: "标题质量检查", RiskExpressionSkill: "风险表达检查", RequiredAttributeSkill: "必填属性检查", FoodCategorySkill: "食品类目检查", BeautyCategorySkill: "美妆类目检查", ElectronicsCategorySkill: "数码类目检查", FoodQualitySkill: "食品类目检查", BeautyQualitySkill: "美妆类目检查", ElectronicsQualitySkill: "数码类目检查", RiskScoringSkill: "风险评分", ReportGenerationSkill: "生成报告", SemanticRiskSkill: "语义风险分析", CopywritingOptimizationSkill: "文案优化", ReportSummarySkill: "报告摘要" };
    traces.forEach((trace, index) => {
      const failed = ["failed", "failure", "error", "degraded", "fallback"].includes(trace.status);
      const item = element("li", failed ? "trace-failed" : "");
      const heading = element("div", "trace-title");
      const latency = typeof trace.latency_ms === "number" ? `${Math.round(trace.latency_ms)} ms` : "—";
      heading.append(element("strong", "", trace.step_name ? t(stepNames[trace.step_name] || trace.step_name) : t("步骤 {number}", { number: index + 1 })), element("span", "trace-time", `${t(statusNames[trace.status] || text(trace.status))} · ${latency}`));
      item.append(heading);
      const tools = [trace.skill_name, trace.tool_name].filter(Boolean);
      if (tools.length) item.append(element("p", "trace-tools", tools.join(" / ")));
      item.append(element("p", "trace-summary", t("输入：{value}", { value: traceSummary(trace.input_summary) })), element("p", "trace-summary", t("输出：{value}", { value: traceSummary(trace.output_summary) })));
      list.append(item);
    });
  }
  function traceSummary(value) {
    if (value && typeof value === "object") return text(value);
    const raw = text(value);
    // These are known trace templates, not product text or arbitrary JSON.
    if (["食品", "美妆", "3C"].includes(raw)) return window.I18n.category(raw);
    if (/^category=(食品|美妆|3C)$/.test(raw)) return `category=${window.I18n.category(raw.slice(9))}`;
    return t(raw);
  }
  async function loadTraces(force = false) {
    if (!lastReport) return [];
    const taskId = lastReport.task_id;
    if (tracePromise && traceTaskId === taskId) return tracePromise;
    if (!force && lastTraces !== null && traceTaskId === taskId) return lastTraces;
    traceTaskId = taskId;
    localized("trace-count", "加载中…");
    $("reload-traces").disabled = true;
    setError("trace-error", "");
    const promise = (async () => {
      try {
        const response = await request(`/api/tasks/${encodeURIComponent(taskId)}/traces`, {}, 30000);
        const traces = Array.isArray(response) ? response : response.traces;
        if (!Array.isArray(traces)) throw new Error("执行过程的响应格式无效，请重试。");
        if (lastReport && lastReport.task_id === taskId) { lastTraces = traces; renderTraces(traces); }
        return traces;
      } catch (error) {
        if (lastReport && lastReport.task_id === taskId) {
          setError("trace-error", () => t("{error} 点击「刷新」可重试。", { error: errorText(error) }));
          localized("trace-count", "加载失败");
        }
        throw error;
      } finally {
        if (lastReport && lastReport.task_id === taskId) { $("reload-traces").disabled = false; tracePromise = null; }
      }
    })();
    tracePromise = promise;
    return promise;
  }
  function downloadJson(value, filename) {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = element("a");
    link.href = url;
    link.download = filename.replace(/[^a-zA-Z0-9_.-]/g, "_");
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function downloadReport() {
    if (!lastReport || inspectBusy) return;
    const report = lastReport;
    const product = lastProduct;
    $("download-report").disabled = true;
    try {
      let traces = [];
      let traceError = null;
      try { traces = await loadTraces(); } catch (error) { traceError = error.message; }
      downloadJson({ exported_at: new Date().toISOString(), product, report, traces, ...(traceError ? { trace_error: traceError } : {}) }, `inspection_${report.task_id}.json`);
      showToast(traceError ? "报告已下载；执行过程获取失败，文件已注明原因" : "完整报告已下载");
    } finally { $("download-report").disabled = inspectBusy || !lastReport; }
  }
  async function copyRewrite() {
    if (!lastReport) return;
    const copy = `${t("商品标题")}\n${text(lastReport.optimized_title, "")}\n\n${t("商品描述")}\n${text(lastReport.optimized_description, "")}`;
    try {
      if (!navigator.clipboard) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(copy);
      showToast("优化文案已复制");
    } catch { showToast("浏览器暂不支持自动复制，请选择文案后手动复制"); }
  }
  function first(object, keys, fallback = null) {
    for (const key of keys) if (object && object[key] !== undefined && object[key] !== null) return object[key];
    return fallback;
  }
  function percent(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return text(value);
    return `${(number <= 1 ? number * 100 : number).toFixed(1)}%`;
  }
  function metric(label, value, description) {
    const card = element("div", "metric-card");
    card.append(element("span", "", t(label)), element("strong", "", value), element("small", "", t(description)));
    return card;
  }
  function categoryRows(byCategory) {
    if (Array.isArray(byCategory)) return byCategory;
    if (byCategory && typeof byCategory === "object") return Object.entries(byCategory).map(([category, values]) => ({ category, ...values }));
    return [];
  }
  function summarizeIssues(value) {
    if (!Array.isArray(value)) return value ? window.I18n.issueLabel(text(value)) : t("无");
    return value.map((issue) => window.I18n.issueLabel(typeof issue === "string" ? issue : issue.issue_type || issue.type || text(issue))).join(window.I18n.getLocale() === "en" ? ", " : "、") || t("无");
  }
  function renderEvaluation(result, mode, preserveView = false) {
    const summary = result.summary || {};
    const cases = Array.isArray(result.cases) ? result.cases : [];
    const count = first(summary, ["total_cases", "total", "case_count", "count"], cases.length);
    const failed = first(summary, ["error_count", "failed_cases"], 0);
    const mismatched = first(summary, ["failed_case_count"], null);
    const fullMode = (result.mode || (result.config || {}).mode || mode) === "full";
    const modelCount = summary.model_used_case_count;
    const degradedCount = summary.degraded_case_count;
    const modelNote = (result.config || {}).model_evaluation_note || (fullMode && modelCount === 0 ? "本次没有成功使用模型，不能作为真实模型效果评测。" : "");
    const modelDetails = [t(fullMode ? "完整模式评测。" : "规则模式基线。"), explanation(modelNote)];
    if (modelCount !== undefined) modelDetails.push(t("模型实际参与 {used} / {count} 条。", { used: modelCount, count }));
    if (degradedCount !== undefined) modelDetails.push(t("使用备用处理 {used} / {count} 条。", { used: degradedCount, count }));
    $("evaluation-model-note").textContent = modelDetails.filter(Boolean).join(" ");
    $("evaluation-model-note").className = `notice ${fullMode && modelCount !== undefined && modelCount < count ? "warning" : "info"}`;
    $("evaluation-model-note").hidden = false;
    const metrics = $("evaluation-metrics");
    metrics.replaceChildren(
      metric("评测样例", text(count), [t("{count} 条运行失败", { count: failed }), mismatched === null ? "" : t("{count} 条未完全匹配", { count: mismatched })].filter(Boolean).join(" · ")),
      metric("风险等级准确率", percent(first(summary, ["risk_accuracy", "risk_level_accuracy", "accuracy"])), "实际风险等级与标注一致"),
      metric("问题召回率", percent(first(summary, ["issue_recall", "recall"])), "标注问题被发现的比例"),
      metric("误报占比", percent(summary.false_discovery_rate), "FP / (TP + FP)，越低越好"),
      metric("高风险精确率", percent(summary.high_risk_precision), "判为高风险中，标注也为高风险"),
      metric("高风险召回率", percent(summary.high_risk_recall), "标注高风险中被正确发现的比例"),
      metric("结构化输出成功率", percent(summary.structured_output_success_rate), "输出报告通过格式校验的比例"),
      metric("平均耗时", summary.average_latency_ms == null ? "—" : `${Math.round(Number(summary.average_latency_ms))} ms`, "每条样例的平均处理时间"),
    );
    const categoryBody = $("category-results");
    categoryBody.replaceChildren();
    categoryRows(result.by_category).forEach((row) => {
      const tr = element("tr");
      tr.append(element("td", "", categoryLabel(row.category)), element("td", "", first(row, ["total_cases", "total", "case_count", "count"])), element("td", "", percent(first(row, ["risk_accuracy", "risk_level_accuracy", "accuracy"]))), element("td", "", percent(first(row, ["issue_recall", "recall"]))));
      categoryBody.append(tr);
    });
    if (!categoryBody.children.length) { const tr = element("tr"); const td = element("td", "", t("本次评测未提供分品类统计。")); td.colSpan = 4; tr.append(td); categoryBody.append(tr); }
    const caseBody = $("case-results");
    caseBody.replaceChildren();
    cases.forEach((item) => {
      const expected = item.expected || {};
      const actual = item.actual || item.report || {};
      const expectedRisk = first(item, ["expected_risk_level", "expected_risk"], expected.risk_level);
      const actualRisk = first(item, ["actual_risk_level", "actual_risk", "risk_level", "predicted_risk_level"], actual.risk_level);
      const failedCase = item.status === "failed" || Boolean(item.error);
      const match = first(item, ["risk_match", "risk_correct", "risk_level_match", "matched"], expectedRisk && actualRisk ? expectedRisk === actualRisk : null);
      const hasMismatch = item.failed === true || match === false;
      const tr = element("tr");
      const product = element("td");
      product.append(element("span", "case-product-title", item.title || item.product_id || item.case_id), element("span", "case-product-meta", `${text(item.product_id || item.case_id)} · ${categoryLabel(item.category)}`));
      const expectedCell = element("td"); expectedCell.append(riskTag(expectedRisk));
      const actualCell = element("td"); actualCell.append(riskTag(actualRisk));
      const details = element("td", "case-detail");
      if (failedCase) details.textContent = errorText(item.error || item.error_message || "运行失败");
      else {
        const expectedIssues = first(item, ["expected_issues", "expected_issue_types"], expected.issues);
        const actualIssues = first(item, ["actual_issues", "actual_issue_types", "predicted_issues"], actual.issues);
        details.append(element("div", "", t("预期：{issues}", { issues: summarizeIssues(expectedIssues) })), element("div", "", t("实际：{issues}", { issues: summarizeIssues(actualIssues) })));
        if (Array.isArray(item.missing_issues) && item.missing_issues.length) details.append(element("div", "mismatch-label", t("漏检：{issues}", { issues: summarizeIssues(item.missing_issues) })));
        if (Array.isArray(item.false_positive_issues) && item.false_positive_issues.length) details.append(element("div", "mismatch-label", t("误报：{issues}", { issues: summarizeIssues(item.false_positive_issues) })));
      }
      tr.append(product, expectedCell, actualCell, element("td", !hasMismatch && !failedCase ? "match-label" : "mismatch-label", t(failedCase ? "运行失败" : hasMismatch ? "存在偏差" : match === null ? "—" : "匹配")), details);
      caseBody.append(tr);
    });
    if (!cases.length) { const tr = element("tr"); const td = element("td", "", t("本次评测未提供逐条结果。")); td.colSpan = 5; tr.append(td); caseBody.append(tr); }
    $("evaluation-run-info").textContent = `${t("运行 ID {id}", { id: text(result.run_id) })} · ${t(fullMode ? "完整模式" : "规则模式")}${result.generated_at ? ` · ${text(result.generated_at)}` : ""}`;
    if (preserveView) return;
    $("evaluation-results").hidden = false;
    localized("evaluation-status", "评测完成，共 {count} 条样例。指标按每条样例的问题类型去重计算；无可计算分母时显示「—」。", { count });
  }
  async function runEvaluation() {
    if (evaluationBusy) return;
    evaluationBusy = true;
    const mode = $("evaluation-mode").value;
    $("run-evaluation").disabled = true;
    $("load-latest-evaluation").disabled = true;
    $("evaluation-mode").disabled = true;
    localized("run-evaluation", "评测运行中…");
    localized("evaluation-status", mode === "full" ? "正在逐条运行完整评测，语义分析可能需要几分钟，请稍候…" : "正在对照标注样例运行规则评测，请稍候…");
    $("evaluation-status").classList.add("running");
    $("evaluation-results").hidden = true;
    setError("evaluation-error", "");
    try {
      const session = await request("/api/admin/auth/me", {}, 30000);
      if (!session.csrf_token) throw new Error("管理员会话未返回安全令牌，请重新登录管理后台。");
      const result = await request(`/api/evaluations/run?mode=${encodeURIComponent(mode)}`, { method: "POST", headers: { "X-CSRF-Token": session.csrf_token } }, mode === "full" ? 900000 : 180000);
      if (!result.summary) throw new Error("服务返回的评测报告不完整，请稍后重试。");
      lastEvaluation = result;
      lastEvaluationMode = mode;
      renderEvaluation(result, mode);
      showToast("评测完成，结果已更新");
    } catch (error) {
      setError("evaluation-error", error);
      localized("evaluation-status", () => t("本次评测未完成，可以重新运行。") + (lastEvaluation ? " " + t("下方保留上一次成功结果。") : ""));
      if (lastEvaluation) $("evaluation-results").hidden = false;
    } finally {
      evaluationBusy = false;
      $("run-evaluation").disabled = false;
      $("load-latest-evaluation").disabled = false;
      $("evaluation-mode").disabled = false;
      localized("run-evaluation", "重新运行评测 ↗");
      $("evaluation-status").classList.remove("running");
    }
  }
  async function loadLatestEvaluation() {
    if (evaluationBusy) return;
    evaluationBusy = true;
    $("run-evaluation").disabled = true;
    $("load-latest-evaluation").disabled = true;
    localized("load-latest-evaluation", "加载中…");
    setError("evaluation-error", "");
    try {
      const result = await request("/api/evaluations/latest", {}, 30000);
      if (!result.summary) throw new Error("最近一次评测报告不完整，请重新运行评测。");
      lastEvaluation = result;
      lastEvaluationMode = (result.config || {}).mode || "rules";
      renderEvaluation(result, lastEvaluationMode);
      const completionText = liveText.get("evaluation-status");
      localized("evaluation-status", () => t("已加载最近一次结果。") + completionText());
    } catch (error) { setError("evaluation-error", error); }
    finally {
      evaluationBusy = false;
      $("run-evaluation").disabled = false;
      $("load-latest-evaluation").disabled = false;
      localized("load-latest-evaluation", "查看最近结果");
    }
  }
  $("product-form").addEventListener("submit", inspect);
  $("product-form").addEventListener("input", markEdited);
  $("product-form").addEventListener("change", markEdited);
  document.querySelectorAll("[data-sample]").forEach((button) => button.addEventListener("click", () => { applySample(button.dataset.sample); showToast("样例已填入，点击「开始质检」运行"); }));
  $("reset-form").addEventListener("click", () => { $("product-form").reset(); $("attributes").value = "{}"; markEdited(); setError("form-error", ""); $("product-id").focus(); });
  $("format-json").addEventListener("click", () => { try { $("attributes").value = JSON.stringify(parseAttributes(), null, 2); setError("form-error", ""); showToast("JSON 已格式化"); } catch (error) { setError("form-error", error); } });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const tabs = [...document.querySelectorAll("[data-tab]")];
      const index = tabs.indexOf(button);
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      activateTab(tabs[next].dataset.tab);
      tabs[next].focus();
    });
  });
  $("trace-details").addEventListener("toggle", () => { if ($("trace-details").open && lastTraces === null) loadTraces().catch(() => {}); });
  $("reload-traces").addEventListener("click", () => loadTraces(true).catch(() => {}));
  $("download-report").addEventListener("click", downloadReport);
  $("copy-rewrite").addEventListener("click", copyRewrite);
  $("run-evaluation").addEventListener("click", runEvaluation);
  $("load-latest-evaluation").addEventListener("click", loadLatestEvaluation);
  $("download-evaluation").addEventListener("click", () => { if (lastEvaluation) downloadJson(lastEvaluation, `evaluation_${lastEvaluation.run_id || "report"}.json`); });
  window.I18n.onChange(() => {
    // Rebuild presentation only: form values, API payloads, active tabs, and
    // completed reports stay intact, including an input's stale-report notice.
    if (lastReport) renderReport(lastReport, lastProduct, lastReportMode, true);
    if (lastEvaluation) renderEvaluation(lastEvaluation, lastEvaluationMode, true);
    if (lastTraces !== null && !tracePromise) {
      const traceLabel = liveText.get("trace-count");
      renderTraces(lastTraces);
      if (traceLabel) liveText.set("trace-count", traceLabel);
    }
    liveText.forEach((render, id) => { $(id).textContent = render(); });
    errorMessages.forEach((message, id) => setError(id, message));
  });
  localized("inspect-button-label", "开始质检");
  localized("loading-note", "正在核对表达、属性与规则依据…");
  localized("trace-count", "按需加载");
  localized("load-latest-evaluation", "查看最近结果");
  localized("run-evaluation", "运行评测 ↗");
  localized("evaluation-status", "运行后显示整体指标、分品类结果和逐条明细。");
  window.I18n.apply();
  document.querySelector('input[name="mode"][value="full"]').checked = true;
  applySample("high");
})();
