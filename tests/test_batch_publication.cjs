const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');
const root = join(__dirname, '..');
const product = (id, risk = 'pass') => ({
  id, product_id: `SKU-${id}`, title: `Product ${id}`, version: 1, category: '食品',
  status: 'pending', merchant_name: 'Test', attributes: {}, description: 'Original copy',
  inspection_fresh: true, inspection_complete: true, latest_risk: risk,
  latest_inspection_id: id * 10, inspected_version: 1, issue_count: 0,
  latest_report: { issues: risk === 'medium' ? [{ risk_level: risk, issue_type: '属性缺失', evidence: 'Missing value', suggestion: 'Check details' }] : [] },
});
const entry = p => ({ id: p.id, eligible: p.latest_risk !== 'high', product: p,
  error: p.latest_risk === 'high' ? '高风险商品不能发布，请修改内容并重新质检。' : null,
  snapshot: p.latest_risk === 'high' ? null : { id: p.id, expected_version: p.version, expected_status: p.status, expected_inspection_id: p.latest_inspection_id },
});
const settle = async w => { for (let i = 0; i < 4; i++) await new Promise(r => w.setTimeout(r, 0)); };

async function setup(t, overrides = {}) {
  const errors = [], requests = [];
  const vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(e.message));
  const dom = new JSDOM(readFileSync(join(root, 'app/static/admin.html'), 'utf8'), {
    url: 'https://quality.test/admin', runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc,
  });
  const w = dom.window, d = w.document;
  t.after(() => { w.close(); assert.deepEqual(errors, []); });
  w.localStorage.setItem('quality-agent.locale', 'zh-CN');
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new w.Event('close')); };
  const products = [product(1), product(2, 'medium'), product(3, 'high')];
  w.fetch = async (url, options) => {
    const path = url.replace('/api/admin', '');
    const body = options.body && JSON.parse(options.body);
    requests.push({ path, body });
    let result;
    if (overrides[path]) result = await overrides[path](body);
    else if (path === '/auth/me') result = { csrf_token: 'test-csrf', username: 'admin' };
    else if (path === '/auth/logout') result = {};
    else if (path === '/summary') result = { total: 3, pending: 3, high_risk: 1, merchants: 1 };
    else if (path.startsWith('/products?')) result = { items: products, total: products.length };
    else if (path === '/products/batch-publish/preview') {
      const items = body.items.map(x => entry(products.find(p => p.id === x.id)));
      result = { items, eligible_count: items.filter(x => x.eligible).length, blocked_count: items.filter(x => !x.eligible).length };
    } else if (path === '/products/batch-publish') {
      result = { items: body.items.map(x => ({ id: x.id, ok: true, product: { ...products.find(p => p.id === x.id), status: 'published' } })), success_count: body.items.length, failed_count: 0 };
    } else if (path === '/products/batch-inspect') {
      result = { items: body.items.map(x => ({ id: x.id, ok: true, result: { product: products.find(p => p.id === x.id), applied: true } })), success_count: body.items.length, failed_count: 0 };
    } else throw Error(`Unexpected request: ${path}`);
    return { ok: true, status: 200, text: async () => JSON.stringify(result) };
  };
  for (const file of ['i18n.js', 'admin-messages.js', 'admin.js']) w.eval(readFileSync(join(root, 'app/static', file), 'utf8'));
  await settle(w);
  const selectAll = () => { const input = d.getElementById('select-all'); input.checked = true; input.dispatchEvent(new w.Event('change', { bubbles: true })); };
  const open = async () => { selectAll(); d.getElementById('batch-publish').click(); await settle(w); };
  const confirm = () => {
    const note = d.getElementById('batch-publish-reason'); note.value = '已复核原文'; note.dispatchEvent(new w.Event('input', { bubbles: true }));
    const checkbox = d.getElementById('batch-publish-confirm'); checkbox.checked = true; checkbox.dispatchEvent(new w.Event('change', { bubbles: true }));
  };
  return { w, d, products, requests, open, confirm, selectAll };
}

test('preview blocks high risk, preselects only pass, and requires note and confirmation', async t => {
  const { d, open, confirm, w } = await setup(t); await open();
  assert.match(d.getElementById('batch-publish-summary').textContent, /2.*1/);
  const choices = [...d.querySelectorAll('.publication-card input')];
  assert.deepEqual(choices.map(x => [x.checked, x.disabled]), [[true, false], [false, false], [false, true]]);
  assert.equal(d.getElementById('batch-publish-submit').disabled, true);
  confirm(); assert.equal(d.getElementById('batch-publish-submit').disabled, false);
  choices[1].click();
  assert.equal(d.getElementById('batch-publish-confirm').checked, false);
  confirm(); w.I18n.setLocale('en');
  assert.equal(d.getElementById('batch-publish-reason').value, '已复核原文');
  assert.equal(d.getElementById('batch-publish-submit').textContent, 'Publish 2 products');
  assert.match(d.getElementById('batch-publish-summary').textContent, /Eligible for review: 2/);
});

