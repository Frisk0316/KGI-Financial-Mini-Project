const state = {
  documents: [],
  trainerId: 'trainer_001',
  selectedDomainIds: new Set(),
  customPrompt: '',
  allDomains: [],
  activeJobIds: [],
  isGenerating: false,
  historyReloadTimer: null,
};

document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('trainer-id').addEventListener('input', handleTrainerIdInput);
  document.getElementById('btn-generate').addEventListener('click', handleGenerate);
  document.getElementById('btn-clear-all-files').addEventListener('click', clearAllFiles);
  document.getElementById('btn-refresh-history').addEventListener('click', () => loadHistory());
  document.getElementById('domain-search').addEventListener('input', filterDomains);
  document.getElementById('custom-prompt').addEventListener('input', handleCustomPromptInput);

  await loadDomains();
  await loadHistory();
  initDragDrop();
  initFileInput();
  renderUploadedDocuments();
  updateGenerateButtonState();
});

function buildFriendlyError(rawMessage) {
  const fallbackMessage = '系統暫時無法完成這次操作，請稍後再試。';
  const technicalDetail = String(rawMessage || '').trim();

  if (!technicalDetail) {
    return { userMessage: fallbackMessage, technicalDetail: '' };
  }

  const normalized = technicalDetail.toLowerCase();

  if (normalized.includes('rate limit') || normalized.includes('429') || normalized.includes('rate_limit_exceeded')) {
    return {
      userMessage: '打給 AI 的請求數過多，請稍後再試。',
      technicalDetail,
    };
  }

  if (normalized.includes('failed to load available domains')) {
    return {
      userMessage: '目前無法載入可用的 domain tags，請稍後重新整理頁面。',
      technicalDetail: '',
    };
  }

  if (normalized.includes('failed to load generation history')) {
    return {
      userMessage: '目前無法載入歷史生成紀錄，請稍後再試。',
      technicalDetail,
    };
  }

  if (normalized.includes('openai_api_key')) {
    return {
      userMessage: '系統尚未設定 OpenAI API Key，請先檢查 `.env` 設定。',
      technicalDetail,
    };
  }

  if (normalized.includes('connection error') || normalized.includes('timed out') || normalized.includes('timeout')) {
    return {
      userMessage: '連線到 AI 服務時逾時或中斷，請稍後再試。',
      technicalDetail,
    };
  }

  if (normalized.includes('invalid module data') || normalized.includes('invalid json') || normalized.includes('json')) {
    return {
      userMessage: 'AI 回傳的資料格式不符合系統預期，這次生成結果已被拒絕。',
      technicalDetail,
    };
  }

  if (normalized.includes('failed to parse file')) {
    return {
      userMessage: '檔案內容無法正確解析，請確認檔案格式與內容是否正常。',
      technicalDetail,
    };
  }

  if (normalized.includes('upload failed')) {
    return {
      userMessage: '檔案上傳失敗，請稍後再試。',
      technicalDetail,
    };
  }

  if (normalized.includes('not supported')) {
    return {
      userMessage: '這個檔案格式目前不支援，請改用 PDF、DOCX、TXT 或 MD。',
      technicalDetail,
    };
  }

  if (normalized.includes('file exceeds the 16 mb upload limit')) {
    return {
      userMessage: '檔案大小超過 16 MB 限制，請更換較小的檔案。',
      technicalDetail: '',
    };
  }

  if (normalized.includes('document appears to be empty')) {
    return {
      userMessage: '文件沒有足夠可擷取的文字內容，可能是空白檔或圖片型 PDF。',
      technicalDetail,
    };
  }

  if (normalized.includes('no file provided')) {
    return {
      userMessage: '這次請求沒有帶入檔案。',
      technicalDetail: '',
    };
  }

  if (normalized.includes('no file selected')) {
    return {
      userMessage: '尚未選擇任何檔案。',
      technicalDetail: '',
    };
  }

  if (normalized.includes('documents not found for this trainer')) {
    return {
      userMessage: '找不到這位 trainer 對應的文件，請確認文件是否已上傳。',
      technicalDetail,
    };
  }

  if (normalized.includes('job not found for this trainer')) {
    return {
      userMessage: '找不到這筆生成工作，可能已失效或 trainer 範圍不一致。',
      technicalDetail,
    };
  }

  if (normalized.includes('document not found for this trainer')) {
    return {
      userMessage: '找不到這份文件，可能不屬於目前的 trainer。',
      technicalDetail,
    };
  }

  if (normalized.includes('domain_ids')) {
    return {
      userMessage: '請至少選擇一個正確的 domain tag。',
      technicalDetail,
    };
  }

  if (normalized.includes('unknown domain_id')) {
    return {
      userMessage: '你選到的 domain tag 不存在，請重新整理後再試。',
      technicalDetail,
    };
  }

  if (normalized.includes('trainer_id')) {
    return {
      userMessage: 'Trainer ID 格式不正確，只能使用英數字、底線或連字號。',
      technicalDetail,
    };
  }

  if (normalized.includes('request body must be json')) {
    return {
      userMessage: '送出的資料格式不是 JSON，請重新操作一次。',
      technicalDetail,
    };
  }

  if (normalized.includes('route not found')) {
    return {
      userMessage: '目前找不到對應的 API 路徑。',
      technicalDetail: '',
    };
  }

  if (normalized.includes('unexpected error occurred while generating')) {
    return {
      userMessage: '生成過程中發生未預期錯誤，請稍後再試。',
      technicalDetail,
    };
  }

  return {
    userMessage: fallbackMessage,
    technicalDetail,
  };
}

