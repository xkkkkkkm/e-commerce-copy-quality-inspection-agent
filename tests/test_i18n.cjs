/* No npm dependencies: run with node --test tests/test_i18n.cjs. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync, existsSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const root = join(__dirname, '..');

function runtime({ stored, browserLanguage = 'en-US', blockedStorage = false, page = 'admin' } = {}) {
  const storage = new Map(stored ? [['quality-agent.locale', stored]] : []);
  const events = {};
  const elements = [];
  const document = { documentElement: { lang: '' }, readyState: 'loading',
    querySelectorAll: () => elements, addEventListener: (name, fn) => { events[name] = fn; } };
  const context = vm.createContext({ document, navigator: { language: browserLanguage },
    localStorage: { getItem(key) { if (blockedStorage) throw Error('denied'); return storage.get(key); },
      setItem(key, value) { if (blockedStorage) throw Error('denied'); storage.set(key, value); } },
    window: { addEventListener(name, fn) { events[name] = fn; }, dispatchEvent() {} },
    CustomEvent: class { constructor(type, detail) { this.type = type; this.detail = detail; } } });
  vm.runInContext(readFileSync(join(root, 'app/static/i18n.js'), 'utf8'), context);
  context.I18n = context.window.I18n;
  for (const file of [`${page}-messages.js`]) {
    const path = join(root, 'app/static', file);
    if (existsSync(path)) vm.runInContext(readFileSync(path, 'utf8'), context);
  }
  return { i18n: context.window.I18n, document, events, storage };
}

test('saved locale wins over browser locale and follows navigation', () => {
  const first = runtime({ stored: 'zh-CN' });
  assert.equal(first.i18n.getLocale(), 'zh-CN');
  first.i18n.setLocale('en');
  assert.equal(first.storage.get('quality-agent.locale'), 'en');
  const nextPage = runtime({ stored: first.storage.get('quality-agent.locale'), browserLanguage: 'zh-CN' });
  assert.equal(nextPage.i18n.getLocale(), 'en');
  assert.equal(nextPage.i18n.category('食品'), 'Food');
  assert.equal(nextPage.i18n.risk('high'), 'High risk');
});

test('language switching and interpolation work with denied storage', () => {
  const { i18n } = runtime({ blockedStorage: true });
  const observed = [];
  i18n.onChange(value => observed.push(value));
  i18n.register({ '共 {count} 件': '{count} products' });
  assert.equal(i18n.t('共 {count} 件', { count: 3 }), '3 products');
  i18n.setLocale('zh-CN');
  assert.equal(i18n.t('共 {count} 件', { count: 3 }), '共 3 件');
  assert.deepEqual(observed, ['zh-CN']);
});

test('tab-to-tab locale changes update document language', () => {
  const { events, i18n, document } = runtime({ stored: 'zh-CN' });
  events.storage({ key: 'quality-agent.locale', newValue: 'en' });
  assert.equal(i18n.getLocale(), 'en');
  assert.equal(document.documentElement.lang, 'en');
});

test('display adapters leave original report, copy, evidence and risk unchanged', () => {
  const { i18n } = runtime();
  const report = { summary: '原始中文报告', category: '食品', status: 'success', risk_level: 'high', mode: 'rules',
    rules: [{ rule_id: 'food_claim_001' }], issues: [{ issue_type: '医疗功效宣称', evidence: '治疗失眠', risk_level: 'high' }],
    optimized_title: '清风花茶', optimized_description: '20袋', retrieval_source: 'mysql' };
  const snapshot = JSON.stringify(report);
  assert.match(i18n.reportSummary(report), /1 issue identified · High risk/);
  assert.equal(i18n.issueLabel(report.issues[0].issue_type), 'Medical treatment claim');
  assert.equal(i18n.t('某商家未登记的中文原文'), '某商家未登记的中文原文');
  assert.equal(JSON.stringify(report), snapshot);
  assert.match(i18n.reportSummary({ ...report, mode: 'full', model_used: false, degraded: true }), /coverage is incomplete/);
});

for (const page of ['admin', 'workbench']) test(`${page}: bundled rules and rewrite guidance are translated independently`, () => {
  const { i18n } = runtime({ page });
  assert.equal(i18n.suggestion('未发现问题，保留原文。'), 'No issues were found. The original copy was retained.');
  const rules = JSON.parse(readFileSync(join(root, 'data/rules/rules.json'), 'utf8'));
  for (const rule of rules) {
    assert.notEqual(i18n.ruleText(rule.rule_text), rule.rule_text, rule.rule_id);
    assert.notEqual(i18n.issueLabel(rule.issue_type), rule.issue_type, rule.rule_id);
    assert.notEqual(i18n.suggestion(rule.rewrite_hint), rule.rewrite_hint, rule.rule_id);
    assert.doesNotMatch(i18n.suggestion(rule.rewrite_hint), /^Original guidance:/, rule.rule_id);
  }
});
