// ============================================
// Element References
// ============================================

function $(id) {
  return document.getElementById(id);
}

// Wizard elements
const wizardSteps = document.querySelectorAll('.wizard-step');
const progressSteps = document.querySelectorAll('.progress-step');
const progressFill = $('progressFill');

// Step 0: Welcome
const startBtn = $('startBtn');

// Step 1: Page Type
const choiceNormal = $('choiceNormal');
const choiceCommunity = $('choiceCommunity');
const pageTypeInput = $('pageType');
const back1 = $('back1');
const next1 = $('next1');

// Step 2: cURL
const normalCurlSection = $('normalCurlSection');
const communityCurlSection = $('communityCurlSection');
const curlInput = $('curlInput');
const communityCurlInput = $('communityCurlInput');
const curlStepTitle = $('curlStepTitle');
const curlStepDesc = $('curlStepDesc');
const back2 = $('back2');
const next2 = $('next2');

// Step 3: Analysis Mode
const choiceWithExpected = $('choiceWithExpected');
const choiceQueryOnly = $('choiceQueryOnly');
const analysisModeInput = $('analysisMode');
const back3 = $('back3');
const next3 = $('next3');

// Step 4: Upload
const uploadZone = $('uploadZone');
const csvFile = $('csvFile');
const fileInfo = $('fileInfo');
const fileName = $('fileName');
const fileMeta = $('fileMeta');
const removeFile = $('removeFile');
const uploadDesc = $('uploadDesc');
const back4 = $('back4');
const next4 = $('next4');

// Step 5: Configure
const configTitle = $('configTitle');
const configDesc = $('configDesc');
const queryColumn = $('queryColumn');
const topNField = $('topNField');
const topNInput = $('topNInput');
const matchModeField = $('matchModeField');
const matchMode = $('matchMode');
const expectedTitleField = $('expectedTitleField');
const expectedTitleColumn = $('expectedTitleColumn');
const expectedUrlField = $('expectedUrlField');
const expectedUrlColumn = $('expectedUrlColumn');
const previewTable = $('previewTable');
const runAnalysisBtn = $('runAnalysis');
const back5 = $('back5');

// Step 6: Results
const runProgress = $('runProgress');
const runProgressFill = $('runProgressFill');
const runStatus = $('runStatus');
const resultsSection = $('resultsSection');
const results = $('results');
const downloadCsv = $('downloadCsv');
const validation = $('validation');
const startOver = $('startOver');

// ============================================
// State
// ============================================

let currentStep = 0;
let currentCsv = { headers: [], rows: [] };

// ============================================
// Utility Functions
// ============================================