async function loadDomains() {
  try {
    const res = await fetch('/api/domains');
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || 'Failed to load available domains.');
    }
    state.allDomains = data;
    renderDomainList(state.allDomains);
  } catch (error) {
    showError(error.message || 'Failed to load available domains.');
  }
}

async function loadHistory() {
  const list = document.getElementById('history-list');
  const emptyState = document.getElementById('history-empty-state');

  try {
    const res = await fetch('/api/history?limit=10', {
      headers: buildApiHeaders(),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.error || 'Failed to load generation history.');
    }
    renderHistory(data.history || []);
  } catch (_error) {
    list.innerHTML = '';
    emptyState.classList.remove('d-none');
    emptyState.textContent = '目前無法載入歷史紀錄。';
  }
}

function scheduleHistoryReload() {
  if (state.historyReloadTimer) {
    window.clearTimeout(state.historyReloadTimer);
  }
  state.historyReloadTimer = window.setTimeout(() => {
    loadHistory();
  }, 250);
}

function handleTrainerIdInput(event) {
  state.trainerId = event.target.value.trim() || 'trainer_001';
  scheduleHistoryReload();
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
      </label>
    `;
    item.querySelector('input').addEventListener('change', event => toggleDomain(domain.domain_id, event.target.checked));
    container.appendChild(item);
  });
}

function renderHistory(historyItems) {
  const list = document.getElementById('history-list');
  const emptyState = document.getElementById('history-empty-state');
  list.innerHTML = '';

  if (!historyItems.length) {
    emptyState.classList.remove('d-none');
    emptyState.textContent = '尚無歷史生成紀錄。';
    return;
  }

  emptyState.classList.add('d-none');

  historyItems.forEach(item => {
    const documents = item.documents || [];
    const domains = item.requested_domains || [];
    const modules = item.modules || [];
    const prompt = (item.requested_custom_prompt || '').trim();
    const summary = item.combined_summary || (item.result && item.result.document_summary) || '';
    const canOpenResult = Boolean(
      item.status === 'completed'
      && item.result
      && Array.isArray(item.result.modules)
      && item.result.modules.length > 0
    );

    const card = document.createElement('div');
    card.className = 'border rounded-3 p-3 bg-light-subtle';
    if (canOpenResult) {
      card.classList.add('history-card-clickable');
      card.role = 'button';
      card.tabIndex = 0;
      card.addEventListener('click', () => openHistoryResult(item));
      card.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openHistoryResult(item);
        }
      });
    }
    card.innerHTML = `
      <div class="d-flex justify-content-between align-items-start gap-2 flex-wrap mb-2">
        <div>
          <div class="fw-semibold">Batch #${escapeHtml(item.batch_id)}</div>
          <div class="small text-muted">${escapeHtml(formatHistoryTimestamp(item.completed_timestamp || item.created_timestamp))}</div>
        </div>
        <div class="d-flex align-items-center gap-2 flex-wrap">
          ${canOpenResult ? '<span class="badge text-bg-light border text-primary-emphasis">Click to open result</span>' : ''}
          <div>${buildHistoryStatusBadge(item.status)}</div>
        </div>
      </div>
      <div class="d-flex flex-wrap gap-2 mb-2 history-documents"></div>
      <div class="d-flex flex-wrap gap-2 mb-2 history-domains"></div>
      <div class="small text-muted mb-1">Custom Prompt</div>
      <div class="small mb-3 history-prompt"></div>
      <div class="small text-muted mb-1">Batch Summary</div>
      <div class="small mb-3 history-summary"></div>
      <div class="small text-muted mb-1">Generated Modules</div>
      <div class="d-flex flex-column gap-2 history-modules"></div>
    `;

    const documentsContainer = card.querySelector('.history-documents');
    documents.forEach(doc => {
      const chip = document.createElement('span');
      chip.className = 'badge rounded-pill text-bg-light border';
      chip.textContent = doc.file_name || `Document ${doc.doc_id}`;
      documentsContainer.appendChild(chip);
    });

    const domainsContainer = card.querySelector('.history-domains');
    domains.forEach(domain => {
      const chip = document.createElement('span');
      chip.className = 'badge rounded-pill text-bg-info-subtle border border-info-subtle text-info-emphasis';
      chip.textContent = `#${domain}`;
      domainsContainer.appendChild(chip);
    });

    const promptContainer = card.querySelector('.history-prompt');
    promptContainer.innerHTML = prompt
      ? escapeHtml(prompt)
      : '<span class="text-muted">No custom prompt</span>';

    const summaryContainer = card.querySelector('.history-summary');
    summaryContainer.innerHTML = summary
      ? escapeHtml(summary)
      : '<span class="text-muted">No summary saved</span>';

    const modulesContainer = card.querySelector('.history-modules');
    if (!modules.length) {
      const emptyModule = document.createElement('div');
      emptyModule.className = 'small text-muted';
      emptyModule.textContent = item.status === 'failed'
        ? (item.error_message || 'This batch failed before modules were saved.')
        : 'No modules saved yet.';
      modulesContainer.appendChild(emptyModule);
    } else {
      modules.forEach(module => {
        const moduleItem = document.createElement('div');
        moduleItem.className = 'small border rounded-2 px-2 py-2 bg-white';
        moduleItem.innerHTML = `
          <div class="fw-semibold">${escapeHtml(module.module_title || `Module ${module.sequence_order || ''}`)}</div>
          <div class="text-muted">${escapeHtml(module.key_takeaway || module.module_content || '')}</div>
        `;
        modulesContainer.appendChild(moduleItem);
      });
    }

    list.appendChild(card);
  });
}

