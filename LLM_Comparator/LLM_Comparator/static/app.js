// Detect mode from URL params
const _urlParams = new URLSearchParams(window.location.search);
const _fromParam = _urlParams.get("from");
const isDqeMode = _fromParam === "dqe";
const isRsMode = _fromParam === "rs";

// Back-to-app navigation
(function initBackButton() {
  const backBtn = document.getElementById("backToApp");
  const backText = document.getElementById("backToAppText");
  if (_fromParam && backBtn && backText) {
    const apps = {
      dqe: { label: "Back to Data Query Engine", url: "/relevancy-framework/dataquery/" },
      rs: { label: "Back to Portal", url: "/relevancy-framework/" },
    };
    const app = apps[_fromParam];
    if (app) {
      backBtn.href = "#";
      backText.textContent = app.label;
      backBtn.classList.remove("hidden");
      backBtn.addEventListener("click", function (e) {
        e.preventDefault();
        window.close();
        setTimeout(function () { window.location.href = app.url; }, 300);
      });
    }
  }
})();

const form = document.getElementById("compare-form");
const statusEl = document.getElementById("status");
const progressPanel = document.querySelector(".progress-panel");
const progressText = document.getElementById("progress-text");
const progressCount = document.getElementById("progress-count");
const progressFill = document.getElementById("progress-fill");
const suTestBtn = document.getElementById("su-test-btn");
const suTestStatus = document.getElementById("su-test-status");
const suTestResults = document.getElementById("su-test-results");
const showLogsBtn = document.getElementById("show-logs-btn");
const logsModal = document.getElementById("logs-modal");
const logsContent = document.getElementById("logs-content");
const copyLogsBtn = document.getElementById("copy-logs-btn");
const clearLogsBtn = document.getElementById("clear-logs-btn");
const closeModalBtn = document.querySelector(".close-modal");
const resultsArea = document.getElementById("results-area");
const previewBtn = document.getElementById("preview-btn");
const hidePreviewBtn = document.getElementById("hide-preview-btn");
const downloadBtn = document.getElementById("download-btn");
const previewContainer = document.getElementById("preview-container");
const previewHead = document.getElementById("preview-head");
const previewBody = document.getElementById("preview-body");

// Usage Dashboard Elements
const usageDashboard = document.getElementById("usage-dashboard");
const usageStatusBadge = document.getElementById("usage-status-badge");
const usageStatusText = document.getElementById("usage-status-text");
const dailyUsageCount = document.getElementById("daily-usage-count");
const dailyUsageFill = document.getElementById("daily-usage-fill");
const dailyResetTime = document.getElementById("daily-reset-time");
const monthlyUsageCount = document.getElementById("monthly-usage-count");
const monthlyUsageFill = document.getElementById("monthly-usage-fill");
const monthlyResetTime = document.getElementById("monthly-reset-time");
const runsCount = document.getElementById("runs-count");
const cooldownInfo = document.getElementById("cooldown-info");
const cooldownTimer = document.getElementById("cooldown-timer");
const limitWarning = document.getElementById("limit-warning");
const limitWarningText = document.getElementById("limit-warning-text");

// New UI Elements
const hasGroundTruthRadios = document.getElementsByName("hasGroundTruth");
const runGoogleBenchmarkCheckbox = document.querySelector("input[name='runGoogleBenchmark']");
const uploadTitle = document.getElementById("upload-title");
const requiredColumns = document.getElementById("required-columns");
const optionalityRule = document.getElementById("optionality-rule");
const uploadLabelText = document.getElementById("upload-label-text");
const uploadNote = document.getElementById("upload-note");
const googleSection = document.getElementById("google-section");
const googleInputs = googleSection ? googleSection.querySelectorAll('input[name="googleApiKey"], input[name="googleCseId"], input[name="googleSites"]') : [];
const llmPromptInput = form.elements["llmPrompt"];

// Method Selection Elements
const methodCards = document.querySelectorAll('.method-card');
const googleMethodInput = document.getElementById('googleMethod');
const apiConfig = document.getElementById('api-config');
const seleniumConfig = document.getElementById('selenium-config');
const bypassConfig = document.getElementById('bypass-config');

// Preview Elements
const filePreviewContainer = document.getElementById("file-preview-container");
const filePreviewTable = document.getElementById("file-preview-table");
const previewStatusBadge = document.getElementById("preview-status-badge");
const previewErrorMsg = document.getElementById("preview-error-msg");
const previewRowCount = document.getElementById("preview-row-count");
const fileInput = document.querySelector('input[name="csvFile"]');
const step2NextBtn = document.querySelector('#step-2 .next-btn');

let isFileValid = false;

const PROMPTS = {
  full: `You are a search relevancy evaluator. Given a search query, an expected document title (the ideal answer), and a list of search result titles, score how well the search results satisfy the user's intent.

Evaluation Criteria:
1. Title Relevance (0-10): How well do the result titles match the query intent? Do the titles indicate content that would answer the user's question?
2. Expected Match: Does any result title closely match or relate to the expected document title? If yes, that's a strong positive signal.
3. Ranking Quality: Are the most relevant titles ranked higher (rank 1-2)?
4. Irrelevance: If the titles are completely unrelated to the query, score low (0-3).

Respond in JSON with keys: score (0-10), reason.`,

  no_gt: `You are a search relevancy evaluator. Given a search query and a list of search result titles, score how well the results satisfy the user's intent.

Evaluation Criteria:
1. Title Relevance (0-10): How well do the result titles match the query intent?
2. Quality: Are the titles specific and authoritative?
3. Irrelevance: If the titles are unrelated, score low (0-3).

Respond in JSON with keys: score (0-10), reason.`,

  no_google: `You are a search relevancy evaluator. Given a search query, an expected document title, and a list of search result titles, score how well the results match what the user is looking for.

Evaluation Criteria:
1. Title Relevance (0-10): How well do the result titles match the query intent?
2. Expected Match: Does any result title closely match the expected document title?
3. Irrelevance: If the titles are unrelated, score low (0-3).

Respond in JSON with keys: score (0-10), reason.`,

  minimal: `You are a search relevancy evaluator. Given a search query and a list of search result titles, score the relevance.

Evaluation Criteria:
1. Relevance: Do the result titles directly answer the query?
2. Irrelevance: If the titles are unrelated, score low (0-3).

Respond in JSON with keys: score (0-10), reason.`
};