function setHidden(el, hidden) {
  if (el) el.classList.toggle('hidden', hidden);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ============================================
// Wizard Navigation
// ============================================

function goToStep(step) {
  // Hide all steps
  wizardSteps.forEach((s, i) => {
    s.classList.toggle('active', i === step);
  });

  // Update progress
  progressSteps.forEach((s, i) => {
    s.classList.remove('active', 'completed');
    if (i < step) s.classList.add('completed');
    if (i === step) s.classList.add('active');
  });

  // Update progress bar
  const progress = (step / (wizardSteps.length - 1)) * 100;
  progressFill.style.width = progress + '%';

  currentStep = step;
}

// ============================================
// Page Type Selection (Step 1)
// ============================================

function selectPageType(type) {
  pageTypeInput.value = type;
  choiceNormal.classList.toggle('selected', type === 'normal');
  choiceCommunity.classList.toggle('selected', type === 'community');
  next1.disabled = false;
}

function updateCurlStep() {
  const isCommunity = pageTypeInput.value === 'community';
  setHidden(normalCurlSection, isCommunity);
  setHidden(communityCurlSection, !isCommunity);

  if (isCommunity) {
    curlStepTitle.textContent = 'Paste Community cURL';
    curlStepDesc.textContent = 'Paste the cURL from your community search endpoint';
  } else {
    curlStepTitle.textContent = 'Paste cURL Command';
    curlStepDesc.textContent = 'Copy the cURL from your browser\'s developer tools';
  }
}

choiceNormal.addEventListener('click', () => selectPageType('normal'));
choiceCommunity.addEventListener('click', () => selectPageType('community'));

// ============================================
// cURL Validation (Step 2)
// ============================================

function validateCurl() {
  const isCommunity = pageTypeInput.value === 'community';
  const curl = isCommunity ? communityCurlInput.value.trim() : curlInput.value.trim();
  next2.disabled = curl.length === 0;
}

curlInput.addEventListener('input', validateCurl);
communityCurlInput.addEventListener('input', validateCurl);

// ============================================
// Analysis Mode Selection (Step 3)
// ============================================

function selectAnalysisMode(mode) {
  analysisModeInput.value = mode;
  choiceWithExpected.classList.toggle('selected', mode === 'with_expected');
  choiceQueryOnly.classList.toggle('selected', mode === 'query_only');
}

choiceWithExpected.addEventListener('click', () => selectAnalysisMode('with_expected'));
choiceQueryOnly.addEventListener('click', () => selectAnalysisMode('query_only'));

// ============================================
// File Upload (Step 4)
// ============================================

function isExcelFile(file) {
  const name = file.name.toLowerCase();
  return name.endsWith('.xlsx') || name.endsWith('.xlsm') || name.endsWith('.xls');
}

async function parseExcelFile(file) {
  const arrayBuffer = await file.arrayBuffer();
  const workbook = XLSX.read(arrayBuffer, { type: 'array' });
  const firstSheetName = workbook.SheetNames[0];
  if (!firstSheetName) throw new Error('Excel file has no sheets.');
  const worksheet = workbook.Sheets[firstSheetName];
  return XLSX.utils.sheet_to_json(worksheet, { header: 1, defval: '' });
}

function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = '';
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    const next = text[i + 1];

    if (inQuotes) {
      if (c === '"' && next === '"') {
        field += '"';
        i++;
      } else if (c === '"') {
        inQuotes = false;
      } else {
        field += c;
      }
      continue;
    }

    if (c === '"') { inQuotes = true; continue; }
    if (c === ',') { row.push(field); field = ''; continue; }
    if (c === '\n') { row.push(field); field = ''; rows.push(row); row = []; continue; }
    if (c === '\r') continue;
    field += c;
  }

  row.push(field);
  rows.push(row);

  while (rows.length && rows[rows.length - 1].every(v => v.trim() === '')) {
    rows.pop();
  }

  return rows;
}

function normalizeHeaders(headers) {
  const seen = new Map();
  return headers.map((h, idx) => {
    const base = (h ?? '').toString().trim() || `Column ${idx + 1}`;
    const count = seen.get(base) ?? 0;
    seen.set(base, count + 1);
    return count === 0 ? base : `${base} (${count + 1})`;
  });
}

async function handleFile(file) {
  let parsed;
  let fileType = 'CSV';

  if (isExcelFile(file)) {
    fileType = 'Excel';
    parsed = await parseExcelFile(file);
  } else {
    const text = await file.text();
    parsed = parseCsv(text);
  }

  if (!parsed.length) throw new Error(`${fileType} file is empty.`);

  const rawHeaders = parsed[0].map(h => String(h ?? ''));
  const headers = normalizeHeaders(rawHeaders);
  const rows = parsed.slice(1).filter(r => r.some(v => String(v ?? '').trim() !== ''));

  currentCsv = { headers, rows };

  // Show file info
  fileName.textContent = file.name;
  fileMeta.textContent = `${rows.length} rows, ${headers.length} columns`;
  setHidden(uploadZone, true);
  setHidden(fileInfo, false);
  next4.disabled = false;
}

csvFile.addEventListener('change', async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;

  try {
    await handleFile(file);
  } catch (err) {
    validation.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  }
});

// Drag and drop
uploadZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  uploadZone.classList.add('dragover');
});

uploadZone.addEventListener('dragleave', () => {
  uploadZone.classList.remove('dragover');
});

uploadZone.addEventListener('drop', async (e) => {
  e.preventDefault();
  uploadZone.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) {
    csvFile.files = e.dataTransfer.files;
    await handleFile(file);
  }
});

