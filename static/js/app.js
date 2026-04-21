const state = {
  documents: [],
  trainerId: 'trainer_001',
  selectedDomainIds: new Set(),
  customPrompt: '',
  allDomains: [],
  activeJobIds: [],
  isGenerating: false,
};

document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('trainer-id').addEventListener('input', handleTrainerIdInput);
  document.getElementById('btn-generate').addEventListener('click', handleGenerate);
  document.getElementById('btn-clear-all-files').addEventListener('click', clearAllFiles);
  document.getElementById('domain-search').addEventListener('input', filterDomains);
  document.getElementById('custom-prompt').addEventListener('input', handleCustomPromptInput);

  await loadDomains();
  initDragDrop();
  initFileInput();
  renderUploadedDocuments();
  updateGenerateButtonState();
});

async function loadDomains() {
  try {
    const res = await fetch('/api/domains');
    state.allDomains = await res.json();
    renderDomainList(state.allDomains);
  } catch {
    showError('Failed to load available domains.');
  }
}

function handleTrainerIdInput(event) {
  state.trainerId = event.target.value.trim() || 'trainer_001';
}

function handleCustomPromptInput(event) {
  state.customPrompt = event.target.value;
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
        <span class="fw-semibold">${escapeHtml(domain.domain_name)}</span>
        <span class="text-muted small ms-1">${escapeHtml(domain.description || '')}</span>
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
    pill.innerHTML = `#${escapeHtml(domain.domain_name)} <button class="btn-close btn-close-white" style="font-size:.6rem;" aria-label="移除"></button>`;
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

function renderUploadedDocuments() {
  const section = document.getElementById('uploaded-files-section');
  const list = document.getElementById('uploaded-file-list');

  list.innerHTML = '';

  state.documents.forEach(doc => {
    const item = document.createElement('div');
    item.className = 'uploaded-file-item';
    item.innerHTML = `
      <div class="uploaded-file-meta">
        <div class="d-flex align-items-center gap-2 flex-wrap">
          <span class="fw-semibold">${escapeHtml(doc.file_name)}</span>
        </div>
        <div class="text-muted small">${Number(doc.char_count || 0).toLocaleString()} chars</div>
      </div>
      <div class="uploaded-file-actions">
        <button type="button" class="btn btn-sm btn-outline-secondary btn-remove-doc" ${state.isGenerating ? 'disabled' : ''}>Remove</button>
      </div>
    `;

    item.querySelector('.btn-remove-doc').addEventListener('click', () => removeDocument(doc.doc_id));
    list.appendChild(item);
  });

  section.classList.toggle('d-none', state.documents.length === 0);

  const clearAllButton = document.getElementById('btn-clear-all-files');
  clearAllButton.disabled = state.documents.length === 0 || state.isGenerating;
}

function updateGenerateButtonState() {
  const btn = document.getElementById('btn-generate');
  const canGenerate = state.documents.length > 0 && state.selectedDomainIds.size > 0 && !state.isGenerating;
  btn.disabled = !canGenerate;

  const warn = document.getElementById('domain-warning');
  warn.classList.toggle('d-none', state.documents.length > 0 && state.selectedDomainIds.size > 0);

  renderUploadedDocuments();
}

function initDragDrop() {
  const zone = document.getElementById('upload-zone');
  const fileInput = document.getElementById('file-input');

  zone.addEventListener('click', () => fileInput.click());
  zone.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      fileInput.click();
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

  zone.addEventListener('drop', async event => {
    await handleFilesUpload(event.dataTransfer.files);
  });
}

function initFileInput() {
  document.getElementById('file-input').addEventListener('change', async event => {
    await handleFilesUpload(event.target.files);
    event.target.value = '';
  });
}

async function handleFilesUpload(fileList) {
  const files = Array.from(fileList || []);
  if (files.length === 0) return;

  clearSplitScreen();
  clearJobStatus();
  hideError();

  const unsupportedFiles = files.filter(file => {
    const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
    return !['pdf', 'docx', 'txt', 'md'].includes(ext);
  });
  const supportedFiles = files.filter(file => !unsupportedFiles.includes(file));
  const issues = unsupportedFiles.map(file => `${file.name}: unsupported file type`);

  if (supportedFiles.length === 0) {
    showError(issues.join(' | '));
    return;
  }

  showLoading(`Uploading ${supportedFiles.length} file(s)...`);

  for (const file of supportedFiles) {
    try {
      const payload = await uploadSingleFile(file);
      addUploadedDocument(payload);
    } catch (err) {
      issues.push(`${file.name}: ${err.message}`);
    }
  }

  hideLoading();
  updateGenerateButtonState();

  if (issues.length > 0) {
    showError(issues.join(' | '));
  }
}

async function uploadSingleFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('trainer_id', state.trainerId);

  const res = await fetch('/api/upload', {
    method: 'POST',
    headers: buildApiHeaders(),
    body: formData,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || 'Upload failed.');
  }
  return data;
}

function addUploadedDocument(data) {
  const documentRecord = {
    doc_id: data.doc_id,
    trainer_id: data.trainer_id,
    file_name: data.file_name,
    preview_text: data.preview_text,
    char_count: data.char_count,
  };

  state.documents.push(documentRecord);
  state.trainerId = data.trainer_id;
  document.getElementById('trainer-id').value = data.trainer_id;
}

function removeDocument(docId) {
  if (state.isGenerating) return;

  state.documents = state.documents.filter(doc => doc.doc_id !== docId);
  clearSplitScreen();
  clearJobStatus();
  updateGenerateButtonState();
}