let isPromptManuallyEdited = false;
let progressTimer = null;
let logsTimer = null;
let currentZipContent = null;
let currentFilename = null;
let currentPreviewData = null;

// Multi-step Logic
let currentStep = 1;
const totalSteps = 5;

// DQE Mode: modify UI when launched from Data Query Engine
if (isDqeMode) {
  // Hide Ground Truth radio group (always "yes" in DQE mode)
  const gtRadioGroup = document.querySelector('.radio-group');
  if (gtRadioGroup) gtRadioGroup.style.display = 'none';
  const gtYesRadio = document.querySelector('input[name="hasGroundTruth"][value="yes"]');
  if (gtYesRadio) gtYesRadio.checked = true;

  // Update Step 2 labels
  if (uploadTitle) uploadTitle.textContent = 'Upload Search Result Sheet';
  if (requiredColumns) requiredColumns.textContent = 'Columns will be selected from dropdown after upload';
  if (optionalityRule) optionalityRule.style.display = 'none';
  if (uploadLabelText) uploadLabelText.textContent = 'Upload DQE Search Evaluation Sheet';
  if (uploadNote) uploadNote.textContent = 'Upload the search evaluation output from Data Query Engine (CSV .csv or Excel .xlsx / .xlsm).';

  // Hide Step 3 (SearchUnify) indicator and its connector from stepper
  const stepIndicators = document.querySelectorAll('.step-indicator');
  stepIndicators.forEach(ind => {
    if (parseInt(ind.dataset.step) === 3) {
      ind.style.display = 'none';
      // Also hide the connector before step 3
      const prev = ind.previousElementSibling;
      if (prev && prev.classList.contains('step-connector')) {
        prev.style.display = 'none';
      }
    }
  });

  // Remove required from suCurl since it won't be used
  const suCurlEl = form.elements["suCurl"];
  if (suCurlEl) suCurlEl.removeAttribute('required');
}

// RS Mode: modify UI when launched from Relevancy Script
if (isRsMode) {
  // Update Step 2 labels for RS mode
  if (uploadTitle) uploadTitle.textContent = 'Upload Search Evaluation Sheet';
  if (requiredColumns) requiredColumns.textContent = 'Columns will be selected from dropdown after upload';
  if (optionalityRule) optionalityRule.style.display = 'none';
  if (uploadLabelText) uploadLabelText.textContent = 'Upload Relevancy Script Output';
  if (uploadNote) uploadNote.textContent = 'Upload the search evaluation output from Relevancy Script (CSV .csv or Excel .xlsx / .xlsm).';

  // Hide Step 3 (SearchUnify) indicator and its connector from stepper
  const stepIndicators = document.querySelectorAll('.step-indicator');
  stepIndicators.forEach(ind => {
    if (parseInt(ind.dataset.step) === 3) {
      ind.style.display = 'none';
      const prev = ind.previousElementSibling;
      if (prev && prev.classList.contains('step-connector')) {
        prev.style.display = 'none';
      }
    }
  });

  // Remove required from suCurl since it won't be used
  const suCurlEl = form.elements["suCurl"];
  if (suCurlEl) suCurlEl.removeAttribute('required');
}

// RS Column Mapping Elements
const rsColumnMapping = document.getElementById('rs-column-mapping');
const rsGtColumns = document.getElementById('rs-gt-columns');
const rsQueryCol = document.getElementById('rsQueryCol');
const rsResultTitleCol = document.getElementById('rsResultTitleCol');
const rsResultUrlCol = document.getElementById('rsResultUrlCol');
const rsResultRankCol = document.getElementById('rsResultRankCol');
const rsExpectedTitleCol = document.getElementById('rsExpectedTitleCol');
const rsExpectedUrlCol = document.getElementById('rsExpectedUrlCol');

function populateRsColumnDropdowns(columns) {
  const selects = [rsQueryCol, rsResultTitleCol, rsResultUrlCol, rsResultRankCol, rsExpectedTitleCol, rsExpectedUrlCol];
  const optionalSelects = [rsResultUrlCol, rsResultRankCol, rsExpectedTitleCol, rsExpectedUrlCol];

  selects.forEach(sel => {
    if (!sel) return;
    sel.innerHTML = '';
    if (optionalSelects.includes(sel)) {
      const noneOpt = document.createElement('option');
      noneOpt.value = '';
      noneOpt.textContent = '(none)';
      sel.appendChild(noneOpt);
    }
    columns.forEach(col => {
      const opt = document.createElement('option');
      opt.value = col.normalized;
      opt.textContent = col.original;
      sel.appendChild(opt);
    });
  });

  // Auto-select common column names
  const queryCands = ['keyword', 'query', 'generated_query', 'search_query', 'q'];
  const resultTitleCands = ['result_title', 'title'];
  const resultUrlCands = ['result_url', 'url', 'href'];
  const resultRankCands = ['result_rank', 'rank'];
  const expectedTitleCands = ['expected_title', 'original_title'];
  const expectedUrlCands = ['expected_url', 'original_url'];

  function autoSelect(selectEl, candidates) {
    if (!selectEl) return;
    const norms = columns.map(c => c.normalized);
    for (const cand of candidates) {
      const idx = norms.indexOf(cand);
      if (idx >= 0) {
        selectEl.value = columns[idx].normalized;
        return;
      }
    }
  }

  autoSelect(rsQueryCol, queryCands);
  autoSelect(rsResultTitleCol, resultTitleCands);
  autoSelect(rsResultUrlCol, resultUrlCands);
  autoSelect(rsResultRankCol, resultRankCands);
  autoSelect(rsExpectedTitleCol, expectedTitleCands);
  autoSelect(rsExpectedUrlCol, expectedUrlCands);
}

function updateRsGtColumnsVisibility() {
  if ((!isRsMode && !isDqeMode) || !rsGtColumns) return;
  if (isDqeMode) {
    rsGtColumns.style.display = 'grid';
    return;
  }
  const hasGT = document.querySelector('input[name="hasGroundTruth"]:checked').value === "yes";
  rsGtColumns.style.display = hasGT ? 'grid' : 'none';
}