removeFile.addEventListener('click', () => {
  csvFile.value = '';
  currentCsv = { headers: [], rows: [] };
  setHidden(uploadZone, false);
  setHidden(fileInfo, true);
  next4.disabled = true;
});

// ============================================
// Configure Step (Step 5)
// ============================================

function setupConfigStep() {
  const isQueryOnly = analysisModeInput.value === 'query_only';

  // Update UI based on mode
  setHidden(topNField, !isQueryOnly);
  setHidden(matchModeField, isQueryOnly);
  setHidden(expectedTitleField, isQueryOnly);
  setHidden(expectedUrlField, isQueryOnly);

  if (isQueryOnly) {
    configTitle.textContent = 'Configure Query Settings';
    configDesc.textContent = 'Select the column containing your search queries';
    uploadDesc.textContent = 'Upload a file with your search queries';
  } else {
    configTitle.textContent = 'Configure Columns';
    configDesc.textContent = 'Map your file columns to the required fields';
    uploadDesc.textContent = 'Upload a file with queries and expected results';
  }

  // Populate dropdowns
  const headers = currentCsv.headers;

  queryColumn.innerHTML = headers.map(h =>
    `<option value="${escapeHtml(h)}">${escapeHtml(h)}</option>`
  ).join('');

  const optionalOpts = `<option value="">(none)</option>` +
    headers.map(h => `<option value="${escapeHtml(h)}">${escapeHtml(h)}</option>`).join('');

  expectedTitleColumn.innerHTML = optionalOpts;
  expectedUrlColumn.innerHTML = optionalOpts;

  // Auto-select common column names
  const lowerHeaders = headers.map(h => h.toLowerCase());
  const queryCands = ['query', 'keyword', 'keywords', 'search', 'q', 'generated_query'];
  const titleCands = ['expected title', 'title', 'expected_title'];
  const urlCands = ['expected url', 'url', 'expected_url', 'document_url'];

  for (const c of queryCands) {
    const idx = lowerHeaders.indexOf(c);
    if (idx >= 0) { queryColumn.value = headers[idx]; break; }
  }
  for (const c of titleCands) {
    const idx = lowerHeaders.indexOf(c);
    if (idx >= 0) { expectedTitleColumn.value = headers[idx]; break; }
  }
  for (const c of urlCands) {
    const idx = lowerHeaders.indexOf(c);
    if (idx >= 0) { expectedUrlColumn.value = headers[idx]; break; }
  }

  // Render preview
  renderPreview();
}

function renderPreview() {
  const thead = previewTable.querySelector('thead');
  const tbody = previewTable.querySelector('tbody');

  thead.innerHTML = `<tr>${currentCsv.headers.map(h =>
    `<th>${escapeHtml(h)}</th>`
  ).join('')}</tr>`;

  const previewRows = currentCsv.rows.slice(0, 5);
  tbody.innerHTML = previewRows.map(r =>
    `<tr>${currentCsv.headers.map((_, i) =>
      `<td>${escapeHtml(r[i] ?? '')}</td>`
    ).join('')}</tr>`
  ).join('');
}

// ============================================
// Run Analysis (Step 5 -> 6)
// ============================================

async function fileToCSVText(file) {
  if (isExcelFile(file)) {
    const arrayBuffer = await file.arrayBuffer();
    const workbook = XLSX.read(arrayBuffer, { type: 'array' });
    const firstSheetName = workbook.SheetNames[0];
    const worksheet = workbook.Sheets[firstSheetName];
    return XLSX.utils.sheet_to_csv(worksheet);
  }
  return await file.text();
}