function clearAllFiles() {
  if (state.isGenerating) return;

  state.documents = [];
  state.activeJobIds = [];
  state.isGenerating = false;
  clearSplitScreen();
  clearJobStatus();
  hideError();
  updateGenerateButtonState();
}

async function handleGenerate() {
  if (state.documents.length === 0) {
    showError('Please upload at least one document before generating.');
    return;
  }

  showLoading('Generating learning sprints...');
  hideError();
  state.isGenerating = true;
  updateGenerateButtonState();

  try {
    const res = await fetch('/api/generate', {
      method: 'POST',
      headers: buildApiHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        doc_ids: state.documents.map(doc => doc.doc_id),
        trainer_id: state.trainerId,
        domain_ids: [...state.selectedDomainIds],
        custom_prompt: state.customPrompt,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);

    state.activeJobIds = (data.jobs || []).map(job => job.job_id);
    showJobStatus('queued', `${state.activeJobIds.length} job(s) created. Starting generation for all uploaded documents.`);
    hideLoading();

    const jobs = await pollJobsUntilFinished(data.jobs || []);
    const failedJob = jobs.find(job => job.status !== 'completed' || !job.result);
    if (failedJob) {
      throw new Error(failedJob.error_message || `Generation did not complete successfully for doc_id ${failedJob.doc_id}.`);
    }

    renderBatchResults(jobs);
  } catch (err) {
    showError(err.message);
  } finally {
    state.isGenerating = false;
    state.activeJobIds = [];
    hideLoading();
    updateGenerateButtonState();
  }
}

async function pollJobsUntilFinished(jobs) {
  const pendingJobs = new Map((jobs || []).map(job => [job.job_id, job]));
  const finalJobs = new Map();

  while (pendingJobs.size > 0) {
    const currentJobs = Array.from(pendingJobs.values());
    const responses = await Promise.all(
      currentJobs.map(job =>
        fetch(`/api/jobs/${job.job_id}`, {
          headers: buildApiHeaders(),
        }).then(async res => {
          const data = await res.json();
          if (!res.ok) throw new Error(data.error);
          return data;
        })
      )
    );

    responses.forEach(job => {
      if (job.status === 'completed' || job.status === 'failed') {
        pendingJobs.delete(job.job_id);
        finalJobs.set(job.job_id, job);
      }
    });

    const completedCount = Array.from(finalJobs.values()).filter(job => job.status === 'completed').length;
    const failedCount = Array.from(finalJobs.values()).filter(job => job.status === 'failed').length;
    const runningCount = responses.filter(job => job.status === 'running').length;
    const queuedCount = responses.filter(job => job.status === 'queued').length;

    const status = failedCount > 0 ? 'failed' : pendingJobs.size === 0 ? 'completed' : runningCount > 0 ? 'running' : 'queued';
    showJobStatus(
      status,
      `Completed: ${completedCount}/${jobs.length}, Running: ${runningCount}, Queued: ${queuedCount}, Failed: ${failedCount}`
    );

    if (pendingJobs.size === 0) {
      return jobs.map(job => finalJobs.get(job.job_id)).filter(Boolean);
    }

    await wait(1200);
  }

  return [];
}

function renderBatchResults(jobs) {
  const resultsContainer = document.getElementById('results-container');
  resultsContainer.innerHTML = '';

  jobs.forEach(job => {
    const documentRecord = state.documents.find(doc => doc.doc_id === job.doc_id);
    const previewText = documentRecord ? documentRecord.preview_text : '';
    const result = job.result || {};

    const block = document.createElement('section');
    block.className = 'result-document-block';
    block.innerHTML = `
      <div class="d-flex justify-content-between align-items-center mb-3 gap-2 flex-wrap">
        <div>
          <div class="result-document-title fw-semibold">${escapeHtml(result.file_name || job.file_name || `Document ${job.doc_id}`)}</div>
          <div class="text-muted small">${escapeHtml(result.document_summary || '')}</div>
        </div>
        <div class="d-flex flex-wrap gap-1 result-domain-pills"></div>
      </div>
      <div class="row g-3">
        <div class="col-md-6">
          <div class="split-panel-header">
            <i class="bi bi-shield-lock me-1"></i>Safe Source Preview
          </div>
          <pre class="split-pane raw-text-panel">${escapeHtml(previewText)}</pre>
        </div>
        <div class="col-md-6">
          <div class="split-panel-header">
            <i class="bi bi-stars me-1"></i>Generated Learning Sprints
          </div>
          <div class="split-pane sprint-panel result-sprint-panel"></div>
        </div>
      </div>
    `;

    const pillsContainer = block.querySelector('.result-domain-pills');
    (result.domains || []).forEach(name => {
      const span = document.createElement('span');
      span.className = 'badge bg-info text-dark';
      span.textContent = `#${name}`;
      pillsContainer.appendChild(span);
    });

    const sprintPanel = block.querySelector('.result-sprint-panel');
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

    resultsContainer.appendChild(block);
  });

  document.getElementById('summary-badge').textContent = `${jobs.length} document(s) generated`;
  document.getElementById('split-screen').classList.remove('d-none');
  document.getElementById('split-screen').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function clearSplitScreen() {
  document.getElementById('split-screen').classList.add('d-none');
  document.getElementById('results-container').innerHTML = '';
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
  document.getElementById('job-status-message').textContent = 'Waiting to start generation.';
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
