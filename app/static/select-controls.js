(() => {
  "use strict";

  // Leave ordinary, usable native controls in place on older browsers.
  if (!window.MutationObserver || !Element.prototype.closest || !Element.prototype.replaceChildren || !window.requestAnimationFrame || !window.Map || !window.Set) return;

  const selector = "select:not([multiple]):not([size]):not([data-native-select])";
  const controls = new Map();
  const supportsPopover = typeof HTMLElement.prototype.showPopover === "function";
  let serial = 0;
  let opened = null;
  let positionFrame = null;

  const setAttribute = (node, name, value) => {
    if (value === null || value === undefined || value === "") {
      if (node.hasAttribute(name)) node.removeAttribute(name);
    } else if (node.getAttribute(name) !== String(value)) node.setAttribute(name, String(value));
  };
  const setText = (node, value) => {
    if (node.textContent !== value) node.textContent = value;
  };
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const listen = (state, node, name, handler, options) => {
    node.addEventListener(name, handler, options);
    state.listeners.push(() => node.removeEventListener(name, handler, options));
  };
  const optionDisabled = (option) => option.disabled || (option.parentElement?.tagName === "OPTGROUP" && option.parentElement.disabled);
  const optionHidden = (option) => option.hidden || (option.parentElement?.tagName === "OPTGROUP" && option.parentElement.hidden);
  const enabledItems = (state) => state.items.filter((item) => !optionDisabled(item.option) && !optionHidden(item.option));

  function isVisible(state) {
    if (!state.select.isConnected || !state.wrapper.isConnected || state.select.matches(":disabled")) return false;
    if (state.select.closest("[hidden], [inert]")) return false;
    const dialog = state.select.closest("dialog");
    if (dialog && !dialog.open) return false;
    const visibility = window.getComputedStyle(state.trigger).visibility;
    if (visibility === "hidden" || visibility === "collapse") return false;
    // A hidden ancestor removes the visible trigger from layout.
    return state.trigger.getClientRects().length > 0;
  }

  function labelText(select) {
    return Array.from(select.labels || []).map((label) => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll("select, input, textarea, button, .select-control").forEach((node) => node.remove());
      return copy.textContent.replace(/\s+/g, " ").trim();
    }).filter(Boolean).join(" ");
  }

  function syncAccessibility(state) {
    const { select, trigger, menu } = state;
    const labelledBy = select.getAttribute("aria-labelledby");
    const label = select.getAttribute("aria-label") || (!labelledBy ? labelText(select) : "");
    setAttribute(trigger, "aria-labelledby", labelledBy);
    setAttribute(trigger, "aria-label", label);
    setAttribute(menu, "aria-labelledby", labelledBy);
    setAttribute(menu, "aria-label", label);
    setAttribute(trigger, "aria-required", select.required ? "true" : null);
    const invalid = select.getAttribute("aria-invalid") || (state.wasInvalid && !select.validity.valid ? "true" : null);
    state.wrapper.classList.toggle("is-invalid", invalid === "true");
    setAttribute(trigger, "aria-invalid", invalid);
    setAttribute(trigger, "aria-errormessage", select.getAttribute("aria-errormessage"));
    setAttribute(trigger, "aria-describedby", select.getAttribute("aria-describedby"));
    setAttribute(trigger, "title", select.title || (invalid === "true" ? select.validationMessage : ""));
    for (const name of ["lang", "dir"]) {
      setAttribute(trigger, name, select.getAttribute(name));
      setAttribute(menu, name, select.getAttribute(name));
    }
  }

  function renderOptions(state) {
    const { select, menu } = state;
    const language = select.hasAttribute("data-language-switch");
    const options = Array.from(select.options);
    const signature = JSON.stringify([language, options.map((option) => [
      option.value, option.label, optionDisabled(option), optionHidden(option),
      option.parentElement?.tagName === "OPTGROUP" ? option.parentElement.label : null,
      option.lang,
    ])]);
    // Node identity matters when business code replaces options with identical text.
    const sameNodes = options.length === state.optionNodes.length && options.every((option, index) => option === state.optionNodes[index]);
    if (signature === state.signature && sameNodes) return;
    state.signature = signature;
    state.optionNodes = options;
    state.items = [];
    state.wrapper.classList.toggle("is-language", language);
    menu.classList.toggle("is-language", language);
    menu.classList.toggle("select-language-menu", language);
    const fragment = document.createDocumentFragment();
    let previousGroup = null;
    options.forEach((option, index) => {
      if (optionHidden(option)) return;
      const group = option.parentElement?.tagName === "OPTGROUP" ? option.parentElement : null;
      if (group && group !== previousGroup) {
        const heading = element("div", "select-group-label", group.label);
        heading.setAttribute("role", "presentation");
        fragment.append(heading);
      }
      previousGroup = group;
      const row = element("div", "select-option");
      row.id = `${state.id}-option-${index}`;
      row.dataset.index = String(index);
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", "false");
      row.classList.toggle("is-disabled", optionDisabled(option));
      setAttribute(row, "aria-disabled", optionDisabled(option) ? "true" : null);
      setAttribute(row, "lang", option.lang);
      const copy = element("span", "select-option-copy");
      let label = option.label;
      let detail = "";
      let code = "";
      if (language && option.value === "zh-CN") {
        label = "简体中文"; detail = "Chinese (Simplified)"; code = "中";
      } else if (language && option.value === "en") {
        label = "English"; detail = "英语"; code = "EN";
      }
      if (code) {
        const badge = element("span", "select-language-code", code);
        badge.setAttribute("aria-hidden", "true");
        row.append(badge);
      }
      copy.append(element("span", "select-option-label", label));
      if (detail) copy.append(element("span", "select-option-detail", detail));
      const check = element("span", "select-check", "✓");
      check.setAttribute("aria-hidden", "true");
      row.append(copy, check);
      fragment.append(row);
      state.items.push({ node: row, option, index, searchLabels: [option.label, label, detail].filter(Boolean) });
    });
    menu.replaceChildren(fragment);
  }

  function setActive(state, index, scroll = true) {
    const item = state.items.find((entry) => entry.index === index && !optionDisabled(entry.option));
    state.activeIndex = item ? index : -1;
    for (const entry of state.items) entry.node.classList.toggle("is-active", entry === item);
    setAttribute(state.trigger, "aria-activedescendant", opened === state && item ? item.node.id : null);
    if (scroll && item && state.menu.clientHeight) {
      // Scroll only the option list; scrollIntoView can also move its dialog or page.
      const top = item.node.offsetTop;
      const bottom = top + item.node.offsetHeight;
      if (top < state.menu.scrollTop) state.menu.scrollTop = top;
      else if (bottom > state.menu.scrollTop + state.menu.clientHeight) state.menu.scrollTop = bottom - state.menu.clientHeight;
    }
  }

  function sync(state) {
    const { select, trigger, wrapper } = state;
    if (!select.matches(selector)) { destroy(state); return; }
    trigger.disabled = select.matches(":disabled");
    wrapper.hidden = select.hidden;
    wrapper.classList.toggle("is-disabled", trigger.disabled);
    renderOptions(state);
    const selected = select.options[select.selectedIndex];
    setText(state.value, selected ? selected.label : "");
    for (const item of state.items) {
      const chosen = item.index === select.selectedIndex;
      item.node.classList.toggle("is-selected", chosen);
      setAttribute(item.node, "aria-selected", String(chosen));
    }
    syncAccessibility(state);
    if (opened === state) {
      if (trigger.disabled || wrapper.hidden || !state.items.length) close(state);
      else {
        const available = enabledItems(state);
        if (!available.some((item) => item.index === state.activeIndex)) setActive(state, available[0]?.index ?? -1, false);
        else setActive(state, state.activeIndex, false);
        schedulePosition();
      }
    }
  }

  function position(state) {
    if (opened !== state) return;
    if (!state.menu.isConnected || state.menu.parentElement !== (state.select.closest("dialog") || document.body) || !isVisible(state)) {
      close(state); return;
    }
    const rect = state.trigger.getBoundingClientRect();
    const viewport = window.visualViewport;
    const leftEdge = viewport?.offsetLeft || 0;
    const topEdge = viewport?.offsetTop || 0;
    const width = viewport?.width || document.documentElement.clientWidth || window.innerWidth;
    const height = viewport?.height || document.documentElement.clientHeight || window.innerHeight;
    if (rect.bottom <= topEdge || rect.top >= topEdge + height || rect.right <= leftEdge || rect.left >= leftEdge + width) {
      close(state); return;
    }
    const margin = 8;
    const gap = 6;
    const desiredWidth = Math.max(0, Math.min(Math.max(rect.width, state.select.hasAttribute("data-language-switch") ? 232 : 196), width - margin * 2));
    const left = Math.max(leftEdge + margin, Math.min(rect.left, leftEdge + width - desiredWidth - margin));
    const below = Math.max(0, topEdge + height - rect.bottom - gap - margin);
    const above = Math.max(0, rect.top - topEdge - gap - margin);
    const estimatedHeight = Math.min(320, state.menu.scrollHeight || state.items.length * 44 + 12);
    const placeAbove = below < estimatedHeight && above > below;
    const maxHeight = Math.max(0, Math.min(320, placeAbove ? above : below));
    state.menu.style.setProperty("position", "fixed");
    state.menu.style.setProperty("left", `${Math.round(left)}px`);
    state.menu.style.setProperty("width", `${Math.round(desiredWidth)}px`);
    state.menu.style.setProperty("max-height", `${Math.floor(maxHeight)}px`);
    state.menu.style.setProperty("margin", "0");
    state.menu.style.setProperty("right", "auto");
    state.menu.style.setProperty("bottom", "auto");
    const menuHeight = Math.min(state.menu.getBoundingClientRect().height || estimatedHeight, maxHeight);
    const top = placeAbove ? rect.top - gap - menuHeight : rect.bottom + gap;
    state.menu.style.setProperty("top", `${Math.round(Math.max(topEdge + margin, top))}px`);
    state.menu.dataset.placement = placeAbove ? "top" : "bottom";
  }

  function schedulePosition() {
    if (!opened || positionFrame !== null) return;
    positionFrame = window.requestAnimationFrame(() => {
      positionFrame = null;
      if (opened) position(opened);
    });
  }

  function open(state, edge) {
    sync(state);
    if (!controls.has(state.select) || state.trigger.disabled || !state.items.length || state.wrapper.hidden) return;
    if (opened && opened !== state) close(opened);
    const host = state.select.closest("dialog") || document.body;
    if (host.tagName === "DIALOG" && !host.open) return;
    if (state.menu.parentElement !== host) host.append(state.menu);
    opened = state;
    state.typeBuffer = "";
    state.menu.hidden = false;
    state.menu.classList.add("is-open");
    state.wrapper.classList.add("is-open");
    state.trigger.setAttribute("aria-expanded", "true");
    if (supportsPopover && state.menu.hasAttribute("popover")) {
      try { state.menu.showPopover(); }
      catch (_) { state.menu.removeAttribute("popover"); }
    }
    const available = enabledItems(state);
    const selected = available.find((item) => item.index === state.select.selectedIndex);
    const item = edge === "last" ? available[available.length - 1] : edge === "first" ? available[0] : selected || available[0];
    state.trigger.focus({ preventScroll: true });
    position(state);
    setActive(state, item?.index ?? -1);
  }

  function close(state, restoreFocus = false) {
    if (!state || opened !== state) return;
    opened = null;
    state.typeBuffer = "";
    if (supportsPopover && state.menu.hasAttribute("popover")) {
      try { state.menu.hidePopover(); } catch (_) { /* Already closed by a dialog transition. */ }
    }
    state.menu.hidden = true;
    state.menu.classList.remove("is-open");
    state.wrapper.classList.remove("is-open");
    state.trigger.setAttribute("aria-expanded", "false");
    state.trigger.removeAttribute("aria-activedescendant");
    if (restoreFocus && state.trigger.isConnected && !state.trigger.disabled) state.trigger.focus({ preventScroll: true });
  }

  function choose(state, index, restoreFocus = true) {
    const option = state.select.options[index];
    if (!option || optionDisabled(option) || optionHidden(option) || state.select.matches(":disabled")) return;
    const changed = state.select.selectedIndex !== index;
    state.select.selectedIndex = index;
    close(state, restoreFocus);
    sync(state);
    if (changed) {
      state.select.dispatchEvent(new Event("input", { bubbles: true }));
      state.select.dispatchEvent(new Event("change", { bubbles: true }));
      if (controls.get(state.select) === state && state.select.isConnected) sync(state);
    }
  }

  function typeahead(state, key) {
    const now = Date.now();
    state.typeBuffer = now - state.typeTime > 700 ? key : state.typeBuffer + key;
    state.typeTime = now;
    const buffer = state.typeBuffer.toLocaleLowerCase();
    const repeated = Array.from(buffer).every((letter) => letter === buffer[0]);
    const search = repeated ? buffer[0] : buffer;
    const available = enabledItems(state);
    if (!available.length) return;
    const current = opened === state ? state.activeIndex : state.select.selectedIndex;
    const currentOffset = available.findIndex((item) => item.index === current);
    const start = currentOffset + (search.length === 1 ? 1 : 0);
    for (let offset = 0; offset < available.length; offset += 1) {
      const item = available[(Math.max(0, start) + offset) % available.length];
      if (item.searchLabels.some((label) => label.trim().toLocaleLowerCase().startsWith(search))) {
        if (opened === state) setActive(state, item.index);
        else choose(state, item.index, false);
        break;
      }
    }
  }

  function keydown(state, event) {
    if (state.trigger.disabled || event.ctrlKey || event.metaKey || event.isComposing) return;
    const key = event.key;
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(key)) {
      event.preventDefault();
      if (opened !== state) {
        open(state, key === "Home" ? "first" : key === "End" ? "last" : undefined);
      } else {
        const available = enabledItems(state);
        if (!available.length) return;
        const current = available.findIndex((item) => item.index === state.activeIndex);
        const index = key === "Home" ? 0 : key === "End" ? available.length - 1
          : (current + (key === "ArrowDown" ? 1 : -1) + available.length) % available.length;
        setActive(state, available[index].index);
      }
    } else if (key === "Enter" || key === " ") {
      event.preventDefault();
      if (opened === state) choose(state, state.activeIndex);
      else open(state);
    } else if (key.length === 1 && !event.altKey) {
      event.preventDefault();
      typeahead(state, key);
    }
  }

  function enhance(select) {
    if (!select.matches(selector) || controls.has(select) || !select.parentNode) return;
    const id = `select-control-${++serial}`;
    const wrapper = element("span", "select-control");
    const trigger = element("button", "select-trigger");
    trigger.type = "button";
    trigger.id = `${id}-trigger`;
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", `${id}-menu`);
    const value = element("span", "select-value");
    const chevron = element("span", "select-chevron");
    chevron.setAttribute("aria-hidden", "true");
    trigger.append(value, chevron);
    const menu = element("div", "select-menu");
    menu.id = `${id}-menu`;
    menu.setAttribute("role", "listbox");
    menu.hidden = true;
    if (supportsPopover) menu.setAttribute("popover", "manual");
    const state = {
      id, select, wrapper, trigger, value, menu, listeners: [], items: [], optionNodes: [],
      signature: null, activeIndex: -1, wasInvalid: false, typeBuffer: "", typeTime: 0,
      originalTabIndex: select.getAttribute("tabindex"), originalAriaHidden: select.getAttribute("aria-hidden"),
      hadNativeClass: select.classList.contains("select-native"),
    };
    controls.set(select, state);
    select.before(wrapper);
    wrapper.append(select, trigger);
    select.classList.add("select-native");
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");
    if (state.originalTabIndex !== null) trigger.setAttribute("tabindex", state.originalTabIndex);
    (select.closest("dialog") || document.body).append(menu);
    listen(state, trigger, "click", () => opened === state ? close(state) : open(state));
    listen(state, trigger, "keydown", (event) => keydown(state, event));
    listen(state, menu, "mousedown", (event) => event.preventDefault());
    listen(state, menu, "pointerdown", (event) => event.preventDefault());
    listen(state, menu, "pointermove", (event) => {
      const row = event.target.closest(".select-option");
      if (row && menu.contains(row) && !row.classList.contains("is-disabled")) setActive(state, Number(row.dataset.index), false);
    });
    listen(state, menu, "click", (event) => {
      const row = event.target.closest(".select-option");
      if (row && menu.contains(row)) { event.stopPropagation(); choose(state, Number(row.dataset.index)); }
    });
    listen(state, menu, "toggle", (event) => {
      if (event.newState === "closed" && opened === state) close(state);
    });
    listen(state, select, "input", () => sync(state));
    listen(state, select, "change", () => sync(state));
    listen(state, select, "focus", () => {
      if (!trigger.disabled) trigger.focus({ preventScroll: true });
    });
    listen(state, select, "invalid", (event) => {
      event.preventDefault();
      state.wasInvalid = true;
      sync(state);
      const firstInvalid = select.form && Array.from(select.form.elements).find((field) => field.willValidate && !field.validity.valid);
      if (!firstInvalid || firstInvalid === select) trigger.focus();
    });
    sync(state);
  }

  function destroy(state) {
    close(state);
    controls.delete(state.select);
    for (const dispose of state.listeners) dispose();
    state.menu.remove();
    if (!state.hadNativeClass) state.select.classList.remove("select-native");
    setAttribute(state.select, "tabindex", state.originalTabIndex);
    setAttribute(state.select, "aria-hidden", state.originalAriaHidden);
    if (state.select.parentNode === state.wrapper) state.wrapper.replaceWith(state.select);
    else state.wrapper.remove();
  }

  function refresh(root = document) {
    for (const state of controls.values()) {
      if (!state.select.isConnected || state.select.parentNode !== state.wrapper || !state.select.matches(selector)) destroy(state);
    }
    if (root.nodeType === 1 && root.matches("select")) {
      if (controls.has(root)) sync(controls.get(root));
      else enhance(root);
    }
    if (typeof root.querySelectorAll === "function") root.querySelectorAll("select").forEach((select) => {
      if (controls.has(select)) sync(controls.get(select));
      else enhance(select);
    });
  }

  const observer = new MutationObserver((records) => {
    const changed = new Set();
    const added = new Set();
    let cleanup = false;
    for (const record of records) {
      const target = record.target.nodeType === 1 ? record.target : record.target.parentElement;
      if (!target) continue;
      const source = target.closest("select");
      if (source) changed.add(source);
      // Changes to generated rows and triggers never feed back into their source.
      if (!source && (target.closest(".select-menu") || target.closest(".select-trigger"))) continue;
      if (record.type === "childList") {
        if (record.removedNodes.length) cleanup = true;
        for (const node of record.addedNodes) {
          if (node.nodeType === 1 && (node.matches("select") || node.querySelector("select"))) added.add(node);
        }
      }
      const label = target.closest("label");
      if (label && !target.closest(".select-control")) {
        label.querySelectorAll("select").forEach((select) => changed.add(select));
        if (label.control?.tagName === "SELECT") changed.add(label.control);
      }
      if (record.type === "attributes") {
        if (target.matches("fieldset") && record.attributeName === "disabled") target.querySelectorAll("select").forEach((select) => changed.add(select));
        if (target.matches("label") && record.attributeName === "for") controls.forEach((state) => changed.add(state.select));
        if (opened && target.tagName === "DIALOG" && record.attributeName === "open" && target.open && !target.contains(opened.select)) close(opened);
        if (opened && (target === opened.select || target.contains(opened.wrapper))) schedulePosition();
      }
    }
    if (cleanup) {
      for (const state of controls.values()) {
        if (!state.select.isConnected || state.select.parentNode !== state.wrapper) destroy(state);
      }
      if (opened) schedulePosition();
    }
    for (const root of added) if (root.isConnected) refresh(root);
    for (const select of changed) if (select.isConnected) {
      const state = controls.get(select);
      if (state) sync(state);
      else enhance(select);
    }
  });

  function start() {
    refresh();
    observer.observe(document.documentElement, {
      subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ["disabled", "selected", "value", "label", "hidden", "inert", "multiple", "size", "data-native-select", "data-language-switch", "aria-label", "aria-labelledby", "aria-describedby", "aria-invalid", "aria-errormessage", "required", "name", "id", "form", "for", "title", "lang", "dir", "open", "class", "style"],
    });
  }

  document.addEventListener("pointerdown", (event) => {
    if (opened && !opened.wrapper.contains(event.target) && !opened.menu.contains(event.target)) close(opened);
  }, true);
  document.addEventListener("focusin", (event) => {
    if (opened && !opened.wrapper.contains(event.target) && !opened.menu.contains(event.target)) close(opened);
  });
  document.addEventListener("keydown", (event) => {
    if (!opened) return;
    if (event.key === "Escape") {
      event.preventDefault(); event.stopPropagation(); close(opened, true);
    } else if (event.key === "Tab") {
      const state = opened;
      if (state.activeIndex >= 0) choose(state, state.activeIndex, false);
      else close(state);
    }
  }, true);
  // Forward visible label activation to the replacement, keeping label/form markup intact.
  document.addEventListener("click", (event) => {
    const label = event.target.closest?.("label");
    const state = label?.control && controls.get(label.control);
    if (!state || event.target.closest("button, input, textarea, a, .select-menu") || state.select.matches(":disabled")) return;
    event.preventDefault();
    state.trigger.focus();
    if (opened !== state) open(state);
  });
  document.addEventListener("reset", (event) => {
    // The reset event precedes the browser's restoration of defaultSelected values.
    window.setTimeout(() => {
      if (event.defaultPrevented) return;
      controls.forEach((state) => {
        if (state.select.form === event.target) { state.wasInvalid = false; close(state); sync(state); }
      });
    }, 0);
  }, true);
  for (const name of ["close", "cancel"]) document.addEventListener(name, (event) => {
    if (opened && event.target.tagName === "DIALOG" && event.target.contains(opened.select)) close(opened);
  }, true);
  window.addEventListener("resize", schedulePosition);
  document.addEventListener("scroll", schedulePosition, true);
  window.visualViewport?.addEventListener("resize", schedulePosition);
  window.visualViewport?.addEventListener("scroll", schedulePosition);
  window.SelectControls = Object.freeze({ refresh, closeAll: () => close(opened) });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
})();