async function runAnalysis() {
  const isCommunity = pageTypeInput.value === 'community';
  const isQueryOnly = analysisModeInput.value === 'query_only';
  const curl = isCommunity ? communityCurlInput.value.trim() : curlInput.value.trim();

  if (!curl) {
    validation.innerHTML = `<div class="error">cURL command is missing.</div>`;
    return;
  }

  const file = csvFile.files?.[0];
  if (!file) {
    validation.innerHTML = `<div class="error">No file uploaded.</div>`;
    return;
  }

  const csvText = await fileToCSVText(file);
  const topN = isQueryOnly ? parseInt(topNInput.value, 10) : 50;

  const payload = {
    curl,
    csvText,
    queryColumn: queryColumn.value,
    analysisMode: isQueryOnly ? 'query_only' : 'with_expected',
    expectedTitleColumn: isQueryOnly ? null : (expectedTitleColumn.value || null),
    expectedUrlColumn: isQueryOnly ? null : (expectedUrlColumn.value || null),
    matchMode: isQueryOnly ? 'title_and_url' : matchMode.value,
    topN,
    pageType: isCommunity ? 'community' : 'normal',
  };

  // Go to results step
  goToStep(6);
  setHidden(runProgress, false);
  setHidden(resultsSection, true);
  if (llmPromptEl) setHidden(llmPromptEl, true);
  runProgressFill.style.width = '0%';
  runStatus.textContent = 'Starting analysis...';

  try {
    const res = await fetch('api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data?.detail || 'Run creation failed');

    await pollRun(data.runId, isQueryOnly);
  } catch (err) {
    validation.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  }
}

async function pollRun(runId, isQueryOnly) {
  const startedAt = Date.now();
  const maxMs = 10 * 60 * 1000;

  // Get log viewer elements
  const logContent = document.getElementById('logContent');
  const logCount = document.getElementById('logCount');
  const logViewer = document.getElementById('logViewer');

  // Clear previous logs
  logContent.innerHTML = '<div class="log-empty">Waiting for logs...</div>';
  logCount.textContent = '0 entries';

  let lastLogCount = 0;

  while (true) {
    const res = await fetch(`api/runs/${encodeURIComponent(runId)}`);
    if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
    const st = await res.json();

    const progress = st.totalQueries > 0
      ? (st.completedQueries / st.totalQueries) * 100
      : 0;
    runProgressFill.style.width = progress + '%';
    runStatus.textContent = `Processing: ${st.completedQueries}/${st.totalQueries} queries`;

    // Update logs
    if (st.logs && st.logs.length > 0) {
      updateLogViewer(st.logs, logContent, logCount, lastLogCount);
      lastLogCount = st.logs.length;
    }

    if (st.status === 'done') {
      setHidden(runProgress, true);
      setHidden(resultsSection, false);

      downloadCsv.href = `api/runs/${encodeURIComponent(runId)}/download`;

      const rr = await fetch(`api/runs/${encodeURIComponent(runId)}/results`);
      if (!rr.ok) throw new Error('Fetching results failed');
      const data = await rr.json();
      renderResults(data.results, isQueryOnly);
      showLlmPrompt();
      return;
    }

    if (st.status === 'error') {
      throw new Error(st.message || 'Run failed');
    }

    if (Date.now() - startedAt > maxMs) {
      throw new Error('Run timed out');
    }

    await new Promise(r => setTimeout(r, 900));
  }
}

function updateLogViewer(logs, logContent, logCount, previousCount) {
  // Update log count
  logCount.textContent = `${logs.length} entries`;

  // If first logs, clear the placeholder
  if (previousCount === 0 && logs.length > 0) {
    logContent.innerHTML = '';
  }

  // Only add new logs
  for (let i = previousCount; i < logs.length; i++) {
    const log = logs[i];
    const entry = document.createElement('div');
    entry.className = `log-entry ${log.level}`;

    // Format time (show only time part)
    const time = new Date(log.time).toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });

    entry.innerHTML = `
      <span class="log-time">${time}</span>
      <span class="log-level ${log.level}">${log.level}</span>
      <span class="log-message">${escapeHtml(log.message)}</span>
    `;

    logContent.appendChild(entry);
  }

  // Auto-scroll to bottom
  logContent.scrollTop = logContent.scrollHeight;
}