// ============================================================================
// Method Card Selection Logic
// ============================================================================

function selectMethodCard(method) {
  // Update card selection UI
  methodCards.forEach(card => {
    if (card.dataset.method === method) {
      card.classList.add('selected');
    } else {
      card.classList.remove('selected');
    }
  });

  // Update hidden input
  if (googleMethodInput) {
    googleMethodInput.value = method;
  }

  // Show/hide relevant config sections
  if (apiConfig) apiConfig.style.display = method === 'api' ? 'block' : 'none';
  if (seleniumConfig) seleniumConfig.style.display = method === 'selenium' ? 'block' : 'none';
  if (bypassConfig) bypassConfig.style.display = method === 'bypass' ? 'block' : 'none';

  // Update required fields
  const apiKeyInput = form.elements["googleApiKey"];
  const cseIdInput = form.elements["googleCseId"];

  if (method === 'api') {
    if (apiKeyInput) apiKeyInput.setAttribute('required', '');
    if (cseIdInput) cseIdInput.setAttribute('required', '');
  } else {
    if (apiKeyInput) apiKeyInput.removeAttribute('required');
    if (cseIdInput) cseIdInput.removeAttribute('required');
  }
}

// Attach click handlers to method cards
methodCards.forEach(card => {
  card.addEventListener('click', () => {
    const method = card.dataset.method;
    selectMethodCard(method);
  });
});

