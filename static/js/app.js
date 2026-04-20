/* =========================================================
   Knowledge Shredder — Frontend Logic
   ========================================================= */

const state = {
  docId: null,
  previewText: '',
  fileName: '',
  trainerId: 'trainer_001',
  selectedDomainIds: new Set(),
  allDomains: [],
  activeJobId: null,
  isGenerating: false,
};

document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('trainer-id').addEventListener('input', handleTrainerIdInput);
  document.getElementById('btn-generate').addEventListener('click', handleGenerate);
  document.getElementById('btn-clear-file').addEventListener('click', clearFile);
  document.getElementById('domain-search').addEventListener('input', filterDomains);

  await loadDomains();
  initDragDrop();
  initFileInput();
  updateGenerateButtonState();
});

async function loadDomains() {
  try {
    const res = await fetch('/api/domains');
    state.allDomains = await res.json();
    renderDomainList(state.allDomains);
  } catch {
    showError('無法載入領域標籤，請重新整理頁面。');
  }
}

function handleTrainerIdInput(event) {
  state.trainerId = event.target.value.trim() || 'trainer_001';
}

function buildApiHeaders(extraHeaders = {}) {
  return {
    'X-Trainer-Id': state.trainerId || 'trainer_001',
    ...extraHeaders,
  };
}

function renderDomainList(domains) {
  const container = document.getElementById('domain-list');
  container.innerHTML = '';
  domains.forEach(domain => {
    const id = `domain-cb-${domain.domain_id}`;
    const checked = state.selectedDomainIds.has(domain.domain_id);
    const item = document.createElement('div');
    item.className = 'form-check domain-check-item';
    item.dataset.domainId = domain.domain_id;
    item.innerHTML = `
      <input class="form-check-input" type="checkbox" id="${id}" value="${domain.domain_id}" ${checked ? 'checked' : ''}>
      <label class="form-check-label" for="${id}">
        <span class="fw-semibold">${domain.domain_name}</span>
        <span class="text-muted small ms-1">— ${domain.description}</span>
      </label>`;
    item.querySelector('input').addEventListener('change', event => toggleDomain(domain.domain_id, event.target.checked));
    container.appendChild(item);
  });
}

function filterDomains() {
  const query = document.getElementById('domain-search').value.toLowerCase();
  document.querySelectorAll('.domain-check-item').forEach(item => {
    const text = item.textContent.toLowerCase();
    item.style.display = text.includes(query) ? '' : 'none';
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
    const domain = state.allDomains.find(item => item.domain_id === id);
    if (!domain) return;
    const pill = document.createElement('span');
    pill.className = 'badge bg-primary d-flex align-items-center gap-1 px-2 py-1';
    pill.innerHTML = `#${domain.domain_name} <button class="btn-close btn-close-white" style="font-size:.6rem;" aria-label="移除"></button>`;
    pill.querySelector('button').addEventListener('click', () => {
      state.selectedDomainIds.delete(id);
      const checkbox = document.getElementById(`domain-cb-${id}`);
      if (checkbox) checkbox.checked = false;
      renderSelectedPills();
      updateGenerateButtonState();
    });
    container.appendChild(pill);
  });
}

function updateGenerateButtonState() {
  const btn = document.getElementById('btn-generate');
  const canGenerate = state.docId !== null && state.selectedDomainIds.size > 0 && !state.isGenerating;
  btn.disabled = !canGenerate;

  const warn = document.getElementById('domain-warning');
  warn.classList.toggle('d-none', state.docId === null || state.selectedDomainIds.size > 0);
}

function initDragDrop() {
  const zone = document.getElementById('upload-zone');
  zone.addEventListener('click', () => document.getElementById('file-input').click());
  zone.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      document.getElementById('file-input').click();
    }
  });

  ['dragenter', 'dragover'].forEach(eventName =>
    zone.addEventListener(eventName, event => {
      event.preventDefault();
      zone.classList.add('drag-active');
    })
  );

  ['dragleave', 'drop'].forEach(eventName =>
    zone.addEventListener(eventName, event => {
      event.preventDefault();
      zone.classList.remove('drag-active');
    })
  );

  zone.addEventListener('drop', event => {
    const file = event.dataTransfer.files[0];
    if (file) handleFileUpload(file);
  });
}

function initFileInput() {
  document.getElementById('file-input').addEventListener('change', event => {
    if (event.target.files[0]) handleFileUpload(event.target.files[0]);
  });
}

