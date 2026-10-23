/* Local-only layout metadata. No meeting text, requests, or observers. */
(function () {
  'use strict';
  const KEY = 'local-meeting-layout-v1';
  const selectors = {dashboard: '#dashboard', live: '#analyses', final: '.final-grid', custom: '#finalCustomResults'};
  const defaults = new Map();
  const attached = new WeakSet();
  const resetButtons = new WeakSet();
  let state = {version: 1, groups: {}};
  let loaded = false;
  let dragging = null;
  const wiredRoots = new WeakSet();

  function wireRoot(group, root) {
    if (wiredRoots.has(root)) return;
    wiredRoots.add(root);
    const targetBlock = event => {
      const node = event.target.closest('[data-layout-id]');
      return node && node.parentElement === root ? node : null;
    };
    root.addEventListener('dragover', event => {
      if (!dragging || dragging.root !== root || !targetBlock(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
    });
    root.addEventListener('drop', event => {
      const target = targetBlock(event);
      if (!dragging || dragging.root !== root || !target || target === dragging.node) return;
      event.preventDefault();
      event.stopPropagation();
      const list = blocks(root), node = dragging.node;
      const rect = target.getBoundingClientRect();
      const after = event.clientY > rect.top + rect.height / 2;
      const next = list[list.indexOf(target) + 1];
      if (after && !next) root.append(node);
      else root.insertBefore(node, after ? next : target);
      capture(group, root, true);
      dragging = null;
    });
  }

  function status(message) {
    const node = document.getElementById('layoutStatus');
    if (node) {
      node.setAttribute('role', 'status');
      node.setAttribute('aria-live', 'polite');
      node.textContent = message;
    }
  }
  function blocks(root) {
    return Array.from(root.children).filter(node => node.dataset.layoutId);
  }
  function save() {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(state));
      status('Layout salvo somente neste navegador.');
    } catch (_) {
      status('Não foi possível salvar o layout neste navegador; alterações são temporárias.');
    }
  }
  const dimensions = new WeakMap();
  const activeResizes = new WeakMap();
  const clamp = (value, min, max) => Math.max(min, Math.min(max, Math.round(value)));
  const narrow = () => window.matchMedia('(max-width: 760px)').matches;
  function defaultSize(group, id) {
    return {span: group === 'live' || group === 'custom' ? 4 :
      id === 'configPanel' || id === 'transcriptPanel' ? 6 : 12, height: 'auto'};
  }
  function applySize(node, size) {
    dimensions.set(node, {span: size.span, height: size.height});
    node.style.gridColumn = 'span ' + size.span;
    node.style.height = size.height === 'auto' ? 'auto' : size.height + 'px';
  }
  function resizeHandle(group, root, node, label) {
    const handle = document.createElement('button');
    handle.type = 'button';
    handle.className = 'layout-resize';
    handle.textContent = '◢';
    handle.setAttribute('aria-label', 'Redimensionar ' + label + ' — setas: largura/altura; Home: altura automática');
    handle.title = 'Arraste o canto ou use as setas; Home restaura a altura automática';
    handle.addEventListener('keydown', event => {
      if (narrow() || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home'].includes(event.key)) return;
      event.preventDefault();
      const size = {...dimensions.get(node)};
      if (event.key === 'Home') size.height = 'auto';
      else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        size.span = clamp(size.span + (event.key === 'ArrowLeft' ? -1 : 1), 3, 12);
      } else {
        const height = size.height === 'auto' ? node.getBoundingClientRect().height : size.height;
        size.height = clamp(height + (event.key === 'ArrowUp' ? -20 : 20), 160, 1600);
      }
      applySize(node, size);
      capture(group, root);
    });
    let gesture = null;
    handle.addEventListener('pointerdown', event => {
      if (narrow() || event.button !== 0 || gesture) return;
      event.preventDefault();
      event.stopPropagation();
      const rect = node.getBoundingClientRect();
      gesture = {id: event.pointerId, x: event.clientX, y: event.clientY,
        height: rect.height, size: {...dimensions.get(node)},
        column: (root.getBoundingClientRect().width + 12) / 12};
      activeResizes.set(node, () => {
        if (gesture) applySize(node, gesture.size);
        gesture = null;
        activeResizes.delete(node);
      });
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      if (!gesture || event.pointerId !== gesture.id) return;
      applySize(node, {span: clamp(gesture.size.span + (event.clientX - gesture.x) / gesture.column, 3, 12),
        height: clamp(gesture.height + event.clientY - gesture.y, 160, 1600)});
    });
    const finish = (event, cancel) => {
      if (!gesture || event.pointerId !== gesture.id) return;
      const before = gesture;
      gesture = null;
      activeResizes.delete(node);
      if (cancel) applySize(node, before.size);
      else capture(group, root);
      handle.releasePointerCapture(event.pointerId);
    };
    handle.addEventListener('pointerup', event => finish(event, false));
    handle.addEventListener('pointercancel', event => finish(event, true));
    handle.addEventListener('lostpointercapture', event => {
      if (gesture && event.pointerId === gesture.id) {
        applySize(node, gesture.size);
        gesture = null;
        activeResizes.delete(node);
      }
    });
    node.append(handle);
  }
  function capture(group, root, reordered = false) {
    const previous = state.groups[group] || [];
    const current = blocks(root).map(node => {
      return {id: node.dataset.layoutId, ...dimensions.get(node)};
    });
    const ids = new Set(current.map(item => item.id));
    if (reordered) {
      state.groups[group] = current.concat(previous.filter(item => !ids.has(item.id)));
    } else {
      const updates = new Map(current.map(item => [item.id, item]));
      const known = new Set(previous.map(item => item.id));
      state.groups[group] = previous.map(item => updates.get(item.id) || item)
        .concat(current.filter(item => !known.has(item.id)));
    }
    // Bound stale dynamic identities while always retaining the displayed cards.
    const history = state.groups[group];
    if (history.length > 256) {
      const keep = new Set(history.filter(item => !ids.has(item.id)).reverse()
        .slice(0, Math.max(0, 256 - current.length)).map(item => item.id));
      state.groups[group] = history.filter(item => ids.has(item.id) || keep.has(item.id)).slice(-256);
    }
    save();
  }
  function reorder(group, root, node, offset) {
    const list = blocks(root), index = list.indexOf(node), other = list[index + offset];
    if (!other) return;
    root.insertBefore(offset < 0 ? node : other, offset < 0 ? other : node);
    capture(group, root, true);
  }
  function attach(group, root, node) {
    if (attached.has(node)) return;
    attached.add(node);
    node.classList.add('layout-block');
    const tools = document.createElement('div');
    tools.className = 'layout-tools';
    const grip = document.createElement('button');
    grip.type = 'button';
    grip.className = 'layout-grip';
    grip.draggable = true;
    grip.addEventListener('dragstart', event => {
      dragging = {root, node};
      event.stopPropagation();
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', node.dataset.layoutId);
      }
    });
    grip.addEventListener('dragend', () => { dragging = null; });
    const label = node.querySelector('h2, h3, legend')?.textContent || node.dataset.layoutId;
    grip.textContent = '⠿ Mover';
    grip.setAttribute('aria-label', 'Mover ' + label + ' — setas para cima/baixo');
    grip.title = 'Arraste pela alça ou use ↑ / ↓ para mover neste grupo';
    grip.addEventListener('keydown', event => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      event.preventDefault();
      reorder(group, root, node, event.key === 'ArrowUp' ? -1 : 1);
      grip.focus();
    });
    tools.append(grip);
    node.prepend(tools);
    resizeHandle(group, root, node, label);
  }
  function refresh() {
    for (const [group, selector] of Object.entries(selectors)) {
      const root = document.querySelector(selector);
      if (!root) continue;
      root.classList.add('layout-grid');
      wireRoot(group, root);
      const list = blocks(root);
      const order = defaults.get(group) || [];
      for (const node of list) if (!order.includes(node.dataset.layoutId)) order.push(node.dataset.layoutId);
      if (group === 'dashboard' && order.includes('notesPanel')) {
        order.splice(order.indexOf('notesPanel'), 1);
        order.unshift('notesPanel');
      }
      defaults.set(group, order);
      const saved = state.groups[group] || [];
      const preferred = saved.map(item => item.id).concat(order.filter(id => !saved.some(item => item.id === id)));
      const sorted = list.slice().sort((a, b) => preferred.indexOf(a.dataset.layoutId) - preferred.indexOf(b.dataset.layoutId));
      sorted.forEach((node, index) => {
        if (blocks(root)[index] !== node) root.insertBefore(node, blocks(root)[index]);
        if (!activeResizes.has(node)) {
          applySize(node, saved.find(item => item.id === node.dataset.layoutId) || defaultSize(group, node.dataset.layoutId));
        }
        attach(group, root, node);
      });
    }
  }
  function validate(raw) {
    if (raw.length > 262144) throw new Error('layout too large');
    const parsed = JSON.parse(raw);
    if (!parsed || parsed.version !== 1 || !parsed.groups ||
        typeof parsed.groups !== 'object' || Array.isArray(parsed.groups)) throw new Error('invalid layout');
    const clean = {version: 1, groups: {}};
    const top = ['notesPanel', 'controlPanel', 'analysisPanel', 'configPanel', 'transcriptPanel', 'finalAnalysisPanel'];
    for (const [group, entries] of Object.entries(parsed.groups)) {
      if (!Object.hasOwn(selectors, group) || !Array.isArray(entries) || entries.length > 256) throw new Error('invalid group');
      const seen = new Set();
      clean.groups[group] = entries.map(entry => {
        if (!entry || typeof entry.id !== 'string' || entry.id.length > 180 || seen.has(entry.id)) throw new Error('invalid id');
        const allowed = group === 'dashboard' ? top.includes(entry.id) :
          group === 'final' ? ['final-summary', 'final-tasks'].includes(entry.id) :
          (group === 'live' ? /^live:[\w-]+$/ : /^final:[\w-]+$/).test(entry.id);
        if (!allowed || !Number.isInteger(entry.span) || entry.span < 3 || entry.span > 12 ||
            (entry.height !== 'auto' && (!Number.isInteger(entry.height) || entry.height < 160 || entry.height > 1600))) throw new Error('invalid size');
        seen.add(entry.id);
        return {id: entry.id, span: entry.span, height: entry.height};
      });
    }
    return clean;
  }
  function init() {
    if (!loaded) {
      loaded = true;
      try {
        const raw = window.localStorage.getItem(KEY);
        if (raw !== null) state = validate(raw);
      } catch (_) { status('Não foi possível ler o layout salvo ou ele é inválido; usando o padrão.'); }
    }
    const button = document.getElementById('resetLayout');
    if (button && !resetButtons.has(button)) {
      resetButtons.add(button);
      button.addEventListener('click', reset);
    }
    refresh();
  }
  function reset() {
    for (const selector of Object.values(selectors)) {
      const root = document.querySelector(selector);
      if (root) for (const node of blocks(root)) activeResizes.get(node)?.();
    }
    state = {version: 1, groups: {}};
    try {
      window.localStorage.removeItem(KEY);
      status('Layout padrão restaurado.');
    } catch (_) { status('Layout restaurado, mas não foi possível remover a preferência salva.'); }
    refresh();
  }
  window.MeetingLayout = {init, refresh, reset};
}());
