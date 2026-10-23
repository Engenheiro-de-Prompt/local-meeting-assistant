/* Manual content is deliberately separate from transcription and LLM inputs. */
(function (global) {
  'use strict';
  const PREFIX = 'local-meeting-manual-v1:';
  const $ = id => document.getElementById(id);
  const empty = () => ({text:'', items:[]});
  const clone = value => JSON.parse(JSON.stringify(value));
  let notes = empty(), sessionId = '', revision = 0, dirty = false, localFailure = false;
  let editVersion = 0, saveTimer, saveChain = Promise.resolve(), lastStatus = null, preferDraft = false, conflict = false, starting = false;
  // A new writer per document avoids collisions even when a tab is duplicated.
  // Its predecessor lives in sessionStorage so reload restores this tab's edits.
  const writerId = crypto.randomUUID();
  let previousWriter = '', tabRecovery = true;
  try {
    previousWriter = global.sessionStorage.getItem(PREFIX + 'writer') || '';
    if (!/^[a-zA-Z0-9_-]{1,80}$/.test(previousWriter)) previousWriter = '';
    global.sessionStorage.setItem(PREFIX + 'writer', writerId);
  } catch (_) { tabRecovery = false; }
  const cacheKey = (id, writer = writerId) => PREFIX + 'tab:' + writer + ':' + (id || 'draft');
  const SELECTED = cacheKey('selected');
  function readCached(id) {
    const keys = [cacheKey(id), previousWriter ? cacheKey(id, previousWriter) : null, PREFIX + (id || 'draft')];
    for (const key of keys.filter(Boolean)) { const raw = localStorage.getItem(key); if (raw !== null) return raw; }
    return null;
  }
  function status(text) {
    $('manualStatus').textContent = localFailure
      ? 'Não foi possível salvar no navegador. Mantenha esta página aberta e copie suas anotações.' +
        (sessionId ? (dirty ? ' Há também alterações pendentes no arquivo da reunião.' : ' A última versão está confirmada no arquivo da reunião.') : '')
      : !tabRecovery ? 'A recuperação automática desta aba após recarregar está indisponível. Copie suas anotações antes de fechar ou atualizar.' : text;
  }
  function load(id) {
    try {
      const entry = JSON.parse(readCached(id) || 'null');
      if (entry && typeof entry.notes?.text === 'string' && Array.isArray(entry.notes.items)
          && entry.notes.text.length <= 20000 && entry.notes.items.length <= 100
          && entry.notes.items.every(x => x && typeof x.id === 'string' && /^[a-zA-Z0-9_-]{1,80}$/.test(x.id)
            && typeof x.text === 'string' && x.text.length <= 500 && typeof x.checked === 'boolean')
          && new Set(entry.notes.items.map(x => x.id)).size === entry.notes.items.length) return entry;
    } catch (_) { localFailure = true; status('Não foi possível ler as anotações locais.'); }
    return null;
  }
  function persist() {
    try {
      localStorage.setItem(cacheKey(sessionId), JSON.stringify({notes, revision, dirty}));
      localFailure = false;
      return true;
    } catch (_) { localFailure = true; status('Não foi possível salvar no navegador. Mantenha esta página aberta e copie suas anotações.'); return false; }
  }
  function changed() {
    dirty = true;
    if (!sessionId) { preferDraft = true; try { localStorage.setItem(SELECTED, 'draft'); } catch (_) {} }
    editVersion += 1;
    if (persist()) status(sessionId ? 'Salvo no navegador; salvando na reunião…' : 'Rascunho salvo neste navegador · não enviado à IA.');
    clearTimeout(saveTimer);
    if (sessionId) saveTimer = setTimeout(() => flush().catch(() => {}), 500);
  }
  function renderItems() {
    const host = $('manualItems'); host.textContent = '';
    for (const item of notes.items) {
      const row = document.createElement('div'); row.className = 'manual-item';
      const check = document.createElement('input'); check.type = 'checkbox'; check.checked = item.checked;
      check.setAttribute('aria-label', 'Concluir item do checklist');
      check.onchange = () => { item.checked = check.checked; row.classList.toggle('done', item.checked); changed(); };
      const text = document.createElement('input'); text.type = 'text'; text.maxLength = 500; text.value = item.text;
      text.setAttribute('aria-label', 'Texto do item do checklist');
      text.oninput = () => { item.text = text.value; changed(); };
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'soft'; remove.textContent = 'Remover';
      remove.setAttribute('aria-label', 'Remover item do checklist');
      remove.onclick = () => { notes.items = notes.items.filter(x => x.id !== item.id); renderItems(); changed(); };
      row.classList.toggle('done', item.checked); row.append(check, text, remove); host.append(row);
    }
  }
  function render() { $('manualText').value = notes.text; $('manualScope').textContent = sessionId ? 'Reunião: ' + sessionId : 'Rascunho da próxima reunião'; renderItems(); }
  function init() {
    const cached = load(''); notes = cached?.notes || empty(); sessionId = ''; revision = 0; dirty = !!cached?.dirty;
    try { preferDraft = readCached('selected') === 'draft' || !!cached; } catch (_) { preferDraft = !!cached; }
    if (cached) persist();
    $('newManualDraft').onclick = newDraft;
    $('manualConflict').hidden = true;
    $('manualKeepMine').onclick = async () => {
      try {
        const response = await fetch('/api/status', {cache:'no-store'});
        if (!response.ok) throw new Error('Servidor indisponível.');
        const latest = await response.json();
        if (latest.session_id !== sessionId) throw new Error('A reunião mudou. Seu texto continua nesta página.');
        localStorage.setItem(PREFIX + 'recovery-' + crypto.randomUUID(), JSON.stringify(latest.manual_notes || empty()));
        lastStatus = latest; revision = latest.manual_notes_revision || 0; conflict = false; $('manualConflict').hidden = true;
        changed(); await flush();
      } catch (e) { status(e.message); }
    };
    $('manualUseSaved').onclick = () => {
      if (lastStatus?.session_id !== sessionId) { status('A reunião mudou; suas anotações foram preservadas.'); return; }
      try { localStorage.setItem(PREFIX + 'recovery-' + crypto.randomUUID(), JSON.stringify(notes)); }
      catch (_) { status('Não foi possível preservar a edição local; copie-a antes de trocar de versão.'); return; }
      notes = clone(lastStatus.manual_notes || empty()); revision = lastStatus.manual_notes_revision || 0; dirty = false; conflict = false;
      $('manualConflict').hidden = true; render(); persist(); status('Versão salva da reunião restaurada.');
    };
    $('manualText').oninput = () => { notes.text = $('manualText').value; changed(); };
    $('addManualItem').onclick = () => {
      if (notes.items.length >= 100) { status('Limite de 100 itens por reunião.'); return; }
      notes.items.push({id:crypto.randomUUID(), text:'', checked:false}); renderItems(); changed();
    };
    render(); status('Rascunho para a próxima reunião · conteúdo manual, sem IA.');
  }
  function sync(server) {
    if (server.session_id === sessionId && (server.manual_notes_revision || 0) < revision) return;
    lastStatus = server;
    const busy = ['recording', 'processing', 'final_analysis'].includes(server.state);
    $('newManualDraft').disabled = busy || starting;
    if (starting) return;
    if (!server.session_id || (preferDraft && !busy)) return;
    if (sessionId !== server.session_id) {
      const cached = load(server.session_id);
      preferDraft = false;
      sessionId = server.session_id;
      notes = cached?.dirty ? cached.notes : clone(server.manual_notes || empty());
      revision = cached?.dirty ? cached.revision : (server.manual_notes_revision || 0);
      dirty = !!cached?.dirty;
      render(); persist();
      if (dirty && revision === (server.manual_notes_revision || 0)) { clearTimeout(saveTimer); saveTimer = setTimeout(() => flush().catch(() => {}), 500); }
    } else if (!dirty && (server.manual_notes_revision || 0) > revision) {
      notes = clone(server.manual_notes || empty()); revision = server.manual_notes_revision || 0; render(); persist();
    }
    conflict = dirty && (server.manual_notes_revision || 0) !== revision;
    $('manualConflict').hidden = !conflict;
    if (conflict) { status('Outra aba alterou estas anotações. Escolha qual versão usar; sua edição foi preservada.'); return; }
    status(dirty ? 'Alterações locais aguardando gravação na reunião.' : 'Anotações desta reunião salvas localmente · não enviadas à IA.');
  }
  function flush() {
    clearTimeout(saveTimer);
    const requestedSession = sessionId;
    saveChain = saveChain.catch(() => {}).then(async () => {
      while (sessionId && dirty && sessionId === requestedSession) {
      if (conflict) throw new Error('Resolva o conflito das anotações antes de continuar.');
      const id = sessionId, sentVersion = editVersion;
      const response = await fetch('/api/manual-notes', {method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({session_id:id, expected_revision:revision, notes:clone(notes)}), cache:'no-store'});
      const result = await response.json();
      if (!response.ok) { if (response.status === 409) { conflict = true; $('manualConflict').hidden = false; } status('Não foi possível salvar na reunião. O texto continua nesta página.'); throw new Error(result.error || 'Falha ao salvar anotações.'); }
      if (sessionId !== id) return;
      revision = result.manual_notes_revision;
      conflict = lastStatus?.session_id === id && (lastStatus.manual_notes_revision || 0) > revision;
      $('manualConflict').hidden = !conflict;
      if (sentVersion === editVersion) dirty = false;
      persist(); status(dirty ? 'Há novas alterações aguardando gravação.' : 'Anotações salvas junto à reunião · não enviadas à IA.');
      }
      if (sessionId === requestedSession) clearTimeout(saveTimer);
    }).catch(error => {
      if (sessionId === requestedSession && dirty) status('Não foi possível salvar na reunião; servidor indisponível ou conflito. Sua edição continua nesta página.');
      throw error;
    });
    return saveChain;
  }
  async function newDraft() {
    if (starting || ['recording', 'processing', 'final_analysis'].includes(lastStatus?.state)) return;
    const previousSession = sessionId;
    try { await flush(); } catch (_) { return; }
    if (sessionId !== previousSession || starting) return;
    if (sessionId && dirty) { status('Há novas alterações; aguarde o salvamento antes de abrir outro rascunho.'); return; }
    if (!persist()) return;
    clearTimeout(saveTimer);
    const cached = load(''); sessionId = ''; revision = 0; notes = cached?.notes || empty(); dirty = !!cached?.dirty;
    preferDraft = true; conflict = false; $('manualConflict').hidden = true; editVersion += 1;
    try { localStorage.setItem(SELECTED, 'draft'); } catch (_) {}
    render(); persist(); status('Rascunho da próxima reunião · não enviado à IA.');
  }
  function updateHandoffLock() {
    const locked = starting && !!sessionId;
    $('manualText').readOnly = locked;
    $('addManualItem').disabled = locked;
    $('manualKeepMine').disabled = locked;
    $('manualUseSaved').disabled = locked;
    for (const control of ($('manualItems').querySelectorAll?.('input,button') || [])) control.disabled = locked;
  }
  function started(server) {
    const pendingDraft = sessionId ? null : clone(notes);
    starting = false; updateHandoffLock();
    preferDraft = false; sync(server);
    if (pendingDraft && server.session_id) {
      notes = pendingDraft; render();
      if (JSON.stringify(notes) !== JSON.stringify(server.manual_notes || empty())) changed();
      if (persist()) { try { localStorage.setItem(cacheKey(''), JSON.stringify({consumed:true})); localStorage.setItem(SELECTED, 'session:' + sessionId); } catch (_) {} }
    }
  }
  global.MeetingNotes = {init, sync, flush, started, beginStart:() => { starting = true; updateHandoffLock(); $('newManualDraft').disabled = true; }, cancelStart:() => { starting = false; updateHandoffLock(); $('newManualDraft').disabled = ['recording','processing','final_analysis'].includes(lastStatus?.state); }, forStart:() => sessionId ? empty() : clone(notes)};
})(globalThis);