function showStep(step) {
  // Hide all steps
  document.querySelectorAll('.step-card').forEach(el => el.classList.remove('active'));
  // Show current step
  const currentStepEl = document.querySelector(`.step-card[data-step="${step}"]`);
  if (currentStepEl) currentStepEl.classList.add('active');

  // Update Stepper
  document.querySelectorAll('.step-indicator').forEach(el => {
    const stepNum = parseInt(el.dataset.step);
    el.classList.remove('active', 'completed');
    if (stepNum === step) {
      el.classList.add('active');
    } else if (stepNum < step) {
      el.classList.add('completed');
    }
  });

  // Scroll to top of form container
  const container = document.querySelector('.container');
  if (container) {
    container.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

function nextStep() {
  const currentStepEl = document.querySelector(`.step-card[data-step="${currentStep}"]`);

  // Validate current step
  const inputs = currentStepEl.querySelectorAll('input[required], textarea[required], select[required]');

  let valid = true;
  inputs.forEach(input => {
    if (!input.value.trim()) {
      setFieldError(input.name, "This field is required.");
      valid = false;
    } else {
      setFieldError(input.name, "");
    }
  });

  // Special validation for Step 2 (File)
  if (currentStep === 2) {
    if (!fileInput.files.length) {
      setFieldError("csvFile", "File is required.");
      valid = false;
    } else if (!isFileValid) {
      setFieldError("csvFile", "Please upload a valid file to proceed.");
      valid = false;
    }
  }

  // Special validation for Step 4 (Google Benchmark)
  if (currentStep === 4) {
    const runGoogle = document.querySelector('input[name="runGoogleBenchmark"]').checked;
    const googleMethod = googleMethodInput ? googleMethodInput.value : 'api';

    if (runGoogle && googleMethod === 'api') {
      // Need API credentials for API method
      const apiKey = form.elements["googleApiKey"].value.trim();
      const cseId = form.elements["googleCseId"].value.trim();

      if (!apiKey) {
        setFieldError("googleApiKey", "Google API key is required for API method.");
        valid = false;
      }
      if (!cseId) {
        setFieldError("googleCseId", "CSE ID is required for API method.");
        valid = false;
      }
    }
    // No validation needed for selenium or bypass methods
  }

  if (!valid) {
    showNotification("Please fix the errors before proceeding.", "error");
    return;
  }

  let next = currentStep + 1;
  // DQE/RS mode: skip step 3 (SearchUnify config)
  if (next === 3 && (isDqeMode || isRsMode)) {
    next = 4;
  }
  // Skip step 4 if Google benchmark is disabled
  const runGoogle = document.querySelector('input[name="runGoogleBenchmark"]').checked;
  if (next === 4 && !runGoogle) {
    next = 5;
  }

  currentStep = next;
  showStep(currentStep);
}

function prevStep() {
  let prev = currentStep - 1;
  const runGoogle = document.querySelector('input[name="runGoogleBenchmark"]').checked;
  if (prev === 4 && !runGoogle) {
    prev = 3;
  }
  // DQE/RS mode: skip step 3 (SearchUnify config)
  if (prev === 3 && (isDqeMode || isRsMode)) {
    prev = 2;
  }

  if (prev < 1) prev = 1;
  currentStep = prev;
  showStep(currentStep);
}

// Attach listeners for nav buttons
document.querySelectorAll('.next-btn').forEach(btn => {
  btn.addEventListener('click', nextStep);
});

document.querySelectorAll('.prev-btn').forEach(btn => {
  btn.addEventListener('click', prevStep);
});


function setStatus(message, isError = false) {
  if (message) {
    statusEl.style.display = "block";
    statusEl.textContent = message;
    statusEl.classList.toggle("error", isError);
  } else {
    statusEl.style.display = "none";
  }
}

function setFieldError(fieldName, message) {
  const errorEl = document.querySelector(`[data-error-for="${fieldName}"]`);
  const inputEl = document.querySelector(`[name="${fieldName}"]`);
  if (errorEl) {
    errorEl.textContent = message || "";
  }
  if (inputEl) {
    inputEl.classList.toggle("invalid", Boolean(message));
  }
}

function clearFieldErrors() {
  document.querySelectorAll(".field-error").forEach((el) => {
    el.textContent = "";
  });
  document.querySelectorAll(".invalid").forEach((el) => {
    el.classList.remove("invalid");
  });
}

function setSuTestStatus(message, isError = false) {
  if (!suTestStatus) return;
  suTestStatus.textContent = message || "";
  suTestStatus.classList.toggle("error", isError);
}

function renderSuResults(results = []) {
  if (!suTestResults) return;
  suTestResults.innerHTML = "";
  if (!results.length) {
    suTestResults.textContent = "No results returned.";
    return;
  }
  results.forEach((item) => {
    const card = document.createElement("div");
    card.className = "su-test-item";

    const title = document.createElement("div");
    title.className = "su-test-item-title";
    title.textContent = item.title || "(Untitled)";

    const link = document.createElement("a");
    link.className = "su-test-item-link";
    link.href = item.url || "#";
    link.textContent = item.url || "No URL provided";
    link.target = "_blank";
    link.rel = "noreferrer";

    card.appendChild(title);
    card.appendChild(link);
    suTestResults.appendChild(card);
  });
}

function validateRequired(fields) {
  let valid = true;
  fields.forEach(({ name, value, message }) => {
    if (!value) {
      setFieldError(name, message);
      valid = false;
    }
  });
  return valid;
}

function updateProgressUI(data) {
  if (!data) return;
  const total = data.total || 0;
  const current = data.current || 0;
  const stage = data.stage || "";
  const percent = total ? Math.min(100, Math.round((current / total) * 100)) : 0;

  progressText.textContent = stage || "Processing...";
  progressCount.textContent = total ? `${current} / ${total}` : "";
  progressFill.style.width = `${percent}%`;
  progressPanel.setAttribute("aria-hidden", "false");
}

async function pollProgress() {
  try {
    const resp = await fetch("api/progress");
    if (!resp.ok) return;
    const data = await resp.json();
    updateProgressUI(data);
    if (data.status === "completed" || data.status === "error") {
      if (data.status === "completed") {
        progressText.textContent = "Completed";
        progressFill.style.width = "100%";
      }
      clearInterval(progressTimer);
      progressTimer = null;
      // Stop logs polling when done
      if (logsTimer) {
        clearInterval(logsTimer);
        logsTimer = null;
      }
    }
  } catch (error) {
    clearInterval(progressTimer);
    progressTimer = null;
  }
}

function updatePrompt() {
  if (isPromptManuallyEdited) return;

  const hasGT = document.querySelector('input[name="hasGroundTruth"]:checked').value === "yes";
  const runGoogle = document.querySelector('input[name="runGoogleBenchmark"]').checked;

  if (hasGT && runGoogle) {
    llmPromptInput.value = PROMPTS.full;
  } else if (!hasGT && runGoogle) {
    llmPromptInput.value = PROMPTS.no_gt;
  } else if (hasGT && !runGoogle) {
    llmPromptInput.value = PROMPTS.no_google;
  } else {
    llmPromptInput.value = PROMPTS.minimal;
  }
}

llmPromptInput.addEventListener("input", () => {
  isPromptManuallyEdited = true;
});

const resetPromptBtn = document.getElementById("reset-prompt-btn");
if (resetPromptBtn) {
  resetPromptBtn.addEventListener("click", () => {
    isPromptManuallyEdited = false;
    updatePrompt();
  });
}

// File Upload Preview Logic
if (fileInput) {
  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) {
      filePreviewContainer.style.display = "none";
      isFileValid = false;
      return;
    }

    // Reset UI
    filePreviewContainer.style.display = "block";
    previewStatusBadge.textContent = "Validating...";
    previewStatusBadge.style.background = "#4a5568";
    previewErrorMsg.style.display = "none";
    previewErrorMsg.textContent = "";
    filePreviewTable.style.display = "none";
    previewRowCount.textContent = "";
    step2NextBtn.disabled = true;
    isFileValid = false;
    setFieldError("csvFile", "");

    const formData = new FormData();
    formData.append("file", file);
    const hasGroundTruth = document.querySelector('input[name="hasGroundTruth"]:checked').value;
    formData.append("has_ground_truth", hasGroundTruth);
    if (isDqeMode) formData.append("mode", "dqe");
    if (isRsMode) formData.append("mode", "rs");

    try {
      const resp = await fetch("api/preview-upload", {
        method: "POST",
        body: formData
      });

      const data = await resp.json();

      if (data.valid) {
        // Success
        isFileValid = true;
        step2NextBtn.disabled = false;

        previewStatusBadge.textContent = "Valid";
        previewStatusBadge.style.background = "#48bb78";

        // Render Table
        filePreviewTable.style.display = "table";
        const thead = filePreviewTable.querySelector("thead");
        const tbody = filePreviewTable.querySelector("tbody");
        thead.innerHTML = "";
        tbody.innerHTML = "";

        if (data.preview && data.preview.length > 0) {
          // Get keys from first row
          const keys = Object.keys(data.preview[0]);

          // Header
          const trHead = document.createElement("tr");
          keys.forEach(k => {
            const th = document.createElement("th");
            th.textContent = k;
            trHead.appendChild(th);
          });
          thead.appendChild(trHead);

          // Body
          data.preview.forEach(row => {
            const tr = document.createElement("tr");
            keys.forEach(k => {
              const td = document.createElement("td");
              td.textContent = row[k];
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
          });
        }

        previewRowCount.textContent = `Showing first ${data.preview.length} of ${data.total_rows} rows.`;

        // DQE/RS mode: populate column mapping dropdowns
        if ((isRsMode || isDqeMode) && data.columns) {
          populateRsColumnDropdowns(data.columns);
          if (rsColumnMapping) rsColumnMapping.style.display = 'block';
          updateRsGtColumnsVisibility();
        }

      } else {
        // Error
        isFileValid = false;
        step2NextBtn.disabled = true; // Keep disabled

        previewStatusBadge.textContent = "Invalid";
        previewStatusBadge.style.background = "#e53e3e";

        previewErrorMsg.textContent = data.error || "Unknown validation error.";
        previewErrorMsg.style.display = "block";

        // DQE/RS mode: hide column mapping on error
        if ((isRsMode || isDqeMode) && rsColumnMapping) rsColumnMapping.style.display = 'none';
      }
    } catch (err) {
      console.error(err);
      isFileValid = false;
      previewStatusBadge.textContent = "Error";
      previewStatusBadge.style.background = "#e53e3e";
      previewErrorMsg.textContent = "Failed to validate file. Check server logs.";
      previewErrorMsg.style.display = "block";
      if ((isRsMode || isDqeMode) && rsColumnMapping) rsColumnMapping.style.display = 'none';
    }
  });
}

// Re-validate if ground truth option changes while file is selected
hasGroundTruthRadios.forEach(radio => {
  radio.addEventListener("change", (e) => {
    // RS mode: update GT column visibility
    if (isRsMode) {
      updateRsGtColumnsVisibility();
    }

    // Trigger validation if file is present
    if (fileInput && fileInput.files.length > 0) {
      fileInput.dispatchEvent(new Event('change'));
    }
  });
});

// UI Event Listeners
hasGroundTruthRadios.forEach(radio => {
  radio.addEventListener("change", (e) => {
    const hasGT = e.target.value === "yes";
    if (isRsMode) {
      // RS mode: keep RS-specific labels, just update GT column visibility
      updateRsGtColumnsVisibility();
    } else if (hasGT) {
      uploadTitle.textContent = "Upload Ground Truth Data";
      requiredColumns.textContent = "Query, Expected Title, Expected URL";
      optionalityRule.style.display = "block";
      uploadLabelText.textContent = "Upload CSV or Excel";
      uploadNote.textContent = "Provide Expected Title or Expected URL for every row.";
    } else {
      uploadTitle.textContent = "Upload Queries";
      requiredColumns.textContent = "Query";
      optionalityRule.style.display = "none";
      uploadLabelText.textContent = "Upload CSV, Excel, or TXT";
      uploadNote.textContent = "File should contain a 'Query' column or be a list of queries.";
    }
    updatePrompt();
  });
});

runGoogleBenchmarkCheckbox.addEventListener("change", (e) => {
  updatePrompt();
});

// Notification System
let activeNotification = null;
let notificationTimer = null;
let notificationStartTime = 0;
const NOTIFICATION_DURATION = 5000;

function showNotification(message, type = 'error') {
  const container = document.getElementById('toast-container');

  // If active notification exists, reset timer
  if (activeNotification) {
    clearTimeout(notificationTimer);
    activeNotification.remove();
  }

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  toast.innerHTML = `
        <div class="toast-header">
            <div class="toast-message">${message}</div>
            <div class="toast-close">&times;</div>
        </div>
        <div class="toast-progress">
            <div class="toast-progress-bar"></div>
        </div>
    `;

  container.appendChild(toast);
  activeNotification = toast;

  // Close button
  toast.querySelector('.toast-close').addEventListener('click', () => {
    clearTimeout(notificationTimer);
    toast.remove();
    activeNotification = null;
  });

  // Progress Bar Animation
  const progressBar = toast.querySelector('.toast-progress-bar');
  progressBar.style.transition = `width ${NOTIFICATION_DURATION}ms linear`;

  // Force reflow
  progressBar.getBoundingClientRect();
  progressBar.style.width = '0%';

  notificationTimer = setTimeout(() => {
    toast.remove();
    activeNotification = null;
  }, NOTIFICATION_DURATION);
}

// Logs System
let previousLogCount = 0;
async function fetchLogs() {
  try {
    const resp = await fetch("api/logs?limit=1000");
    if (resp.ok) {
      const data = await resp.json();
      const modalBody = document.querySelector('.modal-body');

      // Check if user is at bottom before updating
      const isAtBottom = modalBody &&
        (modalBody.scrollHeight - modalBody.scrollTop - modalBody.clientHeight < 50);

      // Only clear and rebuild if log count changed
      if (data.logs.length !== previousLogCount) {
        // Clear current logs
        logsContent.innerHTML = '';

        data.logs.forEach(logLine => {
          const div = document.createElement('div');
          div.className = 'log-entry';

          // Determine type - check in order: error, warning, success, then default to info
          const upperLine = logLine.toUpperCase();
          if (upperLine.includes('ERROR') || upperLine.includes('EXCEPTION') || upperLine.includes('FAILED')) {
            div.classList.add('error');
          } else if (upperLine.includes('WARNING') || upperLine.includes('WARN')) {
            div.classList.add('warning');
          } else if (upperLine.includes('SUCCESS') || upperLine.includes('SUCCESSFULLY') || upperLine.includes('COMPLETED')) {
            div.classList.add('success');
          } else {
            div.classList.add('info');
          }

          div.textContent = logLine;
          logsContent.appendChild(div);
        });

        previousLogCount = data.logs.length;

        // Auto scroll to bottom only if user was at bottom or is untouched
        if (isAtBottom && modalBody) {
          modalBody.scrollTop = modalBody.scrollHeight;
        }
      }
    }
  } catch (e) {
    console.error("Failed to fetch logs", e);
  }
}

if (copyLogsBtn) {
  copyLogsBtn.addEventListener('click', () => {
    const text = Array.from(logsContent.children)
      .map(div => div.textContent)
      .join('\n');

    navigator.clipboard.writeText(text).then(() => {
      const originalText = copyLogsBtn.textContent;
      copyLogsBtn.textContent = "Copied!";
      setTimeout(() => {
        copyLogsBtn.textContent = originalText;
      }, 2000);
    }).catch(err => {
      console.error('Failed to copy logs:', err);
      showNotification("Failed to copy logs", "error");
    });
  });
}

if (clearLogsBtn) {
  clearLogsBtn.addEventListener('click', async () => {
    try {
      const resp = await fetch("api/logs", { method: "DELETE" });
      if (resp.ok) {
        logsContent.innerHTML = '';
        previousLogCount = 0; // Reset log count when clearing
        showNotification("Logs cleared.", "success");
      } else {
        showNotification("Failed to clear logs on server.", "error");
      }
    } catch (e) {
      console.error("Failed to clear logs", e);
      showNotification("Error clearing logs.", "error");
    }
  });
}

showLogsBtn.addEventListener('click', () => {
  logsModal.style.display = "block";
  previousLogCount = 0; // Reset log count when opening modal
  fetchLogs();
  if (!logsTimer) {
    logsTimer = setInterval(fetchLogs, 1000);
  }
});

closeModalBtn.addEventListener('click', () => {
  logsModal.style.display = "none";
  if (logsTimer) {
    clearInterval(logsTimer);
    logsTimer = null;
  }
});

window.addEventListener('click', (event) => {
  if (event.target == logsModal) {
    logsModal.style.display = "none";
    if (logsTimer) {
      clearInterval(logsTimer);
      logsTimer = null;
    }
  }
});

// Results Preview
previewBtn.addEventListener('click', () => {
  if (!currentPreviewData) return;

  previewContainer.style.display = "block";
  previewBtn.style.display = "none";
  if (hidePreviewBtn) hidePreviewBtn.style.display = "inline-block";

  previewHead.innerHTML = "";
  previewBody.innerHTML = "";

  // Header
  const trHead = document.createElement('tr');
  currentPreviewData.header.forEach(h => {
    const th = document.createElement('th');
    th.textContent = h;
    trHead.appendChild(th);
  });
  previewHead.appendChild(trHead);

  // Body
  currentPreviewData.rows.forEach(row => {
    const tr = document.createElement('tr');
    row.forEach(cell => {
      const td = document.createElement('td');
      td.textContent = cell;
      tr.appendChild(td);
    });
    previewBody.appendChild(tr);
  });

  // Scroll to preview
  previewContainer.scrollIntoView({ behavior: 'smooth' });
});

if (hidePreviewBtn) {
  hidePreviewBtn.addEventListener('click', () => {
    previewContainer.style.display = "none";
    hidePreviewBtn.style.display = "none";
    previewBtn.style.display = "inline-block";
  });
}

downloadBtn.addEventListener('click', () => {
  if (!currentZipContent || !currentFilename) return;

  // Convert base64 to blob
  const byteCharacters = atob(currentZipContent);
  const byteNumbers = new Array(byteCharacters.length);
  for (let i = 0; i < byteCharacters.length; i++) {
    byteNumbers[i] = byteCharacters.charCodeAt(i);
  }
  const byteArray = new Uint8Array(byteNumbers);
  const blob = new Blob([byteArray], { type: "application/zip" });

  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = currentFilename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearFieldErrors();

  // Check rate limits before proceeding (only if Google benchmark is enabled with API or Selenium)
  const runGoogleBenchmark = document.querySelector('input[name="runGoogleBenchmark"]').checked;
  const googleMethod = googleMethodInput ? googleMethodInput.value : 'api';

  if (runGoogleBenchmark && (googleMethod === 'api' || googleMethod === 'selenium')) {
    const canProceed = await checkRateLimitBeforeSubmit();
    if (!canProceed) {
      return; // Stop submission if rate limit is hit
    }
  }

  // Clear logs before each run
  try {
    await fetch("api/logs", { method: "DELETE" });
    if (logsContent) logsContent.innerHTML = '';
    previousLogCount = 0; // Reset log count
  } catch (e) {
    console.error("Failed to clear logs:", e);
  }

  setStatus("Processing...", false);
  resultsArea.style.display = "none";
  previewContainer.style.display = "none";

  // Reset preview buttons
  previewBtn.style.display = "inline-block";
  if (hidePreviewBtn) hidePreviewBtn.style.display = "none";

  const formData = new FormData();
  const csvFile = form.elements["csvFile"].files[0];
  const suCurl = form.elements["suCurl"].value.trim();
  const suTitlePath = form.elements["suTitlePath"].value.trim();
  const suUrlPath = form.elements["suUrlPath"].value.trim();
  const suMaxResults = Number(form.elements["suMaxResults"].value || 10);

  const hasGroundTruth = document.querySelector('input[name="hasGroundTruth"]:checked').value === "yes";

  // googleMethod already declared above for rate limit check
  const googleApiKey = form.elements["googleApiKey"].value.trim();
  const googleCseId = form.elements["googleCseId"].value.trim();
  const googleSites = form.elements["googleSites"].value.trim();
  const seleniumSite = form.elements["seleniumSite"] ? form.elements["seleniumSite"].value.trim() : "";

  const llmApiKey = form.elements["llmApiKey"].value.trim();
  const llmModel = form.elements["llmModel"].value;
  const llmPrompt = form.elements["llmPrompt"].value.trim();
  const llmProvider = llmModel.startsWith("gemini") ? "gemini" : "openai";

  const requiredFields = [
    { name: "csvFile", value: csvFile, message: "File is required." },
  ];

  // SU cURL is only required in normal mode (not DQE or RS)
  if (!isDqeMode && !isRsMode) {
    requiredFields.push(
      { name: "suCurl", value: suCurl, message: "SearchUnify cURL is required." }
    );
  }

  if (runGoogleBenchmark && googleMethod === 'api') {
    requiredFields.push(
      { name: "googleApiKey", value: googleApiKey, message: "Google API key is required." },
      { name: "googleCseId", value: googleCseId, message: "CSE ID is required." }
    );
  }

  const requiredValid = validateRequired(requiredFields);

  if (!requiredValid) {
    showNotification("Please fix the highlighted fields.", "error");
    return;
  }

  const config = {
    has_ground_truth: hasGroundTruth,
    run_google_benchmark: runGoogleBenchmark,
    mode: isDqeMode ? "dqe" : isRsMode ? "rs" : "normal",
    searchunify: {
      curl: (isDqeMode || isRsMode) ? "" : suCurl,
      title_path: suTitlePath || "$.result.hits[*].highlight.TitleToDisplayString",
      url_path: suUrlPath || "$.result.hits[*].href",
      max_results: suMaxResults,
    },
    google: {
      method: googleMethod,
      api_key: googleApiKey,
      cse_id: googleCseId,
      sites: googleSites
        ? googleSites.split(",").map((site) => site.trim()).filter(Boolean)
        : [],
      selenium_site: seleniumSite,
      max_results: 10,
    },
    llm: {
      enabled: Boolean(llmApiKey),
      api_key: llmApiKey,
      model: llmModel || "gpt-4o-mini",
      provider: llmProvider,
      prompt: llmPrompt,
    },
  };

  // DQE/RS mode: add column mapping to config
  if (isRsMode || isDqeMode) {
    config.column_mapping = {
      query_col: rsQueryCol ? rsQueryCol.value : "",
      result_title_col: rsResultTitleCol ? rsResultTitleCol.value : "",
      result_url_col: rsResultUrlCol ? rsResultUrlCol.value : "",
      result_rank_col: rsResultRankCol ? rsResultRankCol.value : "",
      expected_title_col: rsExpectedTitleCol ? rsExpectedTitleCol.value : "",
      expected_url_col: rsExpectedUrlCol ? rsExpectedUrlCol.value : "",
    };
  }

  formData.append("file", csvFile);
  formData.append("config_json", JSON.stringify(config));

  try {
    progressPanel.setAttribute("aria-hidden", "false");
    progressText.textContent = "Starting...";
    progressCount.textContent = "";
    progressFill.style.width = "0%";
    if (progressTimer) clearInterval(progressTimer);
    progressTimer = setInterval(pollProgress, 1200);

    const response = await fetch("api/run-option3", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const errorText = await response.text();
      if (progressTimer) {
        clearInterval(progressTimer);
        progressTimer = null;
      }
      setStatus(""); // Hide status on error
      showNotification(errorText || "Failed to process request.", "error");
      return;
    }

    const data = await response.json();

    if (data.status === "success") {
      currentZipContent = data.zip_content;
      currentFilename = data.filename;
      currentPreviewData = data.preview;

      // Refresh usage dashboard after successful run
      fetchUsageStatus();

      // Render Insights
      const insightsContainer = document.getElementById("insights-container");
      const insightsContent = document.getElementById("insights-content");

      let insightsHtml = "";

      // Add Summary Stats first
      if (data.summary && data.summary.length > 0) {
        insightsHtml += `<div class="summary-stats" style="margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border);">`;
        insightsHtml += `<h4 style="color: var(--text-primary); margin-top: 0;">Run Summary</h4>`;
        const summaryHtml = data.summary
          .filter(line => line.trim() !== "" && !line.includes("OVERALL LLM ANALYSIS")) // Filter out the header we added to text file
          .map(line => {
            if (line.startsWith("- ")) {
              return `<li>${line.substring(2)}</li>`;
            }
            return `<p style="margin: 8px 0; font-weight: 600;">${line}</p>`;
          })
          .join("");
        insightsHtml += summaryHtml.includes("<li>") ? `<ul>${summaryHtml}</ul>` : summaryHtml;
        insightsHtml += `</div>`;
      }

      // Add Overall Analysis if present
      if (data.overall_analysis) {
        insightsHtml += `<div class="overall-analysis">`;
        insightsHtml += `<h4 style="color: var(--text-primary); margin-top: 0;">Overall Analysis</h4>`;
        // Use marked.parse to render markdown
        insightsHtml += `<div class="markdown-body" style="font-size: 14px; line-height: 1.6;">${marked.parse(data.overall_analysis)}</div>`;
        insightsHtml += `</div>`;
      }

      if (insightsHtml) {
        insightsContainer.style.display = "block";
        insightsContent.innerHTML = insightsHtml;
      } else {
        insightsContainer.style.display = "none";
      }

      resultsArea.style.display = "block";
      showNotification("Reports generated successfully.", "success");
      setStatus(""); // Hide status when completed
    } else {
      showNotification("Unknown response status.", "error");
    }

  } catch (error) {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    setStatus(""); // Hide status on error
    showNotification(`Error: ${error.message}`, "error");
  }
});

if (suTestBtn) {
  suTestBtn.addEventListener("click", async () => {
    // Clear previous results but don't show "No results" yet
    if (suTestResults) suTestResults.innerHTML = "";
    setSuTestStatus("");

    const suCurl = form.elements["suCurl"].value.trim();
    const suTitlePath = form.elements["suTitlePath"].value.trim();
    const suUrlPath = form.elements["suUrlPath"].value.trim();
    const suMaxResults = Number(form.elements["suMaxResults"].value || 10);
    const suTestQuery = form.elements["suTestQuery"].value.trim();

    if (!suTestQuery) {
      showNotification("Enter a test query first.", "error");
      return;
    }
    if (!suCurl) {
      showNotification("Provide cURL to run the test.", "error");
      return;
    }

    setSuTestStatus("Fetching SearchUnify results...");

    const titlePath = suTitlePath || "$.result.hits[*].highlight.TitleToDisplayString";
    const urlPath = suUrlPath || "$.result.hits[*].href";
    try {
      const response = await fetch("api/su-search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: suTestQuery,
          curl: suCurl,
          title_path: titlePath,
          url_path: urlPath,
          max_results: suMaxResults,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        showNotification(errorText || "Failed to fetch SearchUnify results.", "error");
        setSuTestStatus("Failed.", true);
        return;
      }

      const data = await response.json();
      const results = data.results || [];

      if (results.length === 0) {
        setSuTestStatus("No results returned.");
      } else {
        setSuTestStatus(`Found ${results.length} results.`);
        renderSuResults(results);
      }

    } catch (error) {
      showNotification(`Error: ${error.message}`, "error");
      setSuTestStatus("Error.", true);
    }
  });
}

const clearCurlBtn = document.getElementById("clear-curl-btn");
if (clearCurlBtn) {
  clearCurlBtn.addEventListener("click", () => {
    if (form.elements["suCurl"]) {
      form.elements["suCurl"].value = "";
      form.elements["suCurl"].focus();
    }
  });
}

const clearSuResultsBtn = document.getElementById("clear-su-results-btn");
if (clearSuResultsBtn) {
  clearSuResultsBtn.addEventListener("click", () => {
    setSuTestStatus("");
    renderSuResults([]);
    const resultsContainer = document.getElementById("su-test-results");
    if (resultsContainer) resultsContainer.innerHTML = "";
  });
}

const suCurlTextarea = form.elements["suCurl"];
if (suCurlTextarea) {
  suCurlTextarea.addEventListener('keydown', function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
      e.preventDefault();
      this.select();
    }
  });
}

