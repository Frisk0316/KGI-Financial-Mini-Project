/* =========================================================
   Knowledge Shredder — Frontend Logic
   ========================================================= */

const state = {
  docId: null,
  rawText: '',
  fileName: '',
  selectedDomainIds: new Set(),
  allDomains: [],
};

// ── Initialisation ──────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  await loadDomains();
  initDragDrop();
  initFileInput();
  document.getElementById('btn-generate').addEventListener('click', handleGenerate);
  document.getElementById('btn-clear-file').addEventListener('click', clearFile);
  document.getElementById('domain-search').addEventListener('input', filterDomains);
  updateGenerateButtonState();
});

// ── Domain Picker ────────────────────────────────────────────────────────────

async function loadDomains() {
  try {
    const res = await fetch('/api/domains');
    state.allDomains = await res.json();
    renderDomainList(state.allDomains);
  } catch {
    showError('無法載入領域標籤，請重新整理頁面。');
  }
}

function renderDomainList(domains) {
  const container = document.getElementById('domain-list');
  container.innerHTML = '';
  domains.forEach(d => {
    const id = `domain-cb-${d.domain_id}`;
    const checked = state.selectedDomainIds.has(d.domain_id);
    const item = document.createElement('div');
    item.className = 'form-check domain-check-item';
    item.dataset.domainId = d.domain_id;
    item.innerHTML = `
      <input class="form-check-input" type="checkbox" id="${id}" value="${d.domain_id}" ${checked ? 'checked' : ''}>
      <label class="form-check-label" for="${id}">
        <span class="fw-semibold">${d.domain_name}</span>
        <span class="text-muted small ms-1">— ${d.description}</span>
      </label>`;
    item.querySelector('input').addEventListener('change', e => toggleDomain(d.domain_id, e.target.checked));
    container.appendChild(item);
  });
}

function filterDomains() {
  const q = document.getElementById('domain-search').value.toLowerCase();
  document.querySelectorAll('.domain-check-item').forEach(item => {
    const text = item.textContent.toLowerCase();
    item.style.display = text.includes(q) ? '' : 'none';
  });
}

function toggleDomain(domainId, checked) {
  if (checked) {
    state.selectedDomainIds.add(domainId);
  } else {
    state.selectedDomainIds.delete(domainId);
  }
  renderSelectedPills();
  updateGenerateButtonState();
}

function renderSelectedPills() {
  const container = document.getElementById('selected-tags');
  container.innerHTML = '';
  state.selectedDomainIds.forEach(id => {
    const domain = state.allDomains.find(d => d.domain_id === id);
    if (!domain) return;
    const pill = document.createElement('span');
    pill.className = 'badge bg-primary d-flex align-items-center gap-1 px-2 py-1';
    pill.innerHTML = `#${domain.domain_name} <button class="btn-close btn-close-white" style="font-size:.6rem;" aria-label="移除"></button>`;
    pill.querySelector('button').addEventListener('click', () => {
      state.selectedDomainIds.delete(id);
      // Uncheck the corresponding checkbox
      const cb = document.getElementById(`domain-cb-${id}`);
      if (cb) cb.checked = false;
      renderSelectedPills();
      updateGenerateButtonState();
    });
    container.appendChild(pill);
  });
}

// ── Generate button state ────────────────────────────────────────────────────

function updateGenerateButtonState() {
  const btn = document.getElementById('btn-generate');
  const canGenerate = state.docId !== null && state.selectedDomainIds.size > 0;
  btn.disabled = !canGenerate;

  const warn = document.getElementById('domain-warning');
  // Show warning only if a doc is loaded but no domain selected
  warn.classList.toggle('d-none', state.docId === null || state.selectedDomainIds.size > 0);
}

// ── Drag-and-drop ────────────────────────────────────────────────────────────

function initDragDrop() {
  const zone = document.getElementById('upload-zone');
  zone.addEventListener('click', () => document.getElementById('file-input').click());
  zone.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') document.getElementById('file-input').click(); });

  ['dragenter', 'dragover'].forEach(evt =>
    zone.addEventListener(evt, e => { e.preventDefault(); zone.classList.add('drag-active'); })
  );
  ['dragleave', 'drop'].forEach(evt =>
    zone.addEventListener(evt, e => { e.preventDefault(); zone.classList.remove('drag-active'); })
  );
  zone.addEventListener('drop', e => {
    const file = e.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  });
}

function initFileInput() {
  document.getElementById('file-input').addEventListener('change', e => {
    if (e.target.files[0]) handleFileUpload(e.target.files[0]);
  });
}