async function handleFileUpload(file) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'docx', 'txt'].includes(ext)) {
    showError(`不支援 .${ext} 格式，請上傳 PDF、DOCX 或 TXT 檔案。`);
    return;
  }

  showLoading('解析文件中...');
  clearSplitScreen();
  clearJobStatus();
  hideError();

  const formData = new FormData();
  formData.append('file', file);
  formData.append('trainer_id', state.trainerId);

  try {
    const res = await fetch('/api/upload', {
      method: 'POST',
      headers: buildApiHeaders(),
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    state.docId = data.doc_id;
    state.previewText = data.preview_text;
    state.fileName = data.file_name;
    state.trainerId = data.trainer_id;
    document.getElementById('trainer-id').value = data.trainer_id;

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
  state.previewText = '';
  state.fileName = '';
  state.activeJobId = null;
  state.isGenerating = false;

  document.getElementById('file-info').classList.add('d-none');
  document.getElementById('upload-zone').classList.remove('d-none');
  document.getElementById('file-input').value = '';
  clearSplitScreen();
  clearJobStatus();
  updateGenerateButtonState();
}

async function handleGenerate() {
  showLoading('正在建立生成任務...');
  hideError();
  state.isGenerating = true;
  updateGenerateButtonState();

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        doc_id: state.docId,
        trainer_id: state.trainerId,
        domain_ids: [...state.selectedDomainIds],
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    state.activeJobId = data.job_id;
    showJobStatus(data.status, `任務 #${data.job_id} 已建立，系統開始生成微模組。`);
    hideLoading();

    const job = await pollJobUntilFinished(data.job_id);
    if (job.status !== 'completed' || !job.result) {
      throw new Error(job.error_message || '生成任務未完成。');
    }

    renderSplitScreen(state.previewText, job.result);
  } catch (err) {
    showError(err.message);
  } finally {
    state.isGenerating = false;
    hideLoading();
    updateGenerateButtonState();
  }
}

async function pollJobUntilFinished(jobId) {
  while (true) {
    const res = await fetch(`/api/jobs/${jobId}`, {
      headers: buildApiHeaders(),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    const statusMessages = {
      queued: `任務 #${jobId} 已進入佇列，等待背景工作執行。`,
      running: `任務 #${jobId} 正在生成中，請稍候。`,
      completed: `任務 #${jobId} 已完成，結果如下。`,
      failed: `任務 #${jobId} 失敗：${data.error_message || '請稍後重試。'}`,
    };

    showJobStatus(data.status, statusMessages[data.status] || '正在同步任務狀態。');

    if (data.status === 'completed' || data.status === 'failed') {
      return data;
    }

    await wait(1200);
  }
}

function renderSplitScreen(previewText, result) {
  document.getElementById('raw-text-panel').textContent = previewText;

  const pillsContainer = document.getElementById('domain-pills');
  pillsContainer.innerHTML = '';
  (result.domains || []).forEach(name => {
    const span = document.createElement('span');
    span.className = 'badge bg-info text-dark';
    span.textContent = `#${name}`;
    pillsContainer.appendChild(span);
  });

  document.getElementById('summary-badge').textContent = result.document_summary || '';

  const sprintPanel = document.getElementById('sprint-panel');
  sprintPanel.innerHTML = '';
  (result.modules || []).forEach((mod, index) => {
    const sprintOrder = mod.sequence_order != null ? mod.sequence_order : index + 1;
    const readingTime = mod.reading_time_minutes != null ? mod.reading_time_minutes : 2;
    const domainBadges = (result.domains || []).map(name =>
      `<span class="badge rounded-pill text-bg-info-subtle border border-info-subtle text-info-emphasis">#${escapeHtml(name)}</span>`
    ).join(' ');

    const card = document.createElement('div');
    card.className = 'card sprint-card mb-3';
    card.innerHTML = `
      <div class="card-header d-flex justify-content-between align-items-center">
        <span class="fw-semibold">
          <span class="badge bg-secondary me-1">Sprint ${sprintOrder}</span>
          ${escapeHtml(mod.title || '')}
        </span>
        <span class="badge bg-light text-dark border">
          <i class="bi bi-clock me-1"></i>${readingTime} min
        </span>
      </div>
      <div class="card-body">
        <div class="module-domain-pills mb-3">${domainBadges}</div>
        <p class="card-text">${escapeHtml(mod.content || '').replace(/\n/g, '<br>')}</p>
        ${mod.key_takeaway ? `
        <blockquote class="key-takeaway mb-0">
          <i class="bi bi-lightbulb-fill text-warning me-1"></i>
          <em>${escapeHtml(mod.key_takeaway)}</em>
        </blockquote>` : ''}
      </div>`;
    sprintPanel.appendChild(card);
  });

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

function showJobStatus(status, message) {
  const card = document.getElementById('job-status-card');
  const badge = document.getElementById('job-status-badge');
  const labelMap = {
    queued: ['Queued', 'text-bg-secondary'],
    running: ['Running', 'text-bg-primary'],
    completed: ['Completed', 'text-bg-success'],
    failed: ['Failed', 'text-bg-danger'],
  };
  const [label, badgeClass] = labelMap[status] || ['Pending', 'text-bg-secondary'];

  badge.className = `badge ${badgeClass} mb-2`;
  badge.textContent = label;
  document.getElementById('job-status-message').textContent = message;
  card.classList.remove('d-none');
}

function clearJobStatus() {
  document.getElementById('job-status-card').classList.add('d-none');
  document.getElementById('job-status-badge').className = 'badge text-bg-secondary mb-2';
  document.getElementById('job-status-badge').textContent = 'Queued';
  document.getElementById('job-status-message').textContent = '等待送出生成任務。';
}

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

function hideError() {
  document.getElementById('error-alert').classList.add('d-none');
  document.getElementById('error-message').textContent = '';
}

function wait(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