function openHistoryResult(historyItem) {
  if (!historyItem || !historyItem.result) {
    showError('This history item does not include a saved result.');
    return;
  }

  renderBatchResults([{
    job_id: historyItem.job_id,
    batch_id: historyItem.batch_id,
    status: historyItem.status,
    result: historyItem.result,
  }]);
}

function buildHistoryStatusBadge(status) {
  const labelMap = {
    queued: ['Queued', 'text-bg-secondary'],
    running: ['Running', 'text-bg-primary'],
    completed: ['Completed', 'text-bg-success'],
    failed: ['Failed', 'text-bg-danger'],
  };
  const [label, badgeClass] = labelMap[status] || ['Unknown', 'text-bg-secondary'];
  return `<span class="badge ${badgeClass}">${label}</span>`;
}

function formatHistoryTimestamp(timestamp) {
  if (!timestamp) {
    return 'Timestamp unavailable';
  }

  const normalized = String(timestamp).replace(' ', 'T');
  const date = new Date(normalized);
  if (Number.isNaN(date.getTime())) {
    return timestamp;
  }

  return date.toLocaleString('zh-TW', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
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

  state.selectedDomainIds.forEach(domainId => {
    const domain = state.allDomains.find(item => item.domain_id === domainId);
    if (!domain) {
      return;
    }

    const pill = document.createElement('span');
    pill.className = 'badge bg-primary d-flex align-items-center gap-1 px-2 py-1';
    pill.innerHTML = `#${escapeHtml(domain.domain_name)} <button class="btn-close btn-close-white" style="font-size:.6rem;" aria-label="移除"></button>`;
    pill.querySelector('button').addEventListener('click', () => {
      state.selectedDomainIds.delete(domainId);
      const checkbox = document.getElementById(`domain-cb-${domainId}`);
      if (checkbox) {
        checkbox.checked = false;
      }
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
  document.getElementById('btn-clear-all-files').disabled = state.documents.length === 0 || state.isGenerating;
}

function updateGenerateButtonState() {
  const button = document.getElementById('btn-generate');
  const canGenerate = state.documents.length > 0 && state.selectedDomainIds.size > 0 && !state.isGenerating;
  button.disabled = !canGenerate;

  const warning = document.getElementById('domain-warning');
  warning.classList.toggle('d-none', state.documents.length > 0 && state.selectedDomainIds.size > 0);

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

  ['dragenter', 'dragover'].forEach(eventName => {
    zone.addEventListener(eventName, event => {
      event.preventDefault();
      zone.classList.add('drag-active');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    zone.addEventListener(eventName, event => {
      event.preventDefault();
      zone.classList.remove('drag-active');
    });
  });

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
  if (files.length === 0) {
    return;
  }

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
    } catch (error) {
      issues.push(`${file.name}: ${error.message}`);
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
  state.documents.push({
    doc_id: data.doc_id,
    trainer_id: data.trainer_id,
    file_name: data.file_name,
    preview_text: data.preview_text,
    char_count: data.char_count,
  });

  state.trainerId = data.trainer_id;
  document.getElementById('trainer-id').value = data.trainer_id;
}

function removeDocument(docId) {
  if (state.isGenerating) {
    return;
  }

  state.documents = state.documents.filter(doc => doc.doc_id !== docId);
  clearSplitScreen();
  clearJobStatus();
  updateGenerateButtonState();
}

function clearAllFiles() {
  if (state.isGenerating) {
    return;
  }

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

  showLoading('Generating integrated learning sprints...');
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
    if (!res.ok) {
      throw new Error(data.error || 'Generation failed.');
    }

    const jobs = data.jobs || [];
    state.activeJobIds = jobs.map(job => job.job_id);
    showJobStatus('queued', `已建立 ${jobs.length} 個 batch job，開始執行兩階段生成。`);
    hideLoading();

    const finalJobs = await pollJobsUntilFinished(jobs);
    const failedJob = finalJobs.find(job => job.status !== 'completed' || !job.result);
    if (failedJob) {
      await loadHistory();
      throw new Error(failedJob.error_message || `Generation did not complete successfully for batch ${failedJob.batch_id}.`);
    }

    renderBatchResults(finalJobs);
    await loadHistory();
  } catch (error) {
    showError(error.message);
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
          if (!res.ok) {
            throw new Error(data.error || 'Failed to fetch job status.');
          }
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
    const status = failedCount > 0
      ? 'failed'
      : pendingJobs.size === 0
        ? 'completed'
        : runningCount > 0
          ? 'running'
          : 'queued';

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

  if (!jobs.length) {
    return;
  }

  const job = jobs[0];
  const result = job.result || {};
  const documents = (result.documents || []).map(doc => {
    const safeFullText = doc.safe_full_text || doc.preview_text || '';
    const paragraphs = splitIntoParagraphsSafe(safeFullText);
    return {
      ...doc,
      safe_full_text: safeFullText,
      paragraphs,
      paragraphLookup: buildParagraphLookup(paragraphs),
    };
  });
  const modules = result.modules || [];
  const domains = result.domains || [];
  const documentsById = new Map(documents.map(doc => [Number(doc.doc_id), doc]));

  const block = document.createElement('section');
  block.className = 'result-document-block';
  block.innerHTML = `
    <div class="d-flex justify-content-between align-items-center mb-3 gap-2 flex-wrap">
      <div>
        <div class="result-document-title fw-semibold">Integrated Batch Result</div>
        <div class="text-muted small">${escapeHtml(result.document_summary || '')}</div>
      </div>
      <div class="d-flex flex-wrap gap-1 result-domain-pills"></div>
    </div>
    <div class="row g-3">
      <div class="col-md-5">
        <div class="split-panel-header">
          <i class="bi bi-journal-text me-1"></i>Source Viewer
        </div>
        <div class="split-pane sprint-panel">
          <div class="source-viewer-toolbar mb-3" id="source-viewer-toolbar"></div>
          <div class="source-viewer-meta mb-3" id="source-viewer-meta"></div>
          <div class="source-viewer-body" id="source-viewer-body"></div>
        </div>
      </div>
      <div class="col-md-7">
        <div class="split-panel-header">
          <i class="bi bi-stars me-1"></i>Integrated Learning Sprints
        </div>
        <div class="split-pane sprint-panel" id="batch-modules-panel"></div>
      </div>
    </div>
  `;

  const pillsContainer = block.querySelector('.result-domain-pills');
  domains.forEach(name => {
    const span = document.createElement('span');
    span.className = 'badge bg-info text-dark';
    span.textContent = `#${name}`;
    pillsContainer.appendChild(span);
  });

  const sourceToolbar = block.querySelector('#source-viewer-toolbar');
  const sourceMeta = block.querySelector('#source-viewer-meta');
  const sourceBody = block.querySelector('#source-viewer-body');

  documents.forEach(doc => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-sm btn-outline-secondary source-doc-tab';
    button.dataset.docId = doc.doc_id;
    button.textContent = doc.file_name || `Document ${doc.doc_id}`;
    button.addEventListener('click', () => renderSourceViewer(doc.doc_id, null, false));
    sourceToolbar.appendChild(button);
  });

  function renderSourceViewer(docId, module = null, shouldScroll = false) {
    const selectedDoc = documentsById.get(Number(docId));
    if (!selectedDoc) {
      return;
    }

    const evidence = module ? resolveModuleEvidence(module, selectedDoc.doc_id) : null;
    const matchedParagraphIndex = module
      ? resolveMatchedParagraphIndex(selectedDoc, module, evidence)
      : null;

    sourceToolbar.querySelectorAll('.source-doc-tab').forEach(button => {
      const isActive = Number(button.dataset.docId) === Number(docId);
      button.classList.toggle('btn-primary', isActive);
      button.classList.toggle('btn-outline-secondary', !isActive);
      button.classList.toggle('active', isActive);
    });

    sourceMeta.innerHTML = `
      <div class="small text-muted">Selected source</div>
      <div class="fw-semibold">${escapeHtml(selectedDoc.file_name || `Document ${selectedDoc.doc_id}`)}</div>
      <div class="small text-muted">doc_id: ${escapeHtml(selectedDoc.doc_id)} • ${Number(selectedDoc.char_count || 0).toLocaleString()} chars</div>
      ${module ? '<div class="small text-primary mt-1">已定位到這張學習卡最可能對應的原文段落。</div>' : ''}
    `;
    sourceMeta.insertAdjacentHTML(
      'beforeend',
      module
        ? renderSourceEvidenceMeta(module, evidence)
        : `
          <div class="small text-muted mt-2">
            Select a sprint source card on the right to jump directly to the best-matching passage.
          </div>
        `
    );

    sourceBody.innerHTML = '';
    selectedDoc.paragraphs.forEach((paragraph, index) => {
      const paragraphEl = document.createElement('div');
      paragraphEl.className = 'source-paragraph';
      if (matchedParagraphIndex !== null && index === matchedParagraphIndex && module) {
        paragraphEl.classList.add('is-matched');
      }
      paragraphEl.dataset.paragraphIndex = index;
      paragraphEl.innerHTML = renderMultilineText(paragraph);
      sourceBody.appendChild(paragraphEl);
    });

    if (module && shouldScroll) {
      const matchedEl = sourceBody.querySelector('.source-paragraph.is-matched');
      if (matchedEl) {
        matchedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    } else {
      sourceBody.scrollTop = 0;
    }
  }

  const modulesPanel = block.querySelector('#batch-modules-panel');
  modules.forEach((mod, index) => {
    const sprintOrder = mod.sequence_order != null ? mod.sequence_order : index + 1;
    const readingTime = mod.reading_time_minutes != null ? mod.reading_time_minutes : 2;
    const domainBadges = domains.map(name =>
      `<span class="badge rounded-pill text-bg-info-subtle border border-info-subtle text-info-emphasis">#${escapeHtml(name)}</span>`
    ).join(' ');

    const card = document.createElement('div');
    card.className = 'card sprint-card mb-3';
    card.innerHTML = `
      <div class="card-header sprint-card-header">
        <div class="sprint-card-meta">
          <span class="badge sprint-order-badge">Sprint ${sprintOrder}</span>
          <span class="badge sprint-time-badge">
            <i class="bi bi-clock me-1"></i>${readingTime} min
          </span>
        </div>
        <div class="sprint-card-title">${escapeHtml(mod.title || '')}</div>
      </div>
      <div class="card-body">
        <div class="sprint-card-section">
          <div class="sprint-card-section-label">Focused domains</div>
          <div class="module-domain-pills">${domainBadges}</div>
        </div>
        <div class="sprint-card-section">
          <div class="sprint-card-section-label">Linked source passages</div>
          <div class="source-evidence-list"></div>
        </div>
        <div class="sprint-card-section">
          <div class="sprint-card-section-label">Learning notes</div>
        <div class="module-content">${renderStructuredModuleContent(mod.content || '')}</div>
        </div>
        ${mod.key_takeaway ? `
          <blockquote class="key-takeaway mb-0">
            <i class="bi bi-lightbulb-fill text-warning me-1"></i>
            <em>${escapeHtml(mod.key_takeaway)}</em>
          </blockquote>
        ` : ''}
      </div>
    `;

    const sourceEvidenceList = card.querySelector('.source-evidence-list');
    buildModuleSourceEntries(mod, documentsById).forEach(entry => {
      const sourceButton = document.createElement('button');
      sourceButton.type = 'button';
      sourceButton.className = 'source-evidence-item';
      sourceButton.innerHTML = `
        <div class="source-evidence-item-top">
          <span class="badge rounded-pill text-bg-light border source-link-btn">
            ${escapeHtml(entry.doc.file_name || `Document ${entry.docId}`)}
          </span>
          ${entry.isPrimary ? '<span class="badge text-bg-primary-subtle border border-primary-subtle">Best match</span>' : ''}
        </div>
        <div class="source-evidence-item-text">
          ${escapeHtml(entry.evidence?.matched_excerpt || 'Open the linked source passage for this sprint.')}
        </div>
      `;
      sourceButton.addEventListener('click', () => renderSourceViewer(entry.docId, mod, true));
      sourceEvidenceList.appendChild(sourceButton);
    });

    modulesPanel.appendChild(card);
  });

  resultsContainer.appendChild(block);
  document.getElementById('summary-badge').textContent = `${documents.length} document(s), ${modules.length} module(s)`;
  document.getElementById('split-screen').classList.remove('d-none');
  document.getElementById('split-screen').scrollIntoView({ behavior: 'smooth', block: 'start' });

  if (modules.length > 0 && documents.length > 0) {
    renderSourceViewer(modules[0].primary_source_doc_id || documents[0].doc_id, modules[0], false);
  } else if (documents.length > 0) {
    renderSourceViewer(documents[0].doc_id, null, false);
  }
}

function buildParagraphLookup(paragraphs) {
  const lookup = new Map();
  (paragraphs || []).forEach((paragraph, index) => {
    const normalized = normalizeSearchText(paragraph);
    if (normalized && !lookup.has(normalized)) {
      lookup.set(normalized, index);
    }
  });
  return lookup;
}

function buildModuleSourceEntries(module, documentsById) {
  const entries = [];
  const seenDocIds = new Set();
  const evidenceItems = Array.isArray(module.source_evidence) ? module.source_evidence : [];

  evidenceItems.forEach(evidence => {
    const docId = Number(evidence.doc_id);
    const doc = documentsById.get(docId);
    if (!doc || seenDocIds.has(docId)) {
      return;
    }

    seenDocIds.add(docId);
    entries.push({
      docId,
      doc,
      evidence,
      isPrimary: Number(module.primary_source_doc_id) === docId,
    });
  });

  (module.source_doc_ids || []).forEach(rawDocId => {
    const docId = Number(rawDocId);
    const doc = documentsById.get(docId);
    if (!doc || seenDocIds.has(docId)) {
      return;
    }

    seenDocIds.add(docId);
    entries.push({
      docId,
      doc,
      evidence: null,
      isPrimary: Number(module.primary_source_doc_id) === docId,
    });
  });

  return entries;
}

function resolveModuleEvidence(module, docId) {
  return (module.source_evidence || []).find(item => Number(item.doc_id) === Number(docId)) || null;
}

function resolveMatchedParagraphIndex(document, module, evidence) {
  if (!document) {
    return 0;
  }

  if (
    evidence &&
    Number.isInteger(evidence.matched_paragraph_index) &&
    evidence.matched_paragraph_index >= 0 &&
    evidence.matched_paragraph_index < document.paragraphs.length
  ) {
    return evidence.matched_paragraph_index;
  }

  const matchedText = normalizeSearchText(evidence?.matched_text || '');
  if (matchedText && document.paragraphLookup?.has(matchedText)) {
    return document.paragraphLookup.get(matchedText);
  }

  return findBestParagraphIndex(document.paragraphs, module);
}

function renderSourceEvidenceMeta(module, evidence) {
  const title = escapeHtml(module.title || 'Selected sprint');
  const snippet = escapeHtml(
    evidence?.matched_excerpt ||
    evidence?.matched_text ||
    'No explicit source excerpt was identified for this sprint.'
  );
  const matchedTerms = Array.isArray(evidence?.matched_terms) ? evidence.matched_terms : [];
  const termBadges = matchedTerms.length
    ? `
      <div class="source-evidence-tags mt-2">
        ${matchedTerms.map(term => `<span class="source-evidence-term">${escapeHtml(term)}</span>`).join('')}
      </div>
    `
    : '';

  return `
    <div class="source-evidence-box mt-2">
      <div class="source-evidence-label">Linked to sprint: ${title}</div>
      <div class="source-evidence-snippet">${snippet}</div>
      ${termBadges}
    </div>
  `;
}

function splitIntoParagraphs(text) {
  const normalized = String(text || '').replace(/\r\n/g, '\n').trim();
  if (!normalized) {
    return ['No source text available.'];
  }

  const primaryChunks = normalized
    .split(/\n\s*\n+/)
    .map(chunk => chunk.trim())
    .filter(Boolean);

  const paragraphs = [];
  const chunks = primaryChunks.length ? primaryChunks : [normalized];

  chunks.forEach(chunk => {
    if (chunk.length <= 700) {
      paragraphs.push(chunk);
      return;
    }

    const sentences = chunk.split(/(?<=[。！？.!?])\s+/).filter(Boolean);
    let buffer = '';
    sentences.forEach(sentence => {
      const next = buffer ? `${buffer} ${sentence}` : sentence;
      if (next.length > 700 && buffer) {
        paragraphs.push(buffer.trim());
        buffer = sentence;
      } else {
        buffer = next;
      }
    });
    if (buffer.trim()) {
      paragraphs.push(buffer.trim());
    }
  });

  return paragraphs.length ? paragraphs : [normalized];
}

function splitIntoParagraphsSafe(text) {
  const normalized = String(text || '').replace(/\r\n/g, '\n').trim();
  if (!normalized) {
    return ['No source text available.'];
  }

  const primaryChunks = normalized
    .split(/\n\s*\n+/)
    .map(chunk => chunk.trim())
    .filter(Boolean);

  const paragraphs = [];
  (primaryChunks.length ? primaryChunks : [normalized]).forEach(chunk => {
    if (chunk.length <= 700) {
      paragraphs.push(chunk);
      return;
    }

    const sentences = chunk.split(/(?<=[.!?。！？；;])\s+|\n+/).filter(Boolean);
    let buffer = '';
    sentences.forEach(sentence => {
      const next = buffer ? `${buffer} ${sentence}` : sentence;
      if (next.length > 700 && buffer) {
        paragraphs.push(buffer.trim());
        buffer = sentence;
      } else {
        buffer = next;
      }
    });
    if (buffer.trim()) {
      paragraphs.push(buffer.trim());
    }
  });

  return paragraphs.length ? paragraphs : [normalized];
}

function renderStructuredModuleContent(text) {
  const normalized = normalizeStructuredModuleText(text);
  if (!normalized) {
    return '<p class="module-paragraph text-muted mb-0">No module content generated.</p>';
  }

  const lines = normalized
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);

  const blocks = [];
  let plainLines = [];
  let currentSubsection = null;

  function flushPlainLines() {
    if (!plainLines.length) {
      return;
    }
    blocks.push({
      type: 'line-group',
      items: plainLines,
    });
    plainLines = [];
  }

  function flushSubsection() {
    if (!currentSubsection) {
      return;
    }
    blocks.push(currentSubsection);
    currentSubsection = null;
  }

  lines.forEach(line => {
    const isBulletLine = line.startsWith('- ');
    const cleanedLine = isBulletLine ? line.slice(2).trim() : line;

    if (isStructuredSectionHeading(cleanedLine) && !isBulletLine) {
      flushSubsection();
      flushPlainLines();
      blocks.push({
        type: 'heading',
        text: cleanedLine.replace(/[\uFF1A:]$/, ''),
      });
      return;
    }

    if (isBulletLine && isStructuredSectionHeading(cleanedLine)) {
      flushSubsection();
      flushPlainLines();
      currentSubsection = {
        type: 'subsection',
        title: cleanedLine,
        items: [],
      };
      return;
    }

    if (currentSubsection) {
      currentSubsection.items.push(cleanedLine);
      return;
    }

    plainLines.push(cleanedLine);
  });

  flushSubsection();
  flushPlainLines();

  return blocks.map(block => {
    if (block.type === 'heading') {
      const toneClass = `module-tone-${getModuleHeadingTone(block.text)}`;
      return `<div class="module-section-title ${toneClass}">${escapeHtml(block.text)}</div>`;
    }
    if (block.type === 'subsection') {
      const toneClass = `module-tone-${getModuleHeadingTone(block.title)}`;
      return `
        <div class="module-subsection ${toneClass}">
          <div class="module-subsection-title ${toneClass}">\uFF0E${escapeHtml(block.title)}</div>
          <div class="module-subsection-items">
            ${block.items.map(item => `<div class="module-subsection-item">${escapeHtml(item)}</div>`).join('')}
          </div>
        </div>
      `;
    }
    if (block.type === 'line-group') {
      return `
        <div class="module-line-group">
          ${block.items.map(item => `<div class="module-line-item">${escapeHtml(item)}</div>`).join('')}
        </div>
      `;
    }
    return `<p class="module-paragraph">${escapeHtml(block.text)}</p>`;
  }).join('');
}

function normalizeStructuredModuleText(text) {
  return String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/([.\u3002!\uFF01?\uFF1F])\s*([^-\n]{2,32}[\uFF1A:])/g, '$1\n$2')
    .replace(/([\uFF1A:])\s*-\s+/g, '$1\n- ')
    .replace(/\s+-\s+(?=\S)/g, '\n- ')
    .trim();
}

function isStructuredSectionHeading(line) {
  return /^[^-\n]{2,32}[\uFF1A:]$/.test(String(line || '').trim());
}

function getModuleHeadingTone(text) {
  const normalized = normalizeSearchText(text);

  if (/提醒|注意|風險|警示/.test(normalized)) {
    return 'warning';
  }
  if (/應用|情境|做法|步驟|執行/.test(normalized)) {
    return 'application';
  }
  if (/重點條列|核心|總結|摘要/.test(normalized)) {
    return 'key';
  }
  if (/稅|法規|合規|規範/.test(normalized)) {
    return 'tax';
  }
  if (/規劃|配置|策略|重點/.test(normalized)) {
    return 'planning';
  }

  return 'default';
}

function findBestParagraphIndex(paragraphs, module) {
  if (!module || !Array.isArray(paragraphs) || paragraphs.length === 0) {
    return 0;
  }

  const phrases = buildMatchPhrases(module);
  if (!phrases.length) {
    return 0;
  }

  let bestIndex = 0;
  let bestScore = -1;

  paragraphs.forEach((paragraph, index) => {
    const normalizedParagraph = normalizeSearchText(paragraph);
    let score = 0;

    phrases.forEach(phrase => {
      if (normalizedParagraph.includes(phrase.normalized)) {
        score += phrase.weight;
      }
    });

    if (score > bestScore) {
      bestScore = score;
      bestIndex = index;
    }
  });

  return bestScore > 0 ? bestIndex : 0;
}

function buildMatchPhrases(module) {
  const rawPhrases = String([
    module.title || '',
    module.content || '',
    module.key_takeaway || '',
  ].join(' '))
    .split(/[\n\r,.;:!?，。；：！？()（）\[\]【】"“”'\/\s]+/)
    .map(item => item.trim())
    .filter(Boolean);

  const seen = new Set();
  const phrases = [];

  rawPhrases.forEach(phrase => {
    const normalized = normalizeSearchText(phrase);
    if (!normalized || seen.has(normalized)) {
      return;
    }

    const hasCjk = /[\u3400-\u9fff]/.test(normalized);
    const isUseful = hasCjk ? normalized.length >= 2 : normalized.length >= 4;
    if (!isUseful) {
      return;
    }

    seen.add(normalized);
    phrases.push({
      normalized,
      weight: Math.min(Math.max(normalized.length, 3), 20),
    });
  });

  return phrases.sort((a, b) => b.weight - a.weight).slice(0, 16);
}

function normalizeSearchText(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim();
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
  document.getElementById('job-status-message').textContent = '等待開始產生。';
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
  const details = document.getElementById('error-details');
  const technicalDetail = document.getElementById('error-technical-detail');
  const friendly = buildFriendlyError(message);

  document.getElementById('error-message').textContent = friendly.userMessage;
  technicalDetail.textContent = friendly.technicalDetail;
  details.classList.toggle('d-none', !friendly.technicalDetail);
  details.open = false;
  alert.classList.remove('d-none');
  alert.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

function hideError() {
  document.getElementById('error-alert').classList.add('d-none');
  document.getElementById('error-message').textContent = '';
  document.getElementById('error-technical-detail').textContent = '';
  document.getElementById('error-details').classList.add('d-none');
  document.getElementById('error-details').open = false;
}

function wait(ms) {
  return new Promise(resolve => window.setTimeout(resolve, ms));
}

function renderMultilineText(text) {
  return escapeHtml(text).replace(/\n/g, '<br>');
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