// ============================================================================
// Usage Dashboard Functions
// ============================================================================

let usageTimer = null;
let cooldownCountdownTimer = null;
let currentUsageData = null;

function formatTimeRemaining(seconds) {
  if (seconds <= 0) return "now";

  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (days > 0) {
    return `${days}d ${hours}h`;
  } else if (hours > 0) {
    return `${hours}h ${minutes}m`;
  } else if (minutes > 0) {
    return `${minutes}m`;
  } else {
    return `${seconds}s`;
  }
}

function formatResetTime(isoString) {
  try {
    const resetDate = new Date(isoString);
    const now = new Date();
    const diffSeconds = Math.floor((resetDate - now) / 1000);
    return formatTimeRemaining(diffSeconds);
  } catch (e) {
    return "--";
  }
}

function updateUsageDashboard(data) {
  if (!data) return;

  currentUsageData = data;

  // Show dashboard only if rate limiting is enabled
  if (data.rate_limiting_enabled) {
    usageDashboard.style.display = "block";
  } else {
    usageDashboard.style.display = "none";
    return;
  }

  // Update daily usage
  const dailyPercent = (data.daily.used / data.daily.limit) * 100;
  dailyUsageCount.textContent = `${data.daily.used} / ${data.daily.limit}`;
  dailyUsageFill.style.width = `${Math.min(100, dailyPercent)}%`;
  dailyResetTime.textContent = `Resets in ${formatResetTime(data.daily.resets_at)}`;

  // Color coding for daily
  dailyUsageFill.classList.remove("warning", "danger");
  if (dailyPercent >= 90) {
    dailyUsageFill.classList.add("danger");
  } else if (dailyPercent >= 70) {
    dailyUsageFill.classList.add("warning");
  }

  // Update monthly usage
  const monthlyPercent = (data.monthly.used / data.monthly.limit) * 100;
  monthlyUsageCount.textContent = `${data.monthly.used} / ${data.monthly.limit}`;
  monthlyUsageFill.style.width = `${Math.min(100, monthlyPercent)}%`;
  monthlyResetTime.textContent = `Resets in ${formatResetTime(data.monthly.resets_at)}`;

  // Color coding for monthly
  monthlyUsageFill.classList.remove("warning", "danger");
  if (monthlyPercent >= 90) {
    monthlyUsageFill.classList.add("danger");
  } else if (monthlyPercent >= 70) {
    monthlyUsageFill.classList.add("warning");
  }

  // Update runs
  runsCount.textContent = `${data.runs.used_today} / ${data.runs.free_limit} free`;

  // Update cooldown info
  if (data.runs.cooldown_active) {
    cooldownInfo.style.display = "flex";
    updateCooldownTimer(data.runs.cooldown_remaining_seconds);

    // Start countdown timer if not already running
    if (!cooldownCountdownTimer) {
      cooldownCountdownTimer = setInterval(() => {
        if (currentUsageData && currentUsageData.runs.cooldown_remaining_seconds > 0) {
          currentUsageData.runs.cooldown_remaining_seconds--;
          updateCooldownTimer(currentUsageData.runs.cooldown_remaining_seconds);

          if (currentUsageData.runs.cooldown_remaining_seconds <= 0) {
            clearInterval(cooldownCountdownTimer);
            cooldownCountdownTimer = null;
            fetchUsageStatus(); // Refresh to get updated status
          }
        }
      }, 1000);
    }
  } else {
    cooldownInfo.style.display = "none";
    if (cooldownCountdownTimer) {
      clearInterval(cooldownCountdownTimer);
      cooldownCountdownTimer = null;
    }
  }

  // Update status badge
  usageStatusBadge.classList.remove("warning", "error");
  if (dailyPercent >= 100 || monthlyPercent >= 100) {
    usageStatusBadge.classList.add("error");
    usageStatusText.textContent = "Limit Reached";
  } else if (dailyPercent >= 90 || monthlyPercent >= 90 || data.runs.cooldown_active) {
    usageStatusBadge.classList.add("warning");
    usageStatusText.textContent = "Warning";
  } else {
    usageStatusText.textContent = "Active";
  }

  // Update limit warning banner
  if (dailyPercent >= 100) {
    limitWarning.style.display = "flex";
    limitWarningText.textContent = `Daily limit reached (${data.daily.used}/${data.daily.limit}). Resets in ${formatResetTime(data.daily.resets_at)}.`;
  } else if (monthlyPercent >= 100) {
    limitWarning.style.display = "flex";
    limitWarningText.textContent = `Monthly limit reached (${data.monthly.used}/${data.monthly.limit}). Resets in ${formatResetTime(data.monthly.resets_at)}.`;
  } else if (data.runs.cooldown_active && data.runs.used_today >= data.runs.free_limit) {
    limitWarning.style.display = "flex";
    limitWarningText.textContent = `You've used all ${data.runs.free_limit} free runs today. Cooldown active.`;
  } else {
    limitWarning.style.display = "none";
  }
}