function renderResults(data, isQueryOnly) {
  if (!Array.isArray(data) || data.length === 0) {
    results.innerHTML = `<div class="summary">No results.</div>`;
    return;
  }

  if (isQueryOnly) {
    const totalQueries = data.length;
    const totalResults = data.reduce((sum, r) => sum + (r.hits?.length || 0), 0);
    const avgResults = totalQueries > 0 ? (totalResults / totalQueries).toFixed(1) : 0;

    results.innerHTML = `
      <div class="summary">
        <div><strong>Total queries:</strong> ${totalQueries}</div>
        <div><strong>Total results:</strong> ${totalResults}</div>
        <div><strong>Average per query:</strong> ${avgResults}</div>
      </div>
    `;
  } else {
    const total = data.length;
    const ranks = data.map(r => r.matched_rank).filter(rk => typeof rk === 'number' && rk > 0);
    const buckets = [1, 3, 5, 10, 20, 30, 40, 50];
    const lines = buckets.map(b => {
      const count = ranks.filter(rk => rk <= b).length;
      return `Found at rank 1-${b}: ${count}`;
    });
    const avg = ranks.length ? (ranks.reduce((a, b) => a + b, 0) / ranks.length).toFixed(2) : 'N/A';
    const matchedCount = ranks.length;

    results.innerHTML = `
      <div class="summary">
        <div><strong>Total queries:</strong> ${total}</div>
        <div><strong>Matched:</strong> ${matchedCount} / ${total}</div>
        <pre>${lines.map(l => '   ' + l).join('\n')}\n   Average rank: ${avg}</pre>
      </div>
    `;
  }
}

// ============================================
// Navigation Event Handlers
// ============================================

// Step 0 -> 1
startBtn.addEventListener('click', () => goToStep(1));

// Step 1 navigation
back1.addEventListener('click', () => goToStep(0));
next1.addEventListener('click', () => {
  updateCurlStep();
  goToStep(2);
});

// Step 2 navigation
back2.addEventListener('click', () => goToStep(1));
next2.addEventListener('click', () => goToStep(3));

// Step 3 navigation
back3.addEventListener('click', () => goToStep(2));
next3.addEventListener('click', () => goToStep(4));

// Step 4 navigation
back4.addEventListener('click', () => goToStep(3));
next4.addEventListener('click', () => {
  setupConfigStep();
  goToStep(5);
});

// Step 5 navigation
back5.addEventListener('click', () => goToStep(4));
runAnalysisBtn.addEventListener('click', runAnalysis);

// Step 6 - Start over
startOver.addEventListener('click', () => {
  // Reset state
  currentCsv = { headers: [], rows: [] };
  csvFile.value = '';
  curlInput.value = '';
  communityCurlInput.value = '';
  pageTypeInput.value = 'normal';
  analysisModeInput.value = 'with_expected';

  // Reset UI
  choiceNormal.classList.remove('selected');
  choiceCommunity.classList.remove('selected');
  choiceWithExpected.classList.add('selected');
  choiceQueryOnly.classList.remove('selected');
  setHidden(uploadZone, false);
  setHidden(fileInfo, true);
  next1.disabled = true;
  next2.disabled = true;
  next4.disabled = true;
  validation.innerHTML = '';

  goToStep(0);
});

// ============================================
// LLM Comparator Prompt
// ============================================

const llmPromptEl = document.getElementById('llmComparatorPrompt');
const llmPromptYes = document.getElementById('llmPromptYes');
const llmPromptNo = document.getElementById('llmPromptNo');

function showLlmPrompt() {
  if (llmPromptEl) {
    setHidden(llmPromptEl, false);
    llmPromptEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function dismissLlmPrompt() {
  if (llmPromptEl) setHidden(llmPromptEl, true);
}

if (llmPromptYes) {
  llmPromptYes.addEventListener('click', async () => {
    let llmUrl = '/';
    try {
      const r = await fetch('/api/config');
      const cfg = await r.json();
      if (cfg.llm_comparator_url) llmUrl = cfg.llm_comparator_url.replace(/\/$/, '') + '/';
    } catch (e) { /* fallback */ }
    window.open(llmUrl + '?from=rs', '_blank');
  });
}

if (llmPromptNo) {
  llmPromptNo.addEventListener('click', () => {
    dismissLlmPrompt();
  });
}

// ============================================
// Initialize
// ============================================

goToStep(0);
