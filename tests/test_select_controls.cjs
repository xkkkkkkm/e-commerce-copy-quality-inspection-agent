/* Run with npm test. Layout, clipping and popup placement are covered in browser QA. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { JSDOM, VirtualConsole } = require('jsdom');

const componentPath = join(__dirname, '..', 'app/static/select-controls.js');

async function runtime(t, html) {
  const scriptErrors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => scriptErrors.push(error));
  const dom = new JSDOM(`<!doctype html><html><body>${html}</body></html>`, {
    runScripts: 'outside-only',
    pretendToBeVisual: true,
    url: 'https://quality-agent.test/',
    virtualConsole,
  });
  const { window } = dom;
  t.after(() => {
    window.close();
    assert.deepEqual(scriptErrors, [], 'component event handlers must not throw');
  });
  // jsdom has no layout. These stubs permit opening the menu, not placement assertions.
  window.HTMLElement.prototype.getBoundingClientRect = function () {
    return { x: 20, y: 20, top: 20, left: 20, right: 260, bottom: 60, width: 240, height: 40 };
  };
  window.HTMLElement.prototype.getClientRects = function () { return [this.getBoundingClientRect()]; };
  window.HTMLElement.prototype.scrollIntoView = function () {};
  window.eval(readFileSync(componentPath, 'utf8'));
  window.SelectControls.refresh();
  await settle(window);
  return { window, document: window.document, controls: window.SelectControls };
}

function settle(window) {
  return new Promise(resolve => window.setTimeout(resolve, 0));
}

function triggerFor(select) {
  const trigger = select.closest('.select-control')?.querySelector('.select-trigger');
  assert.ok(trigger, 'the native select has an accessible trigger');
  return trigger;
}

function menuFor(trigger) {
  const menu = trigger.ownerDocument.getElementById(trigger.getAttribute('aria-controls'));
  assert.ok(menu, 'aria-controls references the listbox');
  assert.equal(menu.getAttribute('role'), 'listbox');
  return menu;
}

function activeOption(trigger) {
  const option = trigger.ownerDocument.getElementById(trigger.getAttribute('aria-activedescendant'));
  assert.ok(option, 'the combobox identifies its active option');
  assert.equal(option.getAttribute('role'), 'option');
  return option;
}

function optionNamed(trigger, name) {
  const option = [...menuFor(trigger).querySelectorAll('[role="option"]')]
    .find(item => accessibleText(item) === name);
  assert.ok(option, `the listbox contains ${name}`);
  return option;
}

function accessibleText(element) {
  const copy = element.cloneNode(true);
  copy.querySelectorAll('[aria-hidden="true"]').forEach(node => node.remove());
  return copy.textContent.trim();
}

function key(window, element, value, extra = {}) {
  const event = new window.KeyboardEvent('keydown', {
    key: value, bubbles: true, cancelable: true, ...extra,
  });
  element.dispatchEvent(event);
  return event;
}

function assertOpen(trigger, expected) {
  assert.equal(trigger.getAttribute('aria-expanded'), String(expected));
}

const choices = '<option value="a">Alpha</option><option value="b">Beta</option><option value="c">Charlie</option>';

test('pointer selection preserves native form values and emits input then change exactly once', async t => {
  const { window, document, controls } = await runtime(t, `<form><label for="category">Category</label><select id="category" name="category">${choices}</select></form>`);
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  assert.equal(trigger.getAttribute('role'), 'combobox');
  assert.equal(trigger.getAttribute('aria-haspopup'), 'listbox');
  assert.ok(select.classList.contains('select-native'));
  const events = [];
  select.form.addEventListener('input', event => events.push([event.type, event.target, select.value]));
  select.form.addEventListener('change', event => events.push([event.type, event.target, select.value]));

  trigger.click();
  optionNamed(trigger, 'Beta').click();
  assert.equal(select.value, 'b');
  assert.equal(new window.FormData(select.form).get('category'), 'b');
  assert.deepEqual(events.map(([type, target, value]) => [type, target === select, value]), [
    ['input', true, 'b'], ['change', true, 'b'],
  ]);
  assert.match(trigger.textContent, /Beta/);
  assertOpen(trigger, false);
  assert.equal(document.activeElement, trigger);

  trigger.click();
  assert.equal(optionNamed(trigger, 'Beta').getAttribute('aria-selected'), 'true');
  optionNamed(trigger, 'Beta').click();
  assert.equal(events.length, 2, 'reselecting the current value emits no duplicate events');
  controls.refresh();
  controls.refresh(select);
  assert.equal(document.querySelectorAll('select').length, 1);
  assert.equal(document.querySelectorAll('.select-trigger').length, 1);
  assert.equal(document.querySelector('select'), select, 'the original form control is retained');
});

test('arrows, Home and End navigate enabled options before Enter commits', async t => {
  const { window, document } = await runtime(t, `<select>${choices.replace('<option value="b">', '<option disabled value="b">')}</select>`);
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  trigger.focus();
  key(window, trigger, 'ArrowDown');
  assertOpen(trigger, true);
  assert.equal(accessibleText(activeOption(trigger)), 'Alpha');
  key(window, trigger, 'ArrowDown');
  assert.equal(accessibleText(activeOption(trigger)), 'Charlie');
  assert.equal(select.value, 'a', 'highlighting alone does not alter the form value');
  key(window, trigger, 'ArrowUp');
  assert.equal(accessibleText(activeOption(trigger)), 'Alpha');
  key(window, trigger, 'End');
  assert.equal(accessibleText(activeOption(trigger)), 'Charlie');
  key(window, trigger, 'Home');
  assert.equal(accessibleText(activeOption(trigger)), 'Alpha');
  key(window, trigger, 'End');
  key(window, trigger, 'Enter');
  assert.equal(select.value, 'c');
  assertOpen(trigger, false);
  assert.equal(document.activeElement, trigger);
});

test('Space opens and commits; Tab commits while allowing normal focus navigation', async t => {
  const { window, document } = await runtime(t, `<select>${choices}</select><button id="next">Next</button>`);
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  assert.equal(key(window, trigger, ' ').defaultPrevented, true);
  assertOpen(trigger, true);
  key(window, trigger, 'ArrowDown');
  key(window, trigger, ' ');
  assert.equal(select.value, 'b');
  assertOpen(trigger, false);

  key(window, trigger, 'Enter');
  key(window, trigger, 'ArrowDown');
  const tab = key(window, trigger, 'Tab');
  assert.equal(tab.defaultPrevented, false, 'the browser must retain its normal Tab behavior');
  assert.equal(select.value, 'c');
  assertOpen(trigger, false);
});

test('Escape cancels the open choice without dismissing its parent dialog', async t => {
  const { window, document } = await runtime(t, `<dialog open><select>${choices}</select></dialog>`);
  const dialog = document.querySelector('dialog');
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  let parentEscapes = 0;
  dialog.addEventListener('keydown', event => { if (event.key === 'Escape') parentEscapes += 1; });
  trigger.click();
  assert.ok(dialog.contains(menuFor(trigger)), 'a dialog menu remains inside the dialog');
  key(window, trigger, 'ArrowDown');
  const escape = key(window, trigger, 'Escape');
  assert.equal(escape.defaultPrevented, true);
  assert.equal(parentEscapes, 0);
  assert.equal(select.value, 'a');
  assertOpen(trigger, false);
  assert.equal(document.activeElement, trigger);
  key(window, trigger, 'Escape');
  assert.equal(parentEscapes, 1, 'Escape belongs to the dialog once the listbox is closed');
});

test('typeahead and pointer selection skip disabled options and disabled optgroups', async t => {
  const { window, document } = await runtime(t, `<select>
    <option value="a">Alpha</option><option disabled value="beta">Beta</option>
    <optgroup label="Unavailable" disabled><option value="brick">Brick</option></optgroup>
    <optgroup label="Available"><option value="bravo">Bravo</option><option value="c">Charlie</option></optgroup>
  </select>`);
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  trigger.click();
  const beta = optionNamed(trigger, 'Beta');
  const brick = optionNamed(trigger, 'Brick');
  assert.equal(beta.getAttribute('aria-disabled'), 'true');
  assert.equal(brick.getAttribute('aria-disabled'), 'true');
  beta.click();
  brick.click();
  assert.equal(select.value, 'a');
  assertOpen(trigger, true);
  key(window, trigger, 'b');
  key(window, trigger, 'r');
  assert.equal(accessibleText(activeOption(trigger)), 'Bravo');
  assert.equal(select.value, 'a');
  key(window, trigger, 'Enter');
  assert.equal(select.value, 'bravo');
});

test('disabled native selects and inherited fieldset disabling prevent opening', async t => {
  const { window, document, controls } = await runtime(t, `<select id="direct" disabled>${choices}</select><fieldset disabled><select id="inherited">${choices}</select></fieldset>`);
  for (const select of document.querySelectorAll('select')) {
    const trigger = triggerFor(select);
    trigger.click();
    key(window, trigger, 'ArrowDown');
    assertOpen(trigger, false);
    assert.equal(select.value, 'a');
    assert.ok(trigger.disabled || trigger.getAttribute('aria-disabled') === 'true');
  }
  document.querySelector('fieldset').disabled = false;
  controls.refresh();
  const inherited = triggerFor(document.querySelector('#inherited'));
  inherited.click();
  assertOpen(inherited, true);
});

test('programmatic values and native change events update presentation without extra events', async t => {
  const { window, document, controls } = await runtime(t, `<select>${choices}</select>`);
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  const events = [];
  select.addEventListener('input', () => events.push('input'));
  select.addEventListener('change', () => events.push('change'));
  select.value = 'b';
  controls.refresh(select);
  assert.match(trigger.textContent, /Beta/);
  assert.deepEqual(events, []);
  select.value = 'c';
  select.dispatchEvent(new window.Event('change', { bubbles: true }));
  assert.match(trigger.textContent, /Charlie/);
  assert.deepEqual(events, ['change']);
  trigger.click();
  assert.equal(optionNamed(trigger, 'Charlie').getAttribute('aria-selected'), 'true');
  assert.equal(accessibleText(activeOption(trigger)), 'Charlie');
});

test('form.reset restores the default selection and visible label without change events', async t => {
  const { window, document } = await runtime(t, `<form><select name="category">${choices.replace('value="b"', 'value="b" selected')}</select></form>`);
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  trigger.click();
  optionNamed(trigger, 'Charlie').click();
  let changes = 0;
  select.addEventListener('change', () => { changes += 1; });
  select.form.reset();
  await settle(window);
  assert.equal(select.value, 'b');
  assert.match(trigger.textContent, /Beta/);
  assert.equal(changes, 0);
  assert.equal(new window.FormData(select.form).get('category'), 'b');
});

test('native required validation focuses the visible trigger and clears after selection', async t => {
  const { document } = await runtime(t, '<form><label for="required-choice">Category</label><select id="required-choice" name="category" required><option value="">Choose a category</option><option value="food">Food</option></select></form>');
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  assert.equal(select.willValidate, true);
  assert.equal(select.form.checkValidity(), false);
  assert.equal(document.activeElement, trigger);
  assert.equal(trigger.getAttribute('aria-invalid'), 'true');
  trigger.click();
  optionNamed(trigger, 'Food').click();
  assert.equal(select.form.checkValidity(), true);
  assert.notEqual(trigger.getAttribute('aria-invalid'), 'true');
});

test('translated option and accessible label text refresh without altering values or firing changes', async t => {
  const { document, controls } = await runtime(t, '<select aria-label="商品类别"><option value="食品">食品</option><option value="美妆">美妆</option></select>');
  const select = document.querySelector('select');
  const trigger = triggerFor(select);
  const values = [...select.options].map(option => option.value);
  let changes = 0;
  select.addEventListener('change', () => { changes += 1; });
  select.setAttribute('aria-label', 'Product category');
  select.options[0].textContent = 'Food';
  select.options[1].textContent = 'Beauty';
  controls.refresh(select);
  assert.match(trigger.textContent, /Food/);
  assert.equal(trigger.getAttribute('aria-label'), 'Product category');
  trigger.click();
  assert.equal(optionNamed(trigger, 'Food').getAttribute('aria-selected'), 'true');
  assert.ok(optionNamed(trigger, 'Beauty'));
  assert.deepEqual([...select.options].map(option => option.value), values);
  assert.equal(select.value, '食品');
  assert.equal(changes, 0);
});

test('dynamically inserted selects are enhanced and removing them cleans their popup', async t => {
  const { window, document } = await runtime(t, '<div id="dynamic"></div>');
  const host = document.querySelector('#dynamic');
  host.innerHTML = `<select name="dynamic">${choices}</select>`;
  await settle(window);
  const select = host.querySelector('select');
  const trigger = triggerFor(select);
  trigger.click();
  const menu = menuFor(trigger);
  assert.ok(menu.isConnected);
  host.remove();
  await settle(window);
  assert.equal(menu.isConnected, false, 'removing a control also removes its portaled menu');
  assert.equal(document.querySelectorAll('.select-menu').length, 0);
});

test('opening another control, outside clicks and closeAll dismiss open menus', async t => {
  const { window, document, controls } = await runtime(t, `<select id="first">${choices}</select><select id="second">${choices}</select><button id="outside">Outside</button>`);
  const first = triggerFor(document.querySelector('#first'));
  const second = triggerFor(document.querySelector('#second'));
  first.click();
  assertOpen(first, true);
  second.click();
  assertOpen(first, false);
  assertOpen(second, true);
  document.querySelector('#outside').dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true }));
  document.querySelector('#outside').click();
  assertOpen(second, false);
  first.click();
  controls.closeAll();
  assertOpen(first, false);
  assert.equal(document.querySelector('#first').value, 'a');
});
