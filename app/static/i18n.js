/* Display language only. Source product text, evidence and stored reports are immutable. */
"use strict";
(() => {
  const storageKey = "quality-agent.locale";
  const messages = Object.create(null);
  const listeners = new Set();
  const normalize = (value) => value === "en" || /^en-/i.test(value || "") ? "en" : "zh-CN";
  let preference;
  try { preference = localStorage.getItem(storageKey); } catch (_) { /* storage may be disabled */ }
  let locale = normalize(preference || navigator.language || "zh-CN");

  function t(source, params = {}) {
    const key = source == null ? "" : String(source);
    const result = locale === "en" && Object.hasOwn(messages, key) ? messages[key] : key;
    return result.replace(/\{([\w]+)\}/g, (match, name) => Object.hasOwn(params, name) ? String(params[name]) : match);
  }
  function apply(root = document) {
    const nodes = root.querySelectorAll("[data-i18n], [data-i18n-placeholder], [data-i18n-title], [data-i18n-aria-label]");
    const update = (node) => {
      if (node.closest("[data-i18n-skip]")) return;
      if (node.hasAttribute("data-i18n")) node.textContent = t(node.getAttribute("data-i18n"));
      for (const attr of ["placeholder", "title", "aria-label"]) {
        const key = node.getAttribute(`data-i18n-${attr}`);
        if (key !== null) node.setAttribute(attr, t(key));
      }
    };
    if (root.nodeType === 1) update(root);
    nodes.forEach(update);
    document.documentElement.lang = locale;
    document.querySelectorAll("[data-language-switch]").forEach((select) => {
      select.value = locale;
      select.setAttribute("aria-label", locale === "en" ? "Interface language" : "界面语言");
      if (!select.dataset.languageBound) {
        select.dataset.languageBound = "true";
        select.addEventListener("change", () => setLocale(select.value));
      }
    });
    window.SelectControls?.refresh(root);
  }
  function setLocale(value) {
    const next = normalize(value);
    try { localStorage.setItem(storageKey, next); } catch (_) { /* session preference still works */ }
    if (next === locale) { apply(); return; }
    locale = next;
    apply();
    listeners.forEach((callback) => callback(locale));
    window.dispatchEvent(new CustomEvent("languagechange", { detail: { locale } }));
  }
  function register(dictionary) {
    for (const [key, value] of Object.entries(dictionary)) {
      if (typeof value === "string") messages[key] = value;
    }
  }
  const categories = { "食品": "Food", "美妆": "Beauty", "3C": "Electronics", "通用": "General" };
  const risks = { high: ["高风险", "High risk"], medium: ["中风险", "Medium risk"], low: ["低风险", "Low risk"], pass: ["通过", "Pass"] };
  const fields = {
    title: ["商品标题", "Product title"], description: ["商品描述", "Description"], attributes: ["商品属性", "Attributes"],
    category: ["商品类目", "Category"], brand: ["品牌", "Brand"], origin: ["产地", "Origin"], shelf_life: ["保质期", "Shelf life"],
    ingredients: ["成分 / 配料", "Ingredients"], storage: ["储存方式", "Storage"], skin_type: ["适用肤质", "Skin type"],
    usage: ["使用方法", "Directions"], precautions: ["注意事项", "Precautions"], model: ["型号", "Model"],
    specifications: ["规格", "Specifications"], compatibility: ["兼容性", "Compatibility"], warranty: ["保修期", "Warranty"], report: ["质检报告", "Inspection report"],
  };
  function field(value) {
    const key = String(value || "");
    return key.split(".").map((part) => fields[part]?.[locale === "en" ? 1 : 0] || part).join(" · ");
  }
  function risk(value) { return risks[value]?.[locale === "en" ? 1 : 0] || String(value || "—"); }
  function category(value) { return locale === "en" ? categories[value] || String(value || "—") : String(value || "—"); }
  function reportSummary(report) {
    if (locale !== "en") return report.summary || "";
    const limited = report.status !== "success" || !report.rules?.length || report.retrieval_source === "unavailable"
      || (report.mode === "full" && (report.degraded || !report.model_used));
    const count = (report.issues || []).length;
    return `${category(report.category)} · ${count} issue${count === 1 ? "" : "s"} identified · ${risk(report.risk_level)}. `
      + (limited ? "Inspection coverage is incomplete; this report cannot authorize publication. " : "Results cover the checks performed and require human review. ")
      + "Original Chinese copy and evidence are preserved below.";
  }
  function error(raw, status) {
    const source = typeof raw === "string" ? raw : raw?.detail || raw?.message || "";
    const text = typeof source === "string" ? source : JSON.stringify(source);
    if (locale !== "en" || Object.hasOwn(messages, text)) return t(text);
    const fallback = { 401: "Please sign in to the administrator console.", 403: "This request was rejected. Refresh the page and try again.",
      409: "The product or inspection has changed. Refresh the details before retrying.", 413: "The request is too large.",
      422: "Check the input fields and try again.", 429: "Too many requests. Please try again later.",
      500: "The server could not complete this operation.", 502: "Inspection failed. See the saved task for details.",
      503: "A required service is unavailable." };
    if (!/[\u3400-\u9fff]/u.test(text)) return text || fallback[status] || "Request failed. Please try again.";
    return `${fallback[status] || "The operation could not be completed."} Original server message: ${text}`;
  }
  register({
    "未发现问题，保留原文。": "No issues were found. The original copy was retained.",
  "删去重复修饰词；例如“清风茉莉花茶袋泡茶 30包”。": "Remove repeated modifiers, for example: “清风茉莉花茶袋泡茶 30包”.",
  "删除无法验证的承诺，只保留已提供的属性；例如“清风茉莉花茶 30包”。": "Remove unverifiable promises and keep supplied attributes, for example: “清风茉莉花茶 30包”.",
  "每个必要修饰词最多保留一次，优先保留商品品类与规格。": "Use each necessary modifier once, prioritizing the product type and specifications.",
  "补充商品实际品类；例如“机械键盘 K87 青轴”。": "Add the actual product type, for example: “机械键盘 K87 青轴”.",
  "依据产品标签核实后统一两处信息，不能猜测哪一处正确。": "Check the product label and reconcile both statements. Do not guess which is correct.",
  "移除缺乏依据的结论，保留已给出的型号、配料或使用条件。": "Remove unsupported conclusions; retain supplied models, ingredients, or usage conditions.",
  "删除保证性结论，说明输入中已有的适用条件和限制。": "Remove guarantees and state the applicable conditions and limitations supplied in the input.",
  "保留有依据的适用人群；例如“特殊人群请依据产品标签选择”。": "Retain supported audience information, for example: “特殊人群请依据产品标签选择”.",
  "按包装标签补齐必填信息，不编造配料或期限。": "Complete required details from the packaging label. Do not invent ingredients or durations.",
  "删去医疗暗示；例如“茉莉清香，适合日常冲泡”。": "Remove medical implications, for example: “茉莉清香，适合日常冲泡”.",
  "核对包装标签，填写真实期限；没有依据时仅提示待补充。": "Verify the packaging label and enter the actual duration. Mark unsupported details as missing.",
  "按标签逐项补充配料，不能根据品名推断成分。": "List ingredients from the label. Do not infer them from the product name.",
  "按标签填写温度、避光、密封等真实要求。": "Enter actual temperature, light protection, and sealing requirements from the label.",
  "删除缺少证据的健康效果，保留现有配料、口味及食用方式。": "Remove unsupported health effects; keep supplied ingredients, flavor, and serving instructions.",
  "移除速成承诺，仅描述输入已提供的规格和食用场景。": "Remove promises of rapid results. Describe only supplied specifications and serving contexts.",
  "依据标签补充人群限制和过敏提示，未提供依据时不新增适用人群。": "Add audience restrictions and allergy notices from the label. Do not invent suitability claims.",
  "依据标签补齐属性，不代替厂商编造使用说明。": "Complete attributes from the label. Do not invent the manufacturer's directions.",
  "移除长期功效承诺，保留已提供的日常护理用途。": "Remove long-term efficacy promises; retain supplied daily care uses.",
  "删除治疗性用语；例如“日常保湿护理”，仅在输入已有该用途时使用。": "Remove treatment claims. Use wording such as “日常保湿护理” only if that use was supplied in the input.",
  "使用输入已有的真实使用及补涂说明，删除全天保证。": "Use the supplied usage and reapplication instructions; remove all-day guarantees.",
  "说明已有的适用肤质，保留局部测试和不适停用提醒。": "State supplied skin types and retain patch-test and stop-use notices.",
  "按说明书补充用量、部位和频率；缺乏依据时提示待补充。": "Add amounts, application areas, and frequency from the instructions. Mark unsupported details as missing.",
  "按产品说明补充真实的使用限制和不适处理方式。": "Add actual usage limitations and adverse-reaction instructions from the product directions.",
  "区分成分事实与成品功效，只保留已提供的成分表。": "Separate ingredient facts from finished-product efficacy. Retain only the supplied ingredient list.",
  "补充准确型号、规格、协议和保修期限。": "Add the accurate model, specifications, protocols, and warranty duration.",
  "写明输入已有的适用型号、使用条件和有限保修范围。": "State the supported models, usage conditions, and limited warranty scope supplied in the input.",
  "依据参数表填写支持的协议或型号，避免宣称全兼容。": "List supported protocols or models from the specification sheet. Avoid universal compatibility claims.",
  "填写商家确认的保修期限和范围，不新增永久保修承诺。": "Enter the warranty duration and scope confirmed by the seller. Do not add lifetime guarantees.",
  "从规格表补充实际容量、功率、尺寸或协议版本。": "Add actual capacity, power, dimensions, or protocol versions from the specification sheet.",
  "保留真实显示参数及使用条件，删除未经证实的健康效果。": "Retain actual display parameters and usage conditions. Remove unverified health effects.",
  "写明输入已有的无线标准、规格及已知使用条件。": "State supplied wireless standards, specifications, and known usage conditions.",
  "核实真实型号后统一文案和属性，不自行选择看似更高的规格。": "Verify the actual model and reconcile the copy and attributes. Do not choose a seemingly higher specification.",
  "删除医疗效果承诺，仅保留输入已有的商品属性和使用方式。": "Remove medical promises; keep only product attributes and usage instructions supplied in the input.",
    "删除已命中风险或矛盾表述所在的分句，保留原有客观信息；缺失属性需依据商品实物补齐。若标题被全部删除，使用待补充标记。": "Clauses containing flagged risks or conflicts were removed while factual information was retained. Add missing attributes using the actual product. If the whole title was removed, a placeholder indicates that it needs to be supplied.",
    "商品标题应简明并保留核心商品属性，项目演示阈值为60个字符（含空格标点），超过阈值提示优化。": "Keep titles concise while retaining key attributes. This project's demonstration limit is 60 characters, including spaces and punctuation; exceeding it triggers a suggestion.",
    "不得作无法验证的绝对化、最低价、国家级或安全性保证。结合上下文区分否定提醒、客观配料比例与功效承诺；100%燕麦不等于100%有效。": "Avoid unverifiable absolute, lowest-price, national-level or safety guarantees. Distinguish disclaimers and factual ingredient ratios from efficacy claims: 100% oats is not 100% effective.",
    "商品标题中的旗舰、爆款、特价、正品、新品、官方等营销词不应重复堆砌。": "Do not repeatedly stuff titles with promotional terms such as flagship, best seller, special offer, authentic, new or official.",
    "标题应使消费者识别商品品类，不能只剩无意义的促销修饰词。词典无法识别的真实品类应转语义复核，不能直接认定违规。": "Titles should identify the product type rather than consist solely of promotional language. An unfamiliar product type requires semantic review, not an automatic violation.",
    "标题、描述中明确声明的品牌、产地、型号、适用范围等应与结构化属性一致；近义表述不应直接判为冲突。": "Explicit brand, origin, model and intended-use claims in copy should agree with structured attributes. Equivalent wording is not itself a contradiction.",
    "不得以缺乏证据的文案夸大商品性能或健康效果；量化参数或限定条件应有输入事实支持。": "Do not exaggerate performance or health effects without evidence. Quantified claims and conditions must be supported by supplied facts.",
    "不得承诺任何人、任何环境下的持久效果、速成效果或零失败；否定该承诺的警示不属于正向承诺。": "Avoid universal guarantees of lasting effects, instant results or zero failure. A warning that denies such a guarantee is not a positive claim.",
    "不得在缺少限制说明时宣称所有年龄、所有身体状况的人均适用，涉及婴幼儿、孕期等人群应按标签谨慎表述。": "Do not claim suitability for every age or health condition without limitations. Statements about infants or pregnancy should follow the product label.",
    "食品应提供品牌、产地、保质期、配料和储存方式；空串、空白或空容器视为缺失。": "Food listings should provide brand, origin, shelf life, ingredients and storage instructions. Empty strings, whitespace and empty containers count as missing.",
    "普通食品不得明示或暗示疾病治疗效果，包括改善疾病结局的承诺。明确否定治疗效果、提醒不可替代药品的陈述应排除。": "Ordinary food should not claim or imply disease treatment. Exclude explicit disclaimers denying treatment effects or stating that the product cannot replace medication.",
    "保质期应给出有效的正数期限及单位或明确到期日期，负数、零天等无效值应核实。": "Shelf life should specify a positive duration with units or a clear expiry date. Verify invalid values such as negative durations or zero days.",
    "配料字段应列出实际配料；以实物为准、保密等占位语不能代替配料信息。": "List actual ingredients. Placeholders such as refer to product or confidential do not provide ingredient information.",
    "储存方式应说明与商品相符的保存条件，随便放、任意环境等表述不足以指导保存。": "Specify storage conditions appropriate to the product. Statements such as store anywhere do not give usable guidance.",
    "普通食品不得以无依据的功能描述承诺免疫、防病或生理指标的改善。": "Ordinary food should not make unsupported promises about immunity, disease prevention or physiological improvement.",
    "食品不得承诺短期长高、快速减重或食用一次即可持续改善身体状态。": "Food should not promise rapid growth, rapid weight loss or lasting physical improvement after one serving.",
    "食品不应笼统承诺老人、儿童、孕妇或过敏人群均适合；有明确限定条件的正常人群说明应保留。": "Avoid blanket food suitability claims for older adults, children, pregnancy or people with allergies. Retain appropriately qualified audience statements.",
    "美妆商品应说明品牌、成分、适用肤质、使用方式和注意事项；字段有值不代表内容充分。": "Beauty listings should specify brand, ingredients, skin types, directions and precautions. A nonempty field is not necessarily informative.",
    "化妆品不得宣称永久性改变或暗示持续治疗问题肌肤，医学级等表达应有适用依据。": "Cosmetics should not promise permanent changes or ongoing treatment of skin conditions. Medical-grade claims require applicable evidence.",
    "化妆品不得宣称治疗、根治疾病或将正常老化描述为可以根治的问题；不具备治疗作用的否定警示应排除。": "Cosmetics should not claim to treat or cure disease or describe normal aging as curable. Exclude disclaimers explicitly denying treatment effects.",
    "不得承诺一次使用即可全天有效、防晒永不失效或必然产生特定效果。": "Do not guarantee all-day effectiveness after one use, permanent sun protection or inevitable results.",
    "适用肤质应有合理范围，不得承诺所有肤质都不会过敏；有个体差异及测试提醒的陈述不属于保证。": "Specify a reasonable skin-type range. Do not guarantee no allergies for every skin type; individual-variation and patch-test warnings are not guarantees.",
    "使用方式应说明基本操作，随意使用、想怎么用就怎么用等占位语不能指导正确使用。": "Directions should describe basic usage. Vague instructions such as use however you like are insufficient.",
    "注意事项不能以无需注意、无任何禁忌等安全保证替代，应提供标签中的实际限制。": "Precautions should state actual label restrictions, rather than guarantee that no precautions or contraindications exist.",
    "不得仅因含有某成分就推断成品必然具有治疗、永久修复或其他未经证实的效果。": "The presence of an ingredient does not establish that the finished product treats disease, repairs permanently or has other unverified effects.",
    "3C商品应说明品牌、型号、规格、兼容性和保修期限；字段缺失可汇总为关键信息缺失。": "Electronics listings should provide brand, model, specifications, compatibility and warranty. Missing fields may be grouped as missing required information.",
    "3C商品不得承诺永久有效、兼容所有设备或永不损坏；引用并否定这些说法的兼容性提醒应排除。": "Electronics should not guarantee permanent effectiveness, universal compatibility or zero breakage. Exclude warnings that quote and deny these claims.",
    "应给出支持的接口、协议、系统或型号；空值、都可以等表述不能替代具体兼容性说明。该标签与关键信息缺失粒度不同，评测不自动合并。": "List supported interfaces, protocols, systems or models. Empty or universal statements do not specify compatibility. This label remains distinct from missing required information during evaluation.",
    "应说明实际保修期限及必要范围；空值、见客服等无法确定期限的表述应补充。该标签与关键信息缺失粒度不同，评测不自动合并。": "Provide the actual warranty duration and scope. Empty values or contact support do not establish a period. This label remains distinct from missing required information during evaluation.",
    "规格字段应说明核心参数，标准版、参数见图等占位语无法替代可读取的具体规格。": "Specify readable core parameters. Placeholders such as standard version or see image do not provide specifications.",
    "普通电子产品不得将护眼、舒适等性能夸大为消除视力疲劳或治疗疾病的保证。": "Ordinary electronics should not exaggerate comfort or eye-care features into guarantees of eliminating eye strain or treating disease.",
    "不得承诺所有户型、任何距离或任何网络环境下性能均不受影响。": "Do not guarantee unchanged performance in every building layout, at any distance or under all network conditions.",
    "3C商品标题与描述声明的型号、容量、功率等应与结构化规格一致，单位等价换算不应视为冲突。": "Model, capacity and power claims in electronics copy should match structured specifications. Equivalent unit conversions are not contradictions.",
    "普通商品不得宣称治疗或根治疾病。应结合上下文排除明确否定医疗作用的警示；类目已有同义问题时不应重复累计相同证据。": "Ordinary products should not claim to treat or cure disease. Exclude explicit medical disclaimers and avoid counting identical evidence again under synonymous category rules.",
    "标题过长": "Title exceeds the length limit", "关键词堆砌": "Keyword stuffing", "标题核心属性缺失": "Missing identifying details in title",
    "属性与文案冲突": "Copy conflicts with structured attributes", "关键信息缺失": "Missing required information", "属性类型无效": "Invalid attribute type",
    "医疗功效宣称": "Medical treatment claim", "医疗功效暗示": "Implied medical treatment claim", "绝对化表达": "Absolute or unverifiable claim",
    "夸大宣传": "Exaggerated claim", "过度承诺": "Unsupported guarantee", "适用人群表述不当": "Unsupported audience claim",
    "保质期格式异常": "Invalid shelf-life format", "配料信息不明确": "Unclear ingredient information", "储存信息不明确": "Unclear storage requirements",
    "功效宣称风险": "Unsupported efficacy claim", "适用肤质表述不当": "Unsupported skin-type claim", "使用方式不明确": "Unclear directions for use",
    "注意事项不明确": "Unclear precautions", "成分功效推断": "Unsupported inference from ingredients", "兼容性说明缺失": "Missing compatibility details",
    "保修信息缺失": "Missing warranty details", "规格信息不明确": "Unclear specifications", "误导性功效": "Misleading efficacy claim", "输出结构无效": "Invalid report structure",
    "请先登录管理后台": "Please sign in to the administrator console.", "用户名或密码错误": "Incorrect username or password.",
    "请求来源不受信任": "The request origin is not trusted.", "CSRF 校验失败，请刷新页面后重试": "Your security token is invalid. Refresh the page and try again.",
    "登录尝试过多，请稍后重试": "Too many sign-in attempts. Please try again later.", "商品不存在": "Product not found.", "任务不存在": "Task not found.",
    "质检报告已变化，请重新打开报告并确认后发布。": "The inspection report has changed. Open and review the current report before publishing.",
    "商品状态已变化，请刷新后重试。": "The product status has changed. Refresh and try again.",
    "删除重复修饰词，保留品牌、品类和核心规格。": "Remove repeated modifiers; keep the brand, product type and core specifications.",
    "删除重复关键词，保留最相关的商品属性。": "Remove repeated keywords and keep relevant product attributes.",
    "删除治疗、绝对化或无法验证的承诺，改为客观描述商品信息。": "Remove treatment claims and unverifiable guarantees; describe the supplied product facts objectively.",
    "删除无法证实的承诺，保留已有的客观商品信息。": "Remove unsupported promises and retain the objective facts already supplied.",
    "在标题中保留品牌、品类和核心规格等识别信息。": "Keep identifying information such as the brand, product type and core specifications in the title.",
    "请填写可核对的保质期，如12个月。": "Specify a verifiable shelf life, such as 12 months.", "按包装列出实际配料。": "List the actual ingredients from the package.",
    "提供具体储存条件。": "Provide specific storage conditions.", "写明适用肤质及限制。": "Specify supported skin types and limitations.",
    "根据标签补充用法。": "Provide directions from the product label.", "根据标签补充注意事项。": "Provide precautions from the product label.",
    "提供具体设备、系统或协议范围。": "List the supported devices, systems or protocols.", "明确有限保修期限和条件。": "Specify the limited warranty period and conditions.",
    "提供可核对的数值及单位。": "Provide verifiable values and units.", "请结合真实商品信息复核。": "Review against the actual product information.",
    "未配置 DeepSeek API Key": "No DeepSeek API key is configured.",
    "规则": "Rules", "模型": "Model", "食品": "Food", "美妆": "Beauty", "3C 数码": "Electronics",
  });
  function suggestion(source, issue) {
    if (locale !== "en" || Object.hasOwn(messages, source)) return t(source);
    const missing = /^补充属性字段：(.+)。$/.exec(source || "");
    if (missing) return `Provide the missing attributes: ${missing[1].split(", ").map(field).join(", ")}.`;
    const conflict = /^统一(.+)信息，并以商品实际标签或规格为准。$/.exec(source || "");
    if (conflict) return "Reconcile the copy and structured attributes against the actual product label or specification sheet.";
    if (issue?.issue_type === "属性类型无效") return `Provide a verifiable text value for ${field(issue.field)}.`;
    // Unrecognized model prose remains visibly original; no fabricated translation.
    return source ? `Original guidance: ${source}` : "Review against the actual product information.";
  }
  window.I18n = Object.freeze({ t, register, apply, getLocale: () => locale, setLocale,
    onChange(callback) { listeners.add(callback); return () => listeners.delete(callback); },
    category, risk, field, error, reportSummary, issueLabel: t, suggestion, ruleText: t });
  window.addEventListener("storage", (event) => { if (event.key === storageKey && event.newValue) setLocale(event.newValue); });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => apply());
  else apply();
})();
