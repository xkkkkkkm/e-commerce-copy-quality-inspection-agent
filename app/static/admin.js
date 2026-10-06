"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  // Bind UI messages in place so changing language never rebuilds an open form.
  const bindings = new WeakMap();
  const renderedMessages = new Map();
  class UIMessage {
    constructor(render) { this.render = render; }
    toString() {
      const value = String(this.render());
      renderedMessages.set(value, this);
      if (renderedMessages.size > 500) renderedMessages.delete(renderedMessages.keys().next().value);
      return value;
    }
  }
  const t = (source, params) => new UIMessage(() => I18n.t(source, params));
  const localized = (render) => new UIMessage(render);
  function setText(node, value) {
    bindings.delete(node);
    node.removeAttribute("data-i18n");
    node.toggleAttribute("data-ui-message", value instanceof UIMessage);
    if (value instanceof UIMessage) bindings.set(node, value);
    node.textContent = text(value);
  }
  function setLabel(node, name, value) {
    node.setAttribute(name, String(value));
    if (value instanceof UIMessage) {
      node._localeAttributes ||= {};
      node._localeAttributes[name] = value;
      node.setAttribute("data-ui-attributes", "");
    }
  }
  const riskNames = { high: t("高风险"), medium: t("中风险"), low: t("低风险"), pass: t("通过") };
  const statusNames = { draft: t("草稿"), pending: t("待审核"), published: t("已发布"), rejected: t("已退回"), offline: t("已下架") };
  const actionNames = { create: t("新增商品"), update: t("编辑商品"), edit: t("编辑商品"), inspect: t("执行质检"), inspect_start: t("开始质检"), inspect_complete: t("质检完成"), inspect_failed: t("质检失败"), inspection_recalled: t("重检撤回发布"), publish: t("审核发布"), offline: t("下架商品"), reject: t("退回修改"), submit: t("提交审核") };
  const checkStatus = { success: t("已完成"), partial: t("检查未完成"), failed: t("检查失败"), error: t("检查失败"), running: t("质检中"), degraded: t("检查受限"), fallback: t("备用处理"), completed: t("已完成"), skipped: t("已跳过") };
  const attributeNames = { brand: t("品牌"), origin: t("产地"), shelf_life: t("保质期"), ingredients: t("成分 / 配料"), storage: t("储存方式"), skin_type: t("适用肤质"), usage: t("使用方法"), precautions: t("注意事项"), model: t("型号"), specifications: t("规格"), compatibility: t("兼容性"), warranty: t("保修期") };
  const state = { csrf: null, username: null, sessionGeneration: 0, items: [], total: 0, page: 1, pageSize: 20, status: "", selected: new Map(), detail: null, editor: null, action: null, report: null, loading: false, batchBusy: false, listRequest: 0, detailRequest: 0, editorRequest: 0, reportRequest: 0, toastTimer: null };
  const publication = { request: 0, stage: "idle", candidates: [], items: [], selected: new Set() };

  function text(value, fallback = "—") {
    if (value === undefined || value === null || value === "") return fallback;
    if (value instanceof UIMessage) return value.toString();
    return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  }
  function el(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) setText(node, content);
    return node;
  }
  function button(label, className, handler) {
    const node = el("button", className, label);
    node.type = "button";
    if (handler) node.addEventListener("click", handler);
    return node;
  }
  function badge(label, variant = "") {
    const allowed = new Set([...Object.keys(riskNames), ...Object.keys(statusNames), "success", "partial", "failed", "stale"]);
    return el("span", `badge ${allowed.has(variant) ? variant : ""}`, label);
  }
  function notice(message, variant = "info") { return el("p", `notice ${variant}`, variant === "error" ? errorText(message) : message); }
  function errorText(message, status) { return message instanceof UIMessage ? message : renderedMessages.get(message) || localized(() => I18n.error(message, status)); }
  function setError(id, message) { setText($(id), message ? errorText(message) : ""); $(id).hidden = !message; }
  function showToast(message) {
    setText($("toast"), errorText(message));
    $("toast").hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4500);
  }
  function dateParts(value) {
    const normalized = typeof value === "string" && /^\d{4}-\d\d-\d\d[T ]\d\d:\d\d/.test(value) && !/(Z|[+-]\d\d:\d\d)$/.test(value) ? `${value.replace(" ", "T")}Z` : value;
    const date = new Date(normalized);
    if (!value || Number.isNaN(date.getTime())) return ["—", ""];
    return [localized(() => date.toLocaleDateString(I18n.getLocale(), { year: "numeric", month: "2-digit", day: "2-digit" })), localized(() => date.toLocaleTimeString(I18n.getLocale(), { hour: "2-digit", minute: "2-digit", hour12: false }))];
  }
  function dateTime(value) { return localized(() => dateParts(value).join(" ").trim()); }
  function showDialog(id) {
    if (!$(id).open) { $(id).showModal(); $(id).scrollTop = 0; }
  }
  function closeDialog(id) {
    if (id === "batch-publish-dialog" && publication.stage === "submitting") return;
    $(id).close();
  }
  function showLogin(message = "") {
    state.csrf = null;
    state.username = null;
    state.sessionGeneration += 1;
    state.listRequest += 1;
    state.detailRequest += 1;
    state.reportRequest += 1;
    state.selected.clear();
    state.batchBusy = false;
    publication.request += 1; publication.stage = "idle"; publication.items = []; publication.selected.clear();
    $("batch-results").replaceChildren(); $("batch-results").hidden = true;
    document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
    $("app-view").hidden = true;
    $("login-view").hidden = false;
    $("login-password").value = "";
    setError("login-error", message);
  }
  function responseError(body, status) {
    if (Array.isArray(body?.detail)) return localized(() => body.detail.map((entry) => `${(entry.loc || []).filter((part) => part !== "body").map((part) => I18n.field(String(part))).join(".")}: ${I18n.error(text(entry.msg), status)}`).join("; "));
    const raw = typeof body?.detail === "string" ? body.detail : body?.detail?.message || body?.message;
    return raw ? errorText(raw, status) : t("请求未完成（HTTP {p0}），请稍后重试。", { p0: status });
  }
  async function api(path, { method = "GET", data, timeout = 180000, allowUnauthorized = false } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const headers = { Accept: "application/json" };
      if (data !== undefined) headers["Content-Type"] = "application/json";
      if (method !== "GET" && state.csrf) headers["X-CSRF-Token"] = state.csrf;
      const response = await fetch(`/api/admin${path}`, { method, headers, credentials: "same-origin", body: data === undefined ? undefined : JSON.stringify(data), signal: controller.signal });
      const bodyText = await response.text();
      let body;
      try { body = bodyText ? JSON.parse(bodyText) : null; } catch { body = null; }
      if (!response.ok) {
        const error = new Error(responseError(body, response.status));
        error.status = response.status;
        if (response.status === 401 && !allowUnauthorized) showLogin(t("登录已失效，请重新登录。"));
        if (response.status === 409) error.message = t("{p0} 商品可能已被更新，请刷新后重新打开编辑或操作。", { p0: errorText(error.message, 409) });
        throw error;
      }
      if (body === null) throw new Error(t("服务返回了无法识别的数据，请刷新后重试。"));
      return body;
    } catch (error) {
      if (error.name === "AbortError") throw new Error(t("等待响应超时，服务可能仍在处理。请刷新商品查看最新状态后再操作。"));
      if (error instanceof TypeError) throw new Error(t("暂时无法连接服务，请检查网络后重试。"));
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function activateSession(session) {
    state.sessionGeneration += 1;
    state.csrf = session.csrf_token;
    state.username = session.username;
    setText($("admin-name"), `${session.username} · ${session.tenant || "default"}`);
    document.querySelector(".avatar").textContent = (session.username || "A").slice(0, 1).toUpperCase();
    $("login-view").hidden = true;
    $("app-view").hidden = false;
    $("login-password").value = "";
    await refresh();
  }
  async function login(event) {
    event.preventDefault();
    $("login-submit").disabled = true;
    setText($("login-submit"), t("正在登录…"));
    setError("login-error", "");
    try {
      const session = await api(`/auth/login?tenant=${encodeURIComponent($("login-tenant").value.trim() || "default")}`, { method: "POST", data: { username: $("login-username").value.trim(), password: $("login-password").value }, allowUnauthorized: true });
      await activateSession(session);
    } catch (error) { setError("login-error", error.message); }
    finally { $("login-submit").disabled = false; setText($("login-submit"), t("登录管理后台 →")); }
  }
  async function logout() {
    $("logout").disabled = true;
    try {
      await api("/auth/logout", { method: "POST", data: {} });
      showLogin();
      $("admin-menu").open = false;
    } catch (error) { showToast(error.message); }
    finally { $("logout").disabled = false; }
  }
  async function loadSummary() {
    const summary = await api("/summary");
    if (!state.csrf) return;
    for (const key of ["total", "published", "pending", "high_risk"]) setText($(`metric-${key}`), localized(() => Number(summary[key] || 0).toLocaleString(I18n.getLocale())));
    setText($("metric-merchants"), text(summary.merchants, "0"));
    setText($("nav-total"), text(summary.total, "0"));
    setText($("nav-pending"), text(summary.pending, "0"));
    setText($("nav-high"), text(summary.high_risk, "0"));
  }
  async function loadProducts() {
    const requestId = ++state.listRequest;
    state.loading = true;
    $("list-loading").hidden = false;
    $("list-empty").hidden = true;
    $("products-body").replaceChildren();
    $("select-all").disabled = true;
    $("prev-page").disabled = true;
    $("next-page").disabled = true;
    setError("list-error", "");
    const params = new URLSearchParams({ page: state.page, page_size: state.pageSize });
    for (const [key, value] of Object.entries({ q: $("filter-q").value.trim(), category: $("filter-category").value, status: state.status, risk: $("filter-risk").value })) if (value) params.set(key, value);
    try {
      const result = await api(`/products?${params}`);
      if (requestId !== state.listRequest || !state.csrf) return;
      state.items = result.items || [];
      state.total = result.total || 0;
      if (state.page > 1 && !state.items.length && state.total > 0) { state.page = Math.ceil(state.total / state.pageSize); return loadProducts(); }
      renderProducts();
    } catch (error) {
      if (requestId !== state.listRequest || error.status === 401) return;
      state.items = [];
      setError("list-error", error.message);
      setText($("list-count"), t("加载未完成"));
    } finally {
      if (requestId === state.listRequest) { state.loading = false; $("list-loading").hidden = true; }
    }
  }
  async function refresh() {
    $("refresh-list").disabled = true;
    const results = await Promise.allSettled([loadSummary(), loadProducts()]);
    for (const result of results) if (result.status === "rejected" && result.reason.status !== 401) setError("list-error", result.reason.message);
    $("refresh-list").disabled = false;
  }
  function currentCheck(product) {
    if (product.inspection_status === "running") return { label: t("质检中"), variant: "partial", caption: t("正在检查 v{p0}", { p0: product.version }) };
    if (["failed", "error"].includes(product.inspection_status)) return { label: t("质检失败"), variant: "failed", caption: t("v{p0} · 请重试质检", { p0: product.version }) };
    if (product.inspection_status === "partial") return { label: t("检查未完成"), variant: "partial", caption: t("v{p0} · 需重新质检", { p0: product.version }) };
    if (!product.inspection_fresh) return { label: product.latest_task_id ? t("待重新质检") : t("待质检"), variant: "stale", caption: product.latest_task_id ? t("报告为 v{p0} · 已过期", { p0: product.inspected_version ?? "?" }) : t("尚无本版报告") };
    if (!product.inspection_complete) return { label: t("检查未完成"), variant: "partial", caption: t("结果受限 · 需重新质检") };
    return { label: riskNames[product.latest_risk] || t("待确认"), variant: product.latest_risk || "stale", caption: t("{p0} 项问题 · v{p1}", { p0: Number(product.issue_count || 0), p1: product.version }) };
  }
  function thumb(category) {
    return el("span", `product-thumb ${category === "美妆" ? "beauty" : category === "3C" ? "tech" : ""}`, category === "美妆" ? t("妆") : category === "3C" ? "C" : category === "食品" ? t("食") : t("物"));
  }
  function renderProducts() {
    const body = $("products-body");
    body.replaceChildren();
    for (const product of state.items) {
      const row = el("tr");
      const selectionCell = el("td", "check-cell");
      const checkbox = el("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(product.id); checkbox.disabled = state.batchBusy; setLabel(checkbox, "aria-label", t("选择商品 {p0}", { p0: product.title }));
      checkbox.addEventListener("change", () => {
        if (checkbox.checked && state.selected.size >= 20) { checkbox.checked = false; showToast(t("每批最多选择 20 件商品。")); return; }
        if (checkbox.checked) state.selected.set(product.id, { id: product.id, expected_version: product.version, title: product.title, status: product.status });
        else state.selected.delete(product.id);
        renderSelection();
      });
      selectionCell.append(checkbox);
      const productCell = el("td");
      const productInfo = el("div", "product-cell");
      const productCopy = el("div", "product-copy");
      const title = button(product.title, "product-title", () => openDetail(product.id)); title.title = product.title;
      productCopy.append(title, el("span", "product-meta", `${product.product_id} · v${product.version}`));
      productInfo.append(thumb(product.category), productCopy); productCell.append(productInfo);
      const merchantCell = el("td"); merchantCell.append(el("span", "merchant-name", product.merchant_name), el("span", "cell-caption", localized(() => I18n.category(product.category))));
      const statusCell = el("td"); statusCell.append(badge(statusNames[product.status] || product.status, product.status));
      const checkCell = el("td"); const check = currentCheck(product); checkCell.append(badge(check.label, check.variant), el("span", "cell-caption", check.caption));
      const updatedCell = el("td"); const date = dateParts(product.updated_at); updatedCell.append(el("span", "updated-date", date[0]), el("span", "cell-caption", date[1]));
      const actionsCell = el("td"); const actions = el("div", "row-actions");
      actions.append(button(t("查看"), "", () => openDetail(product.id)), button(t("编辑"), "", () => openEditor(product.id)), button(t("质检"), "", () => openDetail(product.id, true)));
      actionsCell.append(actions); row.append(selectionCell, productCell, merchantCell, statusCell, checkCell, updatedCell, actionsCell); body.append(row);
    }
    $("list-empty").hidden = state.items.length > 0;
    setText($("list-count"), t("共 {p0} 件商品", { p0: localized(() => state.total.toLocaleString(I18n.getLocale())) }));
    const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
    setText($("page-current"), `${state.page} / ${pages}`);
    setText($("pagination-info"), state.total ? t("显示第 {p0}–{p1} 条，共 {p2} 条", { p0: (state.page - 1) * state.pageSize + 1, p1: Math.min(state.page * state.pageSize, state.total), p2: state.total }) : t("共 0 条商品"));
    $("prev-page").disabled = state.page <= 1;
    $("next-page").disabled = state.page >= pages;
    renderSelection();
  }
  function renderSelection() {
    const current = state.items.filter((product) => state.selected.has(product.id)).length;
    $("select-all").checked = current > 0 && current === state.items.length;
    $("select-all").indeterminate = current > 0 && current < state.items.length;
    $("select-all").disabled = !state.items.length || state.batchBusy;
    setText($("selected-count"), state.selected.size);
    $("batch-bar").hidden = !state.selected.size;
    $("batch-inspect").disabled = !state.selected.size || state.batchBusy;
    $("batch-publish").disabled = !state.selected.size || state.batchBusy;
    $("batch-mode").disabled = state.batchBusy;
    $("clear-selection").disabled = state.batchBusy;
    $("batch-published-warning").hidden = ![...state.selected.values()].some((product) => product.status === "published");
  }
  function updateFilterTabs() {
    document.querySelectorAll("[data-status]").forEach((node) => {
      const active = node.dataset.status === state.status;
      node.classList.toggle("active", active); node.setAttribute("aria-selected", active);
    });
    document.querySelectorAll("[data-nav]").forEach((node) => node.classList.toggle("active", node.dataset.nav === ($( "filter-risk").value === "high" ? "high" : state.status === "pending" ? "pending" : "all")));
  }
  async function filterChanged() {
    window.SelectControls?.refresh($("filter-form"));
    state.page = 1; state.selected.clear(); updateFilterTabs(); renderSelection(); await loadProducts();
  }
  async function quickFilter(name) {
    $("filter-q").value = ""; $("filter-category").value = ""; $("filter-risk").value = name === "high" ? "high" : "";
    state.status = ["pending", "published"].includes(name) ? name : "";
    await filterChanged();
  }
  function detailSection(title, content) { const section = el("section", "detail-section"); section.append(el("h3", "", title), content); return section; }
  function attributeList(attributes) {
    const entries = Object.entries(attributes || {});
    if (!entries.length) return el("p", "subtle", t("未填写商品属性"));
    const list = el("dl", "attributes-list");
    entries.forEach(([key, value]) => list.append(el("dt", "", attributeNames[key] || key), el("dd", "", value)));
    return list;
  }
  function canPublish(product) {
    if (!["pending", "offline"].includes(product.status)) return t("当前状态不可发布，请先提交审核。");
    if (!product.inspection_fresh) return t("商品文案尚未完成当前版本质检，请先执行质检。");
    if (!product.inspection_complete) return t("当前质检未完整完成。完整模式若受限，请重新执行规则模式质检。");
    if (product.latest_risk === "high") return t("高风险商品无法发布，请修改文案并重新质检。");
    if (!Object.hasOwn(riskNames, product.latest_risk)) return t("质检结论无法确认，请重新执行质检。");
    return "";
  }
  async function openDetail(id, focusInspection = false) {
    const requestId = ++state.detailRequest;
    setText($("detail-title"), t("商品详情"));
    $("detail-content").replaceChildren(el("p", "subtle", t("正在加载商品详情…")));
    showDialog("detail-dialog");
    try {
      const product = await api(`/products/${id}`);
      if (requestId !== state.detailRequest || !state.csrf) return;
      state.detail = product;
      renderDetail(product);
      if (focusInspection) $("detail-inspect")?.focus();
    } catch (error) { if (requestId === state.detailRequest && error.status !== 401) $("detail-content").replaceChildren(notice(error.message, "error"), button(t("重试"), "button secondary", () => openDetail(id))); }
  }
  function renderDetail(product) {
    const root = $("detail-content"); root.replaceChildren();
    const identity = el("div", "detail-identity"); const identityText = el("div");
    identityText.append(el("h3", "", product.title), el("p", "detail-meta", t("{p0} · {p1} · {p2}", { p0: product.product_id, p1: product.merchant_name, p2: localized(() => I18n.category(product.category)) })));
    identity.append(thumb(product.category), identityText); root.append(identity);
    const labels = el("div", "detail-badges"); const check = currentCheck(product);
    labels.append(badge(statusNames[product.status], product.status), badge(t("当前版本 v{p0}", { p0: product.version })), badge(check.label, check.variant)); root.append(labels);
    if (product.inspection_status === "running") root.append(notice(t("当前版本正在质检，完成后请刷新查看结果。"), "info"));
    else if (["failed", "error", "partial"].includes(product.inspection_status)) root.append(notice(t("当前版本的质检未完整完成，请查看质检记录后重新执行检查。"), "warning"));
    else if (!product.inspection_fresh && product.latest_task_id) root.append(notice(t("当前文案为 v{p0}，已有报告对应 v{p1}，仅供历史参考。修改后的商品需要重新质检。", { p0: product.version, p1: product.inspected_version }), "warning"));
    else if (product.inspection_fresh && !product.inspection_complete) root.append(notice(t("本次检查未完整完成，不能据此发布。完整模式受限时，可重新执行规则模式。"), "warning"));
    if (product.status === "published") root.append(notice(t("重新质检会暂时撤回发布，完成后需重新确认发布。"), "warning"));
    const actions = el("div", "detail-actions");
    actions.append(button(t("编辑商品"), "button secondary", () => openEditor(product.id)));
    const mode = el("select"); mode.id = "detail-mode"; setLabel(mode, "aria-label", t("商品质检模式"));
    for (const [value, label] of [["rules", t("规则模式")], ["full", t("完整模式")]]) { const option = el("option", "", label); option.value = value; mode.append(option); }
    mode.value = "full";
    const inspect = button(product.status === "published" ? t("撤回并重新质检") : t("执行质检"), "button primary", () => inspectProduct(product, mode.value, inspect)); inspect.id = "detail-inspect";
    actions.append(mode, inspect);
    if (["pending", "offline"].includes(product.status)) {
      const publish = button(t("审核发布"), "button secondary", () => openAction(product, "publish")); const reason = canPublish(product); publish.disabled = Boolean(reason); if (reason) setLabel(publish, "title", reason); actions.append(publish);
    }
    if (product.status === "pending") actions.append(button(t("退回修改"), "button ghost", () => openAction(product, "reject")));
    if (product.status === "published") actions.append(button(t("下架商品"), "button ghost", () => openAction(product, "offline")));
    if (["draft", "rejected", "offline"].includes(product.status)) actions.append(button(t("提交审核"), "button secondary", () => openAction(product, "submit")));
    root.append(actions);
    if (["pending", "offline"].includes(product.status) && canPublish(product)) root.append(el("p", "field-help", canPublish(product)));
    const tabs = el("div", "detail-tabs"); tabs.setAttribute("role", "tablist"); setLabel(tabs, "aria-label", t("商品详情内容"));
    const panels = {};
    for (const [key, name] of [["copy", t("商品文案")], ["inspections", t("质检记录")], ["revisions", t("版本历史")], ["audits", t("操作记录")]]) {
      const panel = el("section"); panel.id = `detail-panel-${key}`; panel.hidden = key !== "copy"; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `detail-tab-${key}`); panels[key] = panel;
      const tab = button(name, `detail-tab${key === "copy" ? " active" : ""}`, () => {
        tabs.querySelectorAll("button").forEach((node) => { const active = node === tab; node.classList.toggle("active", active); node.setAttribute("aria-selected", active); });
        Object.entries(panels).forEach(([panelKey, node]) => { node.hidden = key !== panelKey; });
      }); tab.id = `detail-tab-${key}`; tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", key === "copy"); tab.setAttribute("aria-controls", panel.id); tabs.append(tab);
    }
    panels.copy.append(detailSection(t("商品标题"), el("div", "copy-block", product.title)), detailSection(t("商品描述"), el("div", "copy-block", product.description || t("未填写商品描述"))), detailSection(t("商品属性"), attributeList(product.attributes)), el("p", "record-meta", t("创建于 {p0} · 更新于 {p1}", { p0: dateTime(product.created_at), p1: dateTime(product.updated_at) })));
    renderInspectionHistory(panels.inspections, product);
    renderRevisionHistory(panels.revisions, product);
    renderAuditHistory(panels.audits, product);
    root.append(tabs, ...Object.values(panels));
  }
  function renderInspectionHistory(root, product) {
    const history = product.inspections || [];
    if (!history.length) { root.append(notice(t("还没有质检记录。选择规则模式或完整模式，开始第一次质检。"))); return; }
    history.forEach((inspection) => {
      const card = el("article", "record-card"); const heading = el("div", "record-heading");
      heading.append(el("h4", "", t("文案 v{p0} · {p1}", { p0: inspection.version, p1: inspection.mode === "full" ? t("完整模式") : t("规则模式") })), badge(inspection.status === "success" && !inspection.degraded ? t("已返回结果") : inspection.degraded ? t("检查受限") : checkStatus[inspection.status] || t("检查受限"), inspection.status === "success" && !inspection.degraded ? "" : "partial"));
      card.append(heading, el("p", "record-meta", t("{p0} · {p1} · {p2} 项问题", { p0: dateTime(inspection.created_at), p1: text(inspection.actor, t("管理员")), p2: inspection.issue_count || 0 })));
      if (inspection.version !== product.version) card.append(el("p", "record-meta", t("历史文案报告，不代表当前 v{p0} 的质检结果。", { p0: product.version })));
      if (inspection.degraded) card.append(el("p", "record-meta", t("本次检查受限或使用了备用处理。")));
      if (inspection.error_message) card.append(notice(inspection.error_message, "error"));
      if (inspection.task_id) card.append(button(t("查看报告与执行过程 ↗"), "text-button", () => openReport(product, inspection)));
      root.append(card);
    });
  }
  function renderRevisionHistory(root, product) {
    const history = product.revisions || [];
    if (!history.length) { root.append(el("p", "subtle", t("暂无版本记录。"))); return; }
    history.forEach((revision) => {
      const card = el("article", "record-card"); const heading = el("div", "record-heading"); const source = revision.product_json || revision.product || revision.snapshot || {};
      heading.append(el("h4", "", t("文案 v{p0}", { p0: revision.version })), revision.version === product.version ? badge(t("当前版本"), "published") : badge(t("历史版本")));
      card.append(heading, el("p", "record-meta", t("{p0} · {p1}", { p0: dateTime(revision.created_at), p1: text(revision.actor, t("管理员")) })), el("p", "", source.title));
      const details = el("details"); details.append(el("summary", "", t("查看此版本完整文案")), el("pre", "", source)); card.append(details); root.append(card);
    });
  }
  function renderAuditHistory(root, product) {
    const history = product.audits || [];
    if (!history.length) { root.append(el("p", "subtle", t("暂无操作记录。"))); return; }
    history.forEach((audit) => {
      const card = el("article", "record-card"); const heading = el("div", "record-heading");
      heading.append(el("h4", "", actionNames[audit.action] || audit.action), el("span", "record-meta", `v${audit.version}`));
      card.append(heading, el("p", "record-meta", t("{p0} · {p1}", { p0: dateTime(audit.created_at), p1: text(audit.actor, t("管理员")) })));
      if (audit.from_status || audit.to_status) card.append(el("p", "record-meta", t("{p0} → {p1}", { p0: statusNames[audit.from_status] || t("新建"), p1: statusNames[audit.to_status] || "—" })));
      card.append(el("p", "", audit.reason || t("未附加说明"))); root.append(card);
    });
  }
  function parseAttributes() {
    let attributes;
    try { attributes = JSON.parse($("edit-attributes").value || "{}"); } catch { throw new Error(t("商品属性 JSON 格式有误，请检查双引号、逗号和括号。")); }
    if (attributes === null || Array.isArray(attributes) || typeof attributes !== "object") throw new Error(t("商品属性需填写 JSON 对象，例如 {\"brand\": \"品牌名称\"}。"));
    return attributes;
  }
  function renderAttributeForm() {
    const root = $("attribute-fields");
    if (!root) return;
    let attributes;
    try { attributes = parseAttributes(); } catch (error) { setError("editor-error", error.message); return; }
    const categoryFields = {
      "食品": ["ingredients", "shelf_life", "storage", "specifications"],
      "美妆": ["ingredients", "skin_type", "usage", "precautions"],
      "3C": ["model", "specifications", "compatibility", "warranty"],
    };
    const hints = { brand: t("商品品牌"), origin: t("生产地 / 原产地"), ingredients: t("按包装填写真实配料或成分"), shelf_life: t("例如 12 个月"), storage: t("例如 阴凉干燥处保存"), specifications: t("例如 20 袋 / 盒"), skin_type: t("例如 干性肌肤"), usage: t("填写使用方式"), precautions: t("填写必要的使用提醒"), model: t("填写产品型号"), compatibility: t("适用设备 / 协议"), warranty: t("例如 12 个月") };
    root.replaceChildren();
    ["brand", "origin", ...(categoryFields[$("edit-category").value] || ["specifications"])].forEach((key) => {
      const label = el("label"); label.append(el("span", "", attributeNames[key]));
      const input = el("input"); input.type = "text"; input.value = text(attributes[key], ""); setLabel(input, "placeholder", hints[key] || t("按商品实际情况填写")); input.dataset.attribute = key;
      input.addEventListener("input", () => {
        try {
          const next = parseAttributes();
          if (input.value.trim()) next[key] = input.value.trim(); else delete next[key];
          $("edit-attributes").value = JSON.stringify(next, null, 2); setError("editor-error", "");
        } catch (error) { setError("editor-error", t("{p0} 请先修正高级属性，再编辑表单字段。", { p0: errorText(error.message) })); }
      });
      label.append(input); root.append(label);
    });
  }
  function initializeAttributes() {
    const heading = $("edit-attributes").previousElementSibling;
    const root = el("section", "attribute-editor"); root.append(el("h3", "", t("商品属性")), el("p", "field-help", t("按实际商品信息填写，有助于检查信息完整性。")));
    const fields = el("div", "attribute-fields"); fields.id = "attribute-fields"; root.append(fields); heading.before(root);
    const advanced = el("details", "advanced-attributes"); advanced.append(el("summary", "", t("更多属性 · 高级编辑")));
    heading.before(advanced); advanced.append(heading, $("edit-attributes"), $("attributes-help"));
    $("edit-attributes").addEventListener("change", renderAttributeForm);
    $("edit-category").addEventListener("change", renderAttributeForm);
  }
  async function openEditor(id = null, rewrite = null) {
    const requestId = ++state.editorRequest;
    const generation = state.sessionGeneration;
    setError("editor-error", ""); $("save-product").disabled = false;
    let product = null;
    try {
      if (id !== null) product = await api(`/products/${id}`);
      if (requestId !== state.editorRequest || generation !== state.sessionGeneration || !state.csrf) return;
      if (rewrite && (product?.latest_task_id !== rewrite.task_id || !product?.inspection_fresh)) { showToast(t("商品或质检任务已更新，请查看最新报告后再应用建议。")); return; }
    }
    catch (error) { if (error.status !== 401 && requestId === state.editorRequest && generation === state.sessionGeneration) showToast(error.message); return; }
    if (requestId !== state.editorRequest || generation !== state.sessionGeneration || !state.csrf) return;
    state.editor = product;
    setText($("editor-title"), product ? t("编辑商品 · v{p0}", { p0: product.version }) : t("新增商品"));
    $("edit-product-id").value = product?.product_id || ""; $("edit-product-id").disabled = Boolean(product);
    $("edit-merchant").value = product?.merchant_name || "";
    $("edit-category").value = product?.category || "";
    window.SelectControls?.refresh($("editor-form"));
    $("edit-title").value = rewrite?.optimized_title ?? product?.title ?? "";
    $("edit-description").value = rewrite?.optimized_description ?? product?.description ?? "";
    $("edit-attributes").value = JSON.stringify(product?.attributes || {}, null, 2);
    renderAttributeForm();
    document.querySelector(".advanced-attributes").open = false;
    setText($("editor-notice"), rewrite ? t("已将建议填入编辑草稿，请核实事实信息后保存。保存会产生新版本，需重新质检并审核发布。") : t("保存后进入待审核状态。商品修改会产生新版本，需重新质检并审核发布。"));
    showDialog("editor-dialog");
  }
  async function saveProduct(event) {
    event.preventDefault();
    if ($("save-product").disabled) return;
    setError("editor-error", "");
    const old = state.editor;
    let data;
    try {
      data = { product_id: $("edit-product-id").value.trim(), merchant_name: $("edit-merchant").value.trim(), category: $("edit-category").value, title: $("edit-title").value.trim(), description: $("edit-description").value.trim(), attributes: parseAttributes() };
      if (!data.product_id || !data.merchant_name || !data.title || !data.category) throw new Error(t("请填写商品 ID、所属商家、类目和商品标题。"));
      if (old) data.expected_version = old.version;
    } catch (error) { setError("editor-error", error.message); return; }
    $("save-product").disabled = true; setText($("save-product"), t("正在保存…"));
    let conflict = false;
    try {
      const product = await api(old ? `/products/${old.id}` : "/products", { method: old ? "PUT" : "POST", data });
      closeDialog("editor-dialog"); showToast(t("商品已保存为 v{p0}，请执行当前版本质检。", { p0: product.version }));
      state.selected.delete(product.id);
      if ($("detail-dialog").open && state.detail?.id === product.id) { state.detail = product; renderDetail(product); }
      await refresh();
    } catch (error) {
      if (error.status !== 401) setError("editor-error", error.message);
      if (error.status === 409) {
        // A duplicate product_id on creation is recoverable after editing the
        // field. Version conflicts on an existing product still require a
        // refresh before continuing.
        conflict = Boolean(old);
        await refresh();
      }
    } finally { $("save-product").disabled = conflict; setText($("save-product"), t("保存并待审核")); }
  }
  function openAction(product, action) {
    if (action === "publish" && canPublish(product)) { showToast(canPublish(product)); return; }
    state.action = { product, action };
    setText($("action-title"), actionNames[action]);
    setText($("action-product"), `${product.title} · v${product.version}`);
    $("action-reason").value = ""; $("action-reason").required = action !== "submit";
    $("reason-required").hidden = action === "submit";
    const messages = {
      publish: ["medium", "low"].includes(product.latest_risk) ? t("本版质检为{p0}，请确认已复核相关问题，并说明仍可发布的依据。发布仅更新本地演示平台状态。", { p0: riskNames[product.latest_risk] }) : t("本版质检已完成。请核实文案与实际商品一致，填写审核说明后发布。发布仅更新本地演示平台状态。"),
      reject: t("退回后，商品需修改并重新提交审核。请说明需要商家修正的内容。"),
      offline: t("此操作将商品标记为已下架，请记录下架原因。"),
      submit: t("商品将进入待审核状态；审核发布仍需有效的当前版本质检结果。"),
    };
    setText($("action-notice"), messages[action]); setText($("action-submit"), t("确认{p0}", { p0: actionNames[action] }));
    $("action-submit").className = `button ${["reject", "offline"].includes(action) ? "danger" : "primary"}`;
    $("action-submit").disabled = false; setError("action-error", ""); showDialog("action-dialog");
  }
  async function performAction(event) {
    event.preventDefault();
    if (!state.action || $("action-submit").disabled) return;
    const { product, action } = state.action; const reason = $("action-reason").value.trim();
    if (action !== "submit" && !reason) { setError("action-error", t("请填写本次操作的说明。")); return; }
    $("action-submit").disabled = true; setError("action-error", "");
    let conflict = false;
    try {
      const updated = await api(`/products/${product.id}/actions/${action}`, { method: "POST", data: { expected_version: product.version, expected_status: product.status, expected_inspection_id: action === "publish" ? product.latest_inspection_id : undefined, reason } });
      closeDialog("action-dialog"); state.detail = updated;
      if ($("detail-dialog").open) renderDetail(updated);
      showToast(t("{p0}成功，操作说明已记录。", { p0: actionNames[action] })); await refresh();
    } catch (error) {
      if (error.status !== 401) setError("action-error", error.message);
      if (error.status === 409) { conflict = true; await refresh(); }
    } finally { $("action-submit").disabled = conflict; }
  }
  async function inspectProduct(product, mode, trigger) {
    trigger.disabled = true; setText(trigger, t("正在质检…"));
    const controls = $("detail-content").querySelectorAll(".detail-actions button, .detail-actions select"); controls.forEach((node) => { node.disabled = true; });
    try {
      const result = await api(`/products/${product.id}/inspect`, { method: "POST", data: { expected_version: product.version, mode }, timeout: 300000 });
      if ($("detail-dialog").open && state.detail?.id === product.id) { state.detail = result.product; renderDetail(result.product); }
      showToast(result.applied ? result.product.inspection_complete ? t("质检已完成，当前版本结果已更新。") : t("检查未完整完成，请查看报告后重新质检。") : t("质检已记录；商品期间发生变化，请查看最新版本。") );
      await refresh();
      if (result.report && $("detail-dialog").open && state.detail?.id === product.id) renderReport(result.report, result.product, result.inspection);
    } catch (error) {
      if (error.status !== 401) {
        await openDetail(product.id);
        $("detail-content").prepend(notice(error.message, "error"));
        if (error.status === 409) await refresh();
      }
    }
  }
  async function batchInspect() {
    if (state.batchBusy || !state.selected.size) return;
    const selected = [...state.selected.values()];
    const mode = $("batch-mode").value;
    const generation = state.sessionGeneration;
    state.batchBusy = true; renderSelection();
    setText($("batch-inspect"), t("正在质检…"));
    $("products-body").querySelectorAll("input[type=checkbox]").forEach((node) => { node.disabled = true; });
    const resultsRoot = $("batch-results"); resultsRoot.hidden = false; resultsRoot.replaceChildren(el("strong", "", t("正在处理 {p0} 件商品，请稍候…", { p0: selected.length })));
    selected.forEach((item) => { const row = el("div", "batch-result-row"); row.append(el("span", "", item.title), el("span", "", t("等待批次完成"))); resultsRoot.append(row); });
    try {
      let job = await api("/jobs", { method: "POST", data: { items: selected.map(({ id, expected_version }) => ({ id, expected_version })), mode, idempotency_key: crypto.randomUUID() } });
      while (["queued", "running"].includes(job.status)) {
        if (generation !== state.sessionGeneration || !state.csrf) return;
        const finished = job.items.filter((item) => ["success", "failed", "cancelled"].includes(item.status)).length;
        resultsRoot.firstChild.textContent = `${t("批量质检")} · ${finished}/${job.items.length} · ${job.id.slice(-8)}`;
        await new Promise((resolve) => setTimeout(resolve, 1500));
        job = await api(`/jobs/${encodeURIComponent(job.id)}`);
      }
      const result = { items: job.items.map((item) => ({ ...item, ok: item.status === "success", error: item.error_message })) };
      result.success_count = result.items.filter((item) => item.ok).length;
      result.failed_count = result.items.length - result.success_count;
      if (generation !== state.sessionGeneration || !state.csrf) return;
      resultsRoot.replaceChildren(el("strong", "", t("批量质检完成：成功 {p0} 件，失败 {p1} 件", { p0: result.success_count, p1: result.failed_count })));
      const rows = el("div", "batch-result-list"); resultsRoot.append(rows);
      (result.items || []).forEach((item) => {
        const original = selected.find((entry) => entry.id === item.id);
        const row = el("div", `batch-result-row${item.ok ? "" : " error"}`);
        const product = item.result?.product;
        let outcome = item.ok ? product ? currentCheck(product).label : t("已完成") : errorText(text(item.error?.detail || item.error?.message || item.error, t("质检失败")));
        if (item.ok && item.result?.applied === false) outcome = t("已记录，商品版本已变化，需重新质检");
        const identity = el("div", "batch-result-identity");
        identity.append(button(original?.title || t("商品 {p0}", { p0: item.id }), "text-button", () => openDetail(item.id)), el("span", "product-meta", product?.product_id || `#${item.id}`));
        row.append(identity, el("span", "", localized(() => `${item.ok ? "✓" : "!"} ${outcome}`))); rows.append(row);
      });
      const next = el("div", "batch-next-step");
      next.append(el("p", "field-help", t("下一步：复核质检结果，选择符合条件的商品发布。")), button(t("继续批量审核发布"), "button primary compact", () => openBatchPublish(selected)));
      resultsRoot.append(next);
      state.selected.clear(); await refresh();
    } catch (error) { if (error.status !== 401 && generation === state.sessionGeneration) { resultsRoot.replaceChildren(notice(error.message, "error")); await refresh(); } }
    finally { if (generation === state.sessionGeneration) { state.batchBusy = false; setText($("batch-inspect"), t("批量质检")); renderSelection(); $("products-body").querySelectorAll("input[type=checkbox]").forEach((node) => { node.disabled = false; }); } }
  }

  function publicationSelection() {
    $("batch-publish-submit").disabled = publication.stage !== "ready" || !publication.selected.size || !$("batch-publish-confirm").checked || !$("batch-publish-reason").value.trim();
    setText($("batch-publish-submit"), publication.stage === "submitting" ? t("正在发布…") : t("确认发布 {count} 件", { count: publication.selected.size }));
  }
  function publicationCard(item) {
    const product = item.product;
    const original = publication.candidates.find((entry) => entry.id === item.id);
    const card = el("article", "publication-card");
    const row = el("div", "publication-card-heading");
    const select = el("input"); select.type = "checkbox"; select.disabled = !item.eligible;
    select.checked = publication.selected.has(item.id);
    setLabel(select, "aria-label", t("选择发布商品 {p0}", { p0: product?.product_id || `#${item.id}` }));
    select.addEventListener("change", () => {
      if (publication.stage !== "ready") return;
      if (select.checked) publication.selected.add(item.id); else publication.selected.delete(item.id);
      $("batch-publish-confirm").checked = false; publicationSelection();
    });
    const name = el("div", "publication-name");
    name.append(el("strong", "", product?.title || original?.title || `#${item.id}`), el("span", "product-meta", product ? `${product.product_id} · v${product.version}` : `#${item.id}`));
    row.append(select, name, badge(item.eligible ? riskNames[product.latest_risk] : t("不可发布"), item.eligible ? product.latest_risk : "failed")); card.append(row);
    if (item.error) card.append(notice(errorText(item.error), "error"));
    if (item.eligible && product.latest_risk !== "pass") card.append(notice(t("需人工复核：请查看下方问题，确认处理依据后再勾选。"), "warning"));
    if (product) {
      const details = el("details", "publication-evidence");
      details.append(el("summary", "", t("查看文案与质检问题")), el("p", "copy-block", product.description || t("未填写商品描述")), attributeList(product.attributes));
      for (const issue of product.latest_report?.issues || []) {
        details.append(el("p", "", localized(() => `${I18n.risk(issue.risk_level)} · ${I18n.issueLabel(issue.issue_type)}`)), el("p", "copy-block", issue.evidence), el("p", "field-help", localized(() => I18n.suggestion(issue.suggestion, issue))));
      }
      card.append(details);
    }
    return card;
  }
  async function openBatchPublish(candidates = [...state.selected.values()]) {
    if (state.batchBusy || publication.stage === "submitting" || !candidates.length) return;
    const request = ++publication.request, generation = state.sessionGeneration;
    publication.candidates = candidates.map((item) => ({ ...item }));
    publication.items = []; publication.selected.clear(); publication.stage = "loading";
    $("batch-publish-reason").value = ""; $("batch-publish-confirm").checked = false;
    $("batch-publish-review").hidden = true; $("batch-publish-review").disabled = true;
    $("batch-publish-recheck").hidden = true; $("batch-publish-submit").hidden = false;
    $("batch-publish-items").replaceChildren(); setError("batch-publish-error", "");
    $("batch-publish-dialog").querySelectorAll("[data-close]").forEach((node) => { node.disabled = false; });
    setText($("batch-publish-summary"), t("正在核对 {count} 件商品的发布条件…", { count: candidates.length }));
    publicationSelection(); showDialog("batch-publish-dialog"); $("batch-publish-dialog").scrollTop = 0;
    try {
      const result = await api("/products/batch-publish/preview", { method: "POST", data: { items: candidates.map(({ id, expected_version }) => ({ id, expected_version })) } });
      if (request !== publication.request || generation !== state.sessionGeneration || !$("batch-publish-dialog").open) return;
      publication.items = result.items;
      publication.selected = new Set(result.items.filter((item) => item.eligible && item.product.latest_risk === "pass").map((item) => item.id));
      publication.stage = "ready";
      setText($("batch-publish-summary"), t("可审核发布 {eligible} 件 · 不可发布 {blocked} 件", { eligible: result.eligible_count, blocked: result.blocked_count }));
      $("batch-publish-items").replaceChildren(...result.items.map(publicationCard));
      $("batch-publish-review").hidden = !result.eligible_count; $("batch-publish-review").disabled = !result.eligible_count;
      $("batch-publish-recheck").hidden = false; publicationSelection();
    } catch (error) {
      if (request !== publication.request || generation !== state.sessionGeneration) return;
      publication.stage = "error"; setError("batch-publish-error", error.message);
      setText($("batch-publish-summary"), t("发布条件检查未完成，请重试。")); $("batch-publish-recheck").hidden = false; publicationSelection();
    }
  }
  async function publishBatch(event) {
    event.preventDefault();
    if (publication.stage !== "ready" || $("batch-publish-submit").disabled) return;
    const submitted = publication.items.filter((item) => item.eligible && publication.selected.has(item.id));
    const reason = $("batch-publish-reason").value.trim();
    if (!submitted.length || !reason || !$("batch-publish-confirm").checked) return;
    const generation = state.sessionGeneration;
    publication.stage = "submitting"; state.batchBusy = true; renderSelection(); publicationSelection();
    $("batch-publish-review").disabled = true; $("batch-publish-recheck").hidden = true;
    $("batch-publish-items").querySelectorAll("input").forEach((node) => { node.disabled = true; });
    $("batch-publish-dialog").querySelectorAll("[data-close]").forEach((node) => { node.disabled = true; });
    setError("batch-publish-error", "");
    try {
      const result = await api("/products/batch-publish", { method: "POST", data: { items: submitted.map((item) => item.snapshot), reason } });
      if (generation !== state.sessionGeneration || !state.csrf) return;
      publication.stage = "done";
      setText($("batch-publish-summary"), t("批量发布完成：成功 {success} 件，失败 {failed} 件，未提交 {skipped} 件", { success: result.success_count, failed: result.failed_count, skipped: publication.items.length - submitted.length }));
      const rows = publication.items.map((item) => {
        const outcome = result.items.find((entry) => entry.id === item.id);
        const card = el("article", "publication-card");
        card.append(el("strong", "", item.product?.title || `#${item.id}`), el("span", "product-meta", item.product?.product_id || `#${item.id}`));
        if (!outcome) card.append(badge(t("未提交"), "stale"));
        card.append(outcome?.ok ? badge(t("已发布"), "published") : notice(outcome ? errorText(outcome.error) : item.error ? errorText(item.error) : t("未选择，未提交发布。"), outcome ? "error" : item.error ? "warning" : "info"));
        card.append(button(t("查看商品"), "text-button", () => openDetail(item.id)));
        return card;
      });
      $("batch-publish-items").replaceChildren(...rows);
      $("batch-publish-dialog").scrollTop = 0;
      $("batch-publish-review").hidden = true; $("batch-publish-submit").hidden = true;
      publication.candidates = publication.candidates.filter((item) => result.items.some((entry) => entry.id === item.id && !entry.ok));
      $("batch-publish-recheck").hidden = !publication.candidates.length;
      state.selected.clear(); await refresh();
    } catch (error) {
      if (generation !== state.sessionGeneration) return;
      publication.stage = "uncertain";
      setError("batch-publish-error", error.message);
      setText($("batch-publish-summary"), t("发布结果未能完整返回，部分商品可能已发布。请重新检查状态后再操作。"));
      $("batch-publish-recheck").hidden = false; await refresh();
    } finally {
      if (generation === state.sessionGeneration) {
        state.batchBusy = false; renderSelection(); publicationSelection();
        $("products-body").querySelectorAll("input[type=checkbox]").forEach((node) => { node.disabled = false; });
        $("batch-publish-dialog").querySelectorAll("[data-close]").forEach((node) => { node.disabled = false; });
      }
    }
  }
  async function openReport(product, inspection) {
    const requestId = ++state.reportRequest;
    $("report-content").replaceChildren(el("p", "subtle", t("正在加载此版本的质检报告…"))); showDialog("report-dialog");
    try {
      const [report, currentProduct] = await Promise.all([api(`/inspections/${encodeURIComponent(inspection.task_id)}/report`), api(`/products/${product.id}`)]);
      if (requestId !== state.reportRequest || !state.csrf) return;
      renderReport(report, currentProduct, inspection);
    } catch (error) { if (requestId === state.reportRequest && error.status !== 401) $("report-content").replaceChildren(notice(error.message, "error")); }
  }
  function renderReport(report, product, inspection) {
    state.report = { report, product, inspection };
    const root = $("report-content"); root.replaceChildren();
    const version = inspection?.version ?? product.inspected_version;
    const limited = report.status !== "success" || !report.rules?.length || report.retrieval_source === "unavailable" || (report.mode === "full" && (report.degraded || !report.model_used));
    const historical = version !== product.version || (report.task_id || inspection?.task_id) !== product.latest_task_id;
    setText($("report-title"), t("质检报告 · 文案 v{p0}", { p0: version }));
    root.append(el("p", "detail-meta", `${product.product_id} · ${product.merchant_name}`));
    root.append(notice(historical ? t("历史报告：本报告检查的是 v{p0} 文案，商品当前为 v{p1}，或已有更新的质检任务。此报告不代表当前有效质检结果。", { p0: version, p1: product.version }) : t("本报告对应商品 v{p0} 的文案。后续编辑将使该报告失效，需重新质检。", { p0: version }), historical ? "warning" : "info"));
    const reportBanner = el("div", `report-banner ${limited ? "partial" : Object.hasOwn(riskNames, report.risk_level) ? report.risk_level : "partial"}`);
    const conclusion = el("div"); conclusion.append(el("h3", "", limited ? t("检查未完整完成") : riskNames[report.risk_level] || t("结果待确认")), el("p", "", localized(() => I18n.reportSummary(report))));
    const score = el("div", "report-score", text(report.score)); score.append(el("small", "", limited ? t("暂定风险分") : t("风险分 · 越低越好"))); reportBanner.append(conclusion, score); root.append(reportBanner);
    const labels = el("div", "report-labels"); labels.append(el("span", "", t("{p0}{p1}", { p0: (report.mode || inspection?.mode) === "full" ? t("完整模式") : t("规则模式"), p1: report.degraded ? t(" · 能力受限") : "" })), el("span", "", t("{p0} 项问题", { p0: (report.issues || []).length })), el("span", "", dateTime(inspection?.created_at)));
    if (report.rule_version) labels.append(el("span", "", t("规则版本 {p0}{p1}", { p0: report.rule_version, p1: report.rule_source ? ` · ${report.rule_source}` : "" })));
    root.append(labels);
    if (limited) root.append(notice(t("检查未完整完成，不能用于审核发布。请重新运行质检；完整模式受限时可选择规则模式。"), "warning"));
    (report.warnings || []).forEach((warning) => root.append(notice(errorText(text(warning)), "warning")));
    const issueHeading = el("div", "report-section-heading"); issueHeading.append(el("h3", "", t("发现的问题"))); root.append(issueHeading);
    if (!(report.issues || []).length) root.append(el("p", "subtle", limited ? t("已完成的检查暂未发现问题，仍需完成其余检查。") : t("本次检查范围内未发现问题。")));
    (report.issues || []).forEach((issue, index) => {
      const card = el("article", "record-card"); const heading = el("div", "record-heading"); heading.append(el("h4", "", t("{p0}  {p1}", { p0: String(index + 1).padStart(2, "0"), p1: localized(() => I18n.issueLabel(issue.issue_type || "文案问题")) })), badge(riskNames[issue.risk_level] || issue.risk_level, issue.risk_level));
      const evidence = el("div", "issue-quote", issue.evidence); evidence.setAttribute("data-i18n-skip", "");
      card.append(heading, el("p", "record-meta", t("位置：{p0}", { p0: localized(() => I18n.field(issue.field)) })), evidence, el("p", "", t("建议：{p0}", { p0: localized(() => I18n.suggestion(issue.suggestion || "请结合真实商品信息复核。", issue)) })));
      if (issue.rule_id) card.append(el("p", "record-meta", t("规则依据：{p0}", { p0: issue.rule_id }))); root.append(card);
    });
    const rewriteHeading = el("div", "report-section-heading"); rewriteHeading.append(el("h3", "", t("参考优化文案")));
    if (!historical && (report.optimized_title != null || report.optimized_description != null)) rewriteHeading.append(button(t("应用到编辑草稿"), "text-button", () => openEditor(product.id, report)));
    root.append(rewriteHeading, el("p", "field-help", t("建议需核实后保存；保存不会自动发布，并需重新质检。")), detailSection(t("优化标题"), el("div", "copy-block", report.optimized_title === "" ? t("建议清空原商品标题，请补充真实客观的标题。") : report.optimized_title ?? t("未返回优化标题"))), detailSection(t("优化描述"), el("div", "copy-block", report.optimized_description === "" ? t("建议清空原商品描述；可核实后补充客观信息。") : report.optimized_description ?? t("未返回优化描述"))));
    if (report.rewrite_reason) root.append(detailSection(t("修改说明"), el("div", "copy-block", localized(() => I18n.suggestion(report.rewrite_reason)))));
    const rulesHeading = el("div", "report-section-heading"); rulesHeading.append(el("h3", "", t("规则依据"))); root.append(rulesHeading);
    if (!(report.rules || []).length) root.append(el("p", "subtle", t("本次报告未返回规则引用。")));
    (report.rules || []).forEach((rule) => {
      const card = el("article", "record-card"); card.append(el("h4", "", rule.rule_id || rule.id || t("规则依据")), el("p", "", localized(() => I18n.ruleText(rule.rule_text || rule.content || rule.description || rule.rule_name)))); if (rule.rewrite_hint) card.append(el("p", "record-meta", t("修改参考：{p0}", { p0: localized(() => I18n.suggestion(rule.rewrite_hint)) }))); root.append(card);
    });
    const source = (product.revisions || []).find((revision) => revision.version === version)?.product_json;
    if (source) { const details = el("details", "trace-details"); details.append(el("summary", "", t("查看实际检查的 v{p0} 文案", { p0: version })), el("div", "copy-block", source)); root.append(details); }
    const traces = el("details", "trace-details"); const tracesBody = el("div"); traces.append(el("summary", "", t("查看执行过程 · Trace")), tracesBody);
    let traceLoaded = false;
    traces.addEventListener("toggle", async () => {
      if (!traces.open || traceLoaded) return;
      traceLoaded = true; tracesBody.replaceChildren(el("p", "field-help", t("正在加载执行过程…")));
      const load = async () => {
        try {
          const result = await api(`/inspections/${encodeURIComponent(report.task_id || inspection.task_id)}/traces`);
          const list = el("ol", "trace-list"); const entries = Array.isArray(result) ? result : result.traces || [];
          entries.forEach((trace, index) => {
            const item = el("li"); item.append(el("h4", "", t("{p0} · {p1}", { p0: trace.step_name ? t(trace.step_name) : t("步骤 {p0}", { p0: index + 1 }), p1: checkStatus[trace.status] || trace.status || t("完成") })), el("p", "", `${[trace.skill_name, trace.tool_name].filter(Boolean).join(" / ")}${typeof trace.latency_ms === "number" ? ` · ${Math.round(trace.latency_ms)} ms` : ""}`), el("p", "", t("输入：{p0}", { p0: t(text(trace.input_summary)) })), el("p", "", t("输出：{p0}", { p0: t(text(trace.output_summary)) }))); list.append(item);
          });
          tracesBody.replaceChildren(entries.length ? list : el("p", "field-help", t("本次任务暂无执行步骤。")));
        } catch (error) { if (error.status !== 401) tracesBody.replaceChildren(notice(error.message, "error"), button(t("重新加载"), "text-button", load)); }
      };
      await load();
    });
    root.append(traces, el("p", "detail-meta", t("任务 ID：{p0}", { p0: text(report.task_id || inspection?.task_id) }))); showDialog("report-dialog");
  }

  const simulation = { preview: null, options: null, delivered: null, busy: false, request: 0 };
  function simulationOptions() {
    const count = Number($("simulation-count").value);
    const seed = Number($("simulation-seed").value);
    const batch_id = $("simulation-batch-id").value.trim();
    if (!Number.isInteger(count) || count < 1 || count > 100 || !Number.isInteger(seed) || seed < 0 || seed > 4294967295 || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(batch_id)) {
      throw new Error(t("数量须为 1–100 的整数，种子须为 0–4294967295 的整数。批次 ID 为 1–64 位字母、数字、下划线或连字符，且以字母或数字开头。"));
    }
    return { count, seed, scenario: $("simulation-scenario").value, batch_id };
  }
  function setSimulationBusy(busy, action = "preview") {
    simulation.busy = busy;
    $("simulation-options").disabled = busy;
    $("simulation-preview-button").disabled = busy;
    $("simulation-deliver-button").disabled = busy || !simulation.preview || Boolean(simulation.delivered);
    setText($("simulation-preview-button"), busy && action === "preview" ? t("正在生成预览…") : t("预览数据"));
    setText($("simulation-deliver-button"), busy && action === "deliver" ? t("正在投递…") : t("投递此预览"));
  }
  function renderSimulationPreview(result) {
    const root = $("simulation-preview"); root.replaceChildren();
    root.append(el("h3", "", t("预览 {count} 件合成商品 · 批次 {batch}", { count: result.count, batch: result.batch_id })));
    for (const entry of result.products || []) {
      const product = entry.product;
      const card = el("article", "record-card");
      const heading = el("div", "record-heading");
      heading.append(el("h4", "", product.title), badge(t("合成演示数据")));
      card.append(heading, el("p", "record-meta", t("{p0} · {p1} · {p2}", { p0: product.product_id, p1: product.merchant_name, p2: localized(() => I18n.category(product.category)) })), el("p", "", product.description || t("未填写商品描述")));
      const details = el("details"); details.append(el("summary", "", t("查看原始属性")), attributeList(product.attributes)); card.append(details); root.append(card);
    }
  }
  async function previewSimulation(event) {
    event.preventDefault();
    if (simulation.busy) return;
    let options;
    try { options = simulationOptions(); } catch (error) { setError("simulation-error", error.message); return; }
    const request = ++simulation.request;
    const generation = state.sessionGeneration;
    simulation.preview = null; simulation.options = null; simulation.delivered = null;
    $("simulation-preview").replaceChildren(); $("simulation-result").hidden = true;
    setError("simulation-error", ""); setSimulationBusy(true);
    try {
      const result = await api("/simulation/preview", { method: "POST", data: options });
      if (generation !== state.sessionGeneration || request !== simulation.request || !state.csrf) return;
      if (!Array.isArray(result.products) || result.products.length !== options.count || result.source !== "synthetic") throw new Error(t("模拟数据预览失败，请重试。"));
      simulation.preview = result;
      simulation.options = { ...options, batch_id: result.batch_id, seed: result.seed };
      renderSimulationPreview(result);
    } catch (error) { if (error.status !== 401 && generation === state.sessionGeneration) setError("simulation-error", error.message); }
    finally { if (request === simulation.request) setSimulationBusy(false); }
  }
  async function loadSimulationJob(id, target) {
    const generation = state.sessionGeneration;
    target.replaceChildren(el("p", "field-help", t("正在读取任务状态…")));
    try {
      const job = await api(`/jobs/${encodeURIComponent(id)}`);
      if (generation !== state.sessionGeneration || !state.csrf || !target.isConnected) return;
      const names = { queued: t("排队中"), pending: t("排队中"), retry: t("等待重试"), running: t("处理中"), succeeded: t("已完成"), completed: t("已完成"), success: t("已完成"), partial: t("部分完成"), failed: t("检查失败"), cancelled: t("已取消") };
      const content = [el("p", "", t("任务状态：{status}", { status: names[job.status] || job.status || t("任务状态暂不可用") }))];
      const total = job.total_count ?? job.total ?? job.progress?.total ?? job.items?.length;
      const done = job.completed_count ?? job.processed_count ?? job.completed ?? job.progress?.completed ?? job.items?.filter((item) => ["success", "failed", "cancelled"].includes(item.status)).length;
      if (total != null && done != null) content.push(el("p", "field-help", t("已完成 {done} / {total} 件", { done, total })));
      if (job.error_message || job.error) content.push(notice(errorText(text(job.error_message || job.error)), "error"));
      for (const item of job.items || []) {
        const row = el("div", "simulation-job-item");
        const productId = simulation.delivered?.products?.find((product) => product.id === item.id)?.product_id;
        row.append(el("span", "", productId || t("商品 {p0}", { p0: item.id })), badge(names[item.status] || item.status));
        if (item.error_message) row.append(notice(errorText(item.error_message), "error"));
        if (item.task_id && item.inspection_id) row.append(button(t("质检报告"), "text-button", async (event) => {
          const trigger = event.currentTarget; trigger.disabled = true;
          try {
            const product = await api(`/products/${item.id}`);
            const inspection = product.inspections?.find((entry) => entry.task_id === item.task_id) || { task_id: item.task_id, version: item.expected_version, mode: job.mode };
            await openReport(product, inspection);
          } catch (error) { if (error.status !== 401) showToast(error.message); }
          finally { trigger.disabled = false; }
        }));
        content.push(row);
      }
      target.replaceChildren(...content);
    } catch (error) { if (error.status !== 401 && generation === state.sessionGeneration) target.replaceChildren(notice(error.message, "error")); }
  }
  function renderSimulationDelivery(result) {
    const root = $("simulation-result"); root.hidden = false; root.replaceChildren();
    root.append(notice(t("已投递：新增 {created} 件，已存在 {existing} 件。", { created: result.created_count, existing: result.existing_count })));
    if (result.job_id) {
      const status = el("div", "simulation-job-status");
      root.append(el("h3", "", t("质检任务 {id}", { id: result.job_id })), status, button(t("刷新任务状态"), "button secondary compact", async (event) => {
        event.currentTarget.disabled = true;
        const trigger = event.currentTarget;
        try { await loadSimulationJob(result.job_id, status); await refresh(); } finally { trigger.disabled = false; }
      }));
      loadSimulationJob(result.job_id, status);
    } else root.append(el("p", "field-help", t("未创建质检任务。可在商品列表选择商品进行质检。")));
    root.append(button(t("查看商品列表"), "button ghost compact", () => { closeDialog("simulation-dialog"); quickFilter("all"); }));
    root.scrollIntoView({ block: "nearest" });
  }
  async function deliverSimulation() {
    if (simulation.busy || !simulation.preview || !simulation.options || simulation.delivered) return;
    let current;
    try { current = simulationOptions(); } catch (error) { setError("simulation-error", error.message); return; }
    if (JSON.stringify(current) !== JSON.stringify(simulation.options)) { setError("simulation-error", t("参数已修改，请重新预览后投递。")); return; }
    const generation = state.sessionGeneration;
    setError("simulation-error", ""); setSimulationBusy(true, "deliver");
    try {
      const result = await api("/simulation/deliver", { method: "POST", data: { ...simulation.options, enqueue_inspection: $("simulation-enqueue").checked } });
      if (generation !== state.sessionGeneration || !state.csrf) return;
      simulation.delivered = result; renderSimulationDelivery(result); await refresh();
    } catch (error) { if (error.status !== 401 && generation === state.sessionGeneration) setError("simulation-error", error.message); }
    finally { setSimulationBusy(false); }
  }
  function updateLocalizedUI() {
    I18n.apply(document);
    document.querySelectorAll("[data-ui-message]").forEach((node) => {
      const message = bindings.get(node);
      if (message) node.textContent = String(message);
    });
    document.querySelectorAll("[data-ui-attributes]").forEach((node) => {
      for (const [name, message] of Object.entries(node._localeAttributes || {})) node.setAttribute(name, String(message));
    });
    document.querySelectorAll("[data-language-switch]").forEach((node) => { node.value = I18n.getLocale(); });
    window.SelectControls?.refresh();
    document.title = String(t("商品管理 · 文案质检管理后台"));
  }
  function initializeDialogLanguages() {
    // Modal dialogs make the rest of the page inert, so each gets its own switch.
    document.querySelectorAll(".drawer-heading, .modal-heading").forEach((heading) => {
      const wrapper = el("span", "locale-control dialog-locale");
      const switcher = el("select", "dialog-language"); switcher.setAttribute("data-language-switch", "");
      setLabel(switcher, "aria-label", t("界面语言"));
      for (const [value, label] of [["zh-CN", "中文"], ["en", "English"]]) { const option = el("option", "", label); option.value = value; switcher.append(option); }
      switcher.value = I18n.getLocale();
      wrapper.append(switcher);
      heading.insertBefore(wrapper, heading.lastElementChild);
    });
  }
  $("simulate-products").addEventListener("click", () => showDialog("simulation-dialog"));
  $("import-products").addEventListener("click", () => { $("import-form").reset(); $("import-file-name").textContent = text(t("尚未选择文件")); $("import-error").hidden = true; $("import-result").hidden = true; showDialog("import-dialog"); });
  $("import-file").addEventListener("change", () => { const file = $("import-file").files[0]; $("import-file-name").textContent = file ? file.name : text(t("尚未选择文件")); });
  $("import-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const file = $("import-file").files[0]; if (!file) return;
    const submit = $("import-submit"); submit.disabled = true; $("import-error").hidden = true; $("import-result").hidden = true;
    try {
      const form = new FormData(); form.append("file", file);
      const response = await fetch("/api/admin/products/import", { method: "POST", body: form, credentials: "same-origin", headers: state.csrf ? { "X-CSRF-Token": state.csrf } : {} });
      const body = await response.json(); if (!response.ok) throw new Error(responseError(body, response.status));
      $("import-result").textContent = text(t("导入完成：成功 {p0} 条，失败 {p1} 条。", { p0: body.created || 0, p1: body.failed || 0 })); $("import-result").hidden = false;
      state.page = 1; await refresh();
    } catch (error) { setError("import-error", error.message); } finally { submit.disabled = false; }
  });
  $("simulation-form").addEventListener("submit", previewSimulation);
  $("simulation-deliver-button").addEventListener("click", deliverSimulation);
  $("simulation-options").addEventListener("input", (event) => {
    if (event.target.id === "simulation-enqueue") return;
    simulation.preview = null; simulation.options = null; simulation.delivered = null;
    $("simulation-preview").replaceChildren(); $("simulation-result").hidden = true;
    setSimulationBusy(false);
  });

  const tenantLabel = el("label", "", t("租户标识")); tenantLabel.htmlFor = "login-tenant";
  tenantLabel.setAttribute("data-i18n", "租户标识");
  const tenantInput = el("input"); tenantInput.id = "login-tenant"; tenantInput.name = "tenant";
  tenantInput.value = "default"; tenantInput.maxLength = 32; tenantInput.required = true;
  tenantInput.autocomplete = "organization"; tenantInput.pattern = "[a-z][a-z0-9-]{0,31}";
  const usernameLabel = document.querySelector('label[for="login-username"]');
  usernameLabel.before(tenantLabel, tenantInput);
  $("batch-mode").value = "full";
  $("login-form").addEventListener("submit", login);
  $("logout").addEventListener("click", logout);
  $("create-product").addEventListener("click", () => openEditor());
  $("refresh-list").addEventListener("click", () => { state.selected.clear(); refresh(); });
  $("filter-form").addEventListener("submit", (event) => { event.preventDefault(); filterChanged(); });
  $("filter-category").addEventListener("change", filterChanged);
  $("filter-risk").addEventListener("change", filterChanged);
  $("reset-filters").addEventListener("click", () => quickFilter("all"));
  $("empty-reset").addEventListener("click", () => quickFilter("all"));
  document.querySelectorAll("[data-nav]").forEach((node) => node.addEventListener("click", () => quickFilter(node.dataset.nav)));
  document.querySelectorAll("[data-metric]").forEach((node) => node.addEventListener("click", () => quickFilter(node.dataset.metric)));
  document.querySelectorAll("[data-status]").forEach((node) => node.addEventListener("click", () => { state.status = node.dataset.status; filterChanged(); }));
  $("page-size").addEventListener("change", () => { state.pageSize = Number($("page-size").value); filterChanged(); });
  $("prev-page").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; state.selected.clear(); renderSelection(); loadProducts(); } });
  $("next-page").addEventListener("click", () => { if (state.page * state.pageSize < state.total) { state.page += 1; state.selected.clear(); renderSelection(); loadProducts(); } });
  $("select-all").addEventListener("change", () => {
    if ($("select-all").checked) {
      const remaining = 20 - state.selected.size;
      const selectable = state.items.filter((product) => !state.selected.has(product.id));
      selectable.slice(0, remaining).forEach((product) => state.selected.set(product.id, { id: product.id, expected_version: product.version, title: product.title, status: product.status }));
      if (selectable.length > remaining) showToast(t("已选择本页前 20 件商品，每批最多质检 20 件。"));
    } else state.items.forEach((product) => state.selected.delete(product.id));
    renderProducts();
  });
  $("clear-selection").addEventListener("click", () => { state.selected.clear(); renderProducts(); });
  const batchPublishButton = button(t("批量审核发布"), "button secondary compact", () => openBatchPublish());
  batchPublishButton.id = "batch-publish"; $("batch-inspect").after(batchPublishButton);
  $("batch-inspect").addEventListener("click", batchInspect);
  $("batch-publish-form").addEventListener("submit", publishBatch);
  $("batch-publish-reason").addEventListener("input", publicationSelection);
  $("batch-publish-confirm").addEventListener("change", publicationSelection);
  // A deliberate recheck reads new versions; the next submission still uses
  // only the new preview's frozen snapshot and requires fresh confirmation.
  $("batch-publish-recheck").addEventListener("click", () => openBatchPublish(publication.candidates.map(({ id, title }) => ({ id, title }))));
  $("batch-publish-dialog").addEventListener("cancel", (event) => { if (publication.stage === "submitting") event.preventDefault(); });
  $("batch-publish-dialog").addEventListener("close", () => { publication.request += 1; });
  $("editor-form").addEventListener("submit", saveProduct);
  $("format-attributes").addEventListener("click", () => { try { $("edit-attributes").value = JSON.stringify(parseAttributes(), null, 2); renderAttributeForm(); setError("editor-error", ""); } catch (error) { setError("editor-error", error.message); } });
  $("action-form").addEventListener("submit", performAction);
  document.querySelectorAll("[data-close]").forEach((node) => node.addEventListener("click", () => closeDialog(node.dataset.close)));
  document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => {
    const bounds = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom)) closeDialog(dialog.id);
  }));
  initializeAttributes();
  initializeDialogLanguages();
  I18n.onChange(updateLocalizedUI);
  updateLocalizedUI();
  (async () => {
    try { const session = await api("/auth/me", { allowUnauthorized: true }); await activateSession(session); }
    catch (error) { showLogin(error.status === 401 ? "" : error.message); }
  })();
})();