function updateCooldownTimer(seconds) {
  if (seconds <= 0) {
    cooldownTimer.textContent = "Next run available now";
    return;
  }

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;

  cooldownTimer.textContent = `Next run available in ${hours}h ${minutes}m ${secs}s`;
}

async function fetchUsageStatus() {
  try {
    const resp = await fetch("api/usage-status");
    if (resp.ok) {
      const data = await resp.json();
      updateUsageDashboard(data);
    }
  } catch (e) {
    console.error("Failed to fetch usage status:", e);
  }
}

async function checkRateLimitBeforeSubmit() {
  try {
    const resp = await fetch("api/usage-status");
    if (!resp.ok) return true; // Allow if can't check

    const data = await resp.json();

    // If rate limiting is disabled, allow
    if (!data.rate_limiting_enabled) return true;

    // Check if daily limit is reached
    if (data.daily.used >= data.daily.limit) {
      showNotification(
        `Daily limit reached (${data.daily.used}/${data.daily.limit}). Resets in ${formatResetTime(data.daily.resets_at)}.`,
        "error"
      );
      return false;
    }

    // Check if monthly limit is reached
    if (data.monthly.used >= data.monthly.limit) {
      showNotification(
        `Monthly limit reached (${data.monthly.used}/${data.monthly.limit}). Resets in ${formatResetTime(data.monthly.resets_at)}.`,
        "error"
      );
      return false;
    }

    // Check if cooldown is active
    if (data.runs.cooldown_active && data.runs.cooldown_remaining_seconds > 0) {
      const hours = Math.floor(data.runs.cooldown_remaining_seconds / 3600);
      const minutes = Math.floor((data.runs.cooldown_remaining_seconds % 3600) / 60);
      showNotification(
        `Cooldown active. Next run available in ${hours}h ${minutes}m.`,
        "error"
      );
      return false;
    }

    return true;
  } catch (e) {
    console.error("Failed to check rate limits:", e);
    return true; // Allow if check fails
  }
}

// Start polling usage status
fetchUsageStatus();
usageTimer = setInterval(fetchUsageStatus, 10000); // Poll every 10 seconds

// ============================================================================
// Initialize
// ============================================================================
showStep(1);