// ── File Upload ──────────────────────────────────────────────────────────────

async function handleFileUpload(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'docx', 'doc', 'txt'].includes(ext)) {
    showError(`不支援 .${ext} 格式，請上傳 PDF、DOCX 或 TXT 檔案。`);
    return;
  }

  showLoading('解析文件中...');
  clearSplitScreen();

  const formData = new FormData();
  formData.append('file', file);

  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    state.docId = data.doc_id;
    state.rawText = data.raw_text;
    state.fileName = data.file_name;

    document.getElementById('file-name').textContent = data.file_name;
    document.getElementById('char-count').textContent = `${data.char_count.toLocaleString()} 字元`;
    document.getElementById('file-info').classList.remove('d-none');
    document.getElementById('upload-zone').classList.add('d-none');

    updateGenerateButtonState();
  } catch (err) {
    showError(err.message);
  } finally {
    hideLoading();
  }
}

function clearFile() {
  state.docId = null;
  state.rawText = '';
  state.fileName = '';
  document.getElementById('file-info').classList.add('d-none');
  document.getElementById('upload-zone').classList.remove('d-none');
  document.getElementById('file-input').value = '';
  clearSplitScreen();
  updateGenerateButtonState();
}

// ── Generation ───────────────────────────────────────────────────────────────

async function handleGenerate() {
  showLoading('Claude AI 生成微模組中，請稍候...');

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        doc_id: state.docId,
        domain_ids: [...state.selectedDomainIds],
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    renderSplitScreen(state.rawText, data);
  } catch (err) {
    showError(err.message);
  } finally {
    hideLoading();
  }
}

// ── Split-Screen Render ──────────────────────────────────────────────────────

function renderSplitScreen(rawText, result) {
  // Left: raw text
  document.getElementById('raw-text-panel').textContent = rawText;

  // Domain pills (top right)
  const pillsContainer = document.getElementById('domain-pills');
  pillsContainer.innerHTML = '';
  (result.domains || []).forEach(name => {
    const span = document.createElement('span');
    span.className = 'badge bg-info text-dark';
    span.textContent = `#${name}`;
    pillsContainer.appendChild(span);
  });

  // Summary
  if (result.document_summary) {
    document.getElementById('summary-badge').textContent = result.document_summary;
  }

  // Sprint cards
  const sprintPanel = document.getElementById('sprint-panel');
  sprintPanel.innerHTML = '';
  (result.modules || []).forEach((mod, i) => {
    const card = document.createElement('div');
    card.className = 'card sprint-card mb-3';
    card.innerHTML = `
      <div class="card-header d-flex justify-content-between align-items-center">
        <span class="fw-semibold">
          <span class="badge bg-secondary me-1">Sprint ${mod.sequence_order ?? i + 1}</span>
          ${escapeHtml(mod.title || '')}
        </span>
        <span class="badge bg-light text-dark border">
          <i class="bi bi-clock me-1"></i>${mod.reading_time_minutes ?? 2} min
        </span>
      </div>
      <div class="card-body">
        <p class="card-text">${escapeHtml(mod.content || '').replace(/\n/g, '<br>')}</p>
        ${mod.key_takeaway ? `
        <blockquote class="key-takeaway mb-0">
          <i class="bi bi-lightbulb-fill text-warning me-1"></i>
          <em>${escapeHtml(mod.key_takeaway)}</em>
        </blockquote>` : ''}
      </div>`;
    sprintPanel.appendChild(card);
  });

  // Show split screen and scroll into view
  const splitScreen = document.getElementById('split-screen');
  splitScreen.classList.remove('d-none');
  splitScreen.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function clearSplitScreen() {
  document.getElementById('split-screen').classList.add('d-none');
  document.getElementById('sprint-panel').innerHTML = '';
  document.getElementById('raw-text-panel').textContent = '';
  document.getElementById('domain-pills').innerHTML = '';
  document.getElementById('summary-badge').textContent = '';
}

// ── UI Helpers ───────────────────────────────────────────────────────────────

function showLoading(message) {
  document.getElementById('loading-message').textContent = message;
  document.getElementById('loading-overlay').classList.remove('d-none');
}

function hideLoading() {
  document.getElementById('loading-overlay').classList.add('d-none');
}

function showError(message) {
  const alert = document.getElementById('error-alert');
  document.getElementById('error-message').textContent = message;
  alert.classList.remove('d-none');
  alert.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