test('submission uses reviewed snapshots, rejects duplicate clicks and keeps results per item', async t => {
  let finish;
  const { d, w, open, confirm, requests } = await setup(t, { '/products/batch-publish': () => new Promise(r => { finish = r; }) });
  await open(); confirm();
  const submit = d.getElementById('batch-publish-submit'); submit.click(); submit.click();
  assert.equal(requests.filter(r => r.path === '/products/batch-publish').length, 1);
  assert.deepEqual(requests.find(r => r.path === '/products/batch-publish').body, {
    items: [{ id: 1, expected_version: 1, expected_status: 'pending', expected_inspection_id: 10 }], reason: '已复核原文',
  });
  const esc = new w.Event('cancel', { cancelable: true }); d.getElementById('batch-publish-dialog').dispatchEvent(esc);
  assert.equal(esc.defaultPrevented, true);
  finish({ items: [{ id: 1, ok: true }], success_count: 1, failed_count: 0 }); await settle(w);
  assert.match(d.getElementById('batch-publish-summary').textContent, /成功 1.*未提交 2/);
  assert.equal(d.querySelectorAll('#batch-publish-items .publication-card').length, 3);
  assert.equal(submit.hidden, true);
  assert.ok([...d.querySelectorAll('#products-body input[type=checkbox]')].every(x => !x.disabled));
});

test('uncertain response cannot be blindly resubmitted and recheck is available', async t => {
  const { d, w, open, confirm, requests } = await setup(t, { '/products/batch-publish': () => { throw new TypeError('network'); } });
  await open(); confirm(); d.getElementById('batch-publish-submit').click(); await settle(w);
  assert.equal(d.getElementById('batch-publish-submit').disabled, true);
  assert.equal(d.getElementById('batch-publish-recheck').hidden, false);
  assert.match(d.getElementById('batch-publish-summary').textContent, /部分商品可能已发布/);
  d.getElementById('batch-publish-submit').click();
  assert.equal(requests.filter(r => r.path === '/products/batch-publish').length, 1);
  assert.ok([...d.querySelectorAll('#products-body input[type=checkbox]')].every(x => !x.disabled));
});

test('partial failure offers only failed items for a fresh review', async t => {
  const { d, w, open, confirm, requests } = await setup(t, { '/products/batch-publish': () => ({ items: [{ id: 1, ok: true }, { id: 2, ok: false, error: '商品状态已变化，请刷新后重试。' }], success_count: 1, failed_count: 1 }) });
  await open(); d.querySelectorAll('.publication-card input')[1].click(); confirm();
  d.getElementById('batch-publish-submit').click(); await settle(w);
  assert.match(d.getElementById('batch-publish-summary').textContent, /成功 1.*失败 1/);
  d.getElementById('batch-publish-recheck').click(); await settle(w);
  assert.deepEqual(requests.filter(r => r.path === '/products/batch-publish/preview').at(-1).body.items, [{ id: 2 }]);
  assert.equal(d.getElementById('batch-publish-confirm').checked, false);
});

test('closing a pending preview discards its late response', async t => {
  let finish;
  const { d, w, open } = await setup(t, { '/products/batch-publish/preview': () => new Promise(r => { finish = r; }) });
  await open(); d.querySelector('[data-close="batch-publish-dialog"]').click();
  finish({ items: [entry(product(1))], eligible_count: 1, blocked_count: 0 }); await settle(w);
  assert.equal(d.getElementById('batch-publish-dialog').open, false);
  assert.equal(d.querySelectorAll('.publication-card').length, 0);
});

test('inspection result exposes publication continuation with original batch IDs', async t => {
  const { d, w, selectAll, requests } = await setup(t);
  selectAll(); d.getElementById('batch-inspect').click(); await settle(w);
  assert.equal(d.querySelectorAll('.batch-result-identity .product-meta').length, 3);
  d.querySelector('.batch-next-step button').click(); await settle(w);
  assert.equal(d.getElementById('batch-publish-dialog').open, true);
  assert.deepEqual(requests.find(r => r.path === '/products/batch-publish/preview').body.items.map(x => x.id), [1, 2, 3]);
});
