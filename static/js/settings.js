/**
 * Settings modal — config sliders, model library management.
 */

import { state, elements, API_URL } from './state.js';
import { loadModels } from './models.js';
import { showToast } from './toast.js';

// --- Settings Modal ---

export function openSettings() {
    loadSettingsModels();
    loadHfCacheInfo();
    elements.settingsModal.style.display = 'flex';
    setTimeout(() => elements.settingsModal.classList.add('active'), 10);
}

export function closeSettings() {
    elements.settingsModal.classList.remove('active');
    // Reset cache delete UI to initial state
    const confirmArea = document.getElementById('hf-cache-confirm-area');
    const deleteArea = document.getElementById('hf-cache-delete-area');
    const confirmInput = document.getElementById('hf-cache-confirm-input');
    const confirmBtn = document.getElementById('hf-cache-confirm-btn');
    const startBtn = document.getElementById('hf-cache-delete-start-btn');
    if (confirmArea) confirmArea.style.display = 'none';
    if (deleteArea) deleteArea.style.display = 'block';
    if (confirmInput) confirmInput.value = '';
    if (confirmBtn) confirmBtn.disabled = true;
    if (startBtn) startBtn.disabled = false;
    setTimeout(() => elements.settingsModal.style.display = 'none', 300);
}

// --- Reset to Defaults ---

export async function resetSettings() {
    const defaults = {
        max_tokens: 8192,
        temperature: 0.3,
        top_p: 0.9,
        repetition_penalty: 1.1,
        rag_similarity_threshold: 0.3,
        pdf_text_pages_per_batch: 50,
        pdf_image_pages_per_batch: 5,
        image_generation_resolution: '720x720',
        rolling_window_max_tokens: 3200,
        summary_max_tokens: 600,
        context_window_pct: 100,
    };

    try {
        const res = await fetch(`${API_URL}/api/config`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(defaults),
        });
        if (!res.ok) throw new Error('Failed to reset');
        const cfg = await res.json();
        applyConfigToUI(cfg);
        showToast('Settings reset to defaults.', 'success', 3000);
    } catch (err) {
        console.error('Failed to reset settings:', err);
        showToast('Failed to reset settings.', 'error');
    }
}

// --- Config Load/Save ---

export async function loadConfig() {
    try {
        const res = await fetch(`${API_URL}/api/config`);
        if (!res.ok) return;
        const cfg = await res.json();
        applyConfigToUI(cfg);
    } catch (err) {
        console.error('Failed to load config:', err);
    }
}

function applyConfigToUI(cfg) {
    const set = (id, valId, val) => {
        const el = document.getElementById(id);
        const valEl = document.getElementById(valId);
        if (el) el.value = val;
        if (valEl) valEl.textContent = val;
    };
    set('cfg-max-tokens', 'val-max-tokens', cfg.max_tokens);
    set('cfg-temperature', 'val-temperature', cfg.temperature);
    set('cfg-top-p', 'val-top-p', cfg.top_p);
    set('cfg-rep-penalty', 'val-rep-penalty', cfg.repetition_penalty);
    set('cfg-rag-similarity-threshold', 'val-rag-similarity-threshold', cfg.rag_similarity_threshold || 0.3);
    set('cfg-pdf-text-batch', 'val-pdf-text-batch', cfg.pdf_text_pages_per_batch || 50);
    set('cfg-pdf-image-batch', 'val-pdf-image-batch', cfg.pdf_image_pages_per_batch || 5);
    set('cfg-image-res', null, cfg.image_generation_resolution || "720x720");
    set('cfg-rolling-max-tokens', 'val-rolling-max-tokens', cfg.rolling_window_max_tokens ?? 3200);
    set('cfg-summary-max-tokens', 'val-summary-max-tokens', cfg.summary_max_tokens ?? 600);
    set('cfg-context-window', 'val-context-window', cfg.context_window_pct ?? 100);
    // Override the raw number display with percentage label
    const ctxVal = document.getElementById('val-context-window');
    const ctxSlider = document.getElementById('cfg-context-window');
    if (ctxVal && ctxSlider) ctxVal.textContent = ctxSlider.value + '%';
}

function scheduleConfigSave(patch) {
    clearTimeout(state._configSaveTimer);
    state._configSaveTimer = setTimeout(async () => {
        try {
            const res = await fetch(`${API_URL}/api/config`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            });
            const cfg = await res.json();
            applyConfigToUI(cfg);
        } catch (err) {
            console.error('Failed to save config:', err);
        }
    }, 300);
}

// --- Wire Config Sliders ---

export function initConfigSliders() {
    document.getElementById('cfg-max-tokens').addEventListener('input', function () {
        document.getElementById('val-max-tokens').textContent = this.value;
        scheduleConfigSave({ max_tokens: parseInt(this.value) });
    });
    document.getElementById('cfg-temperature').addEventListener('input', function () {
        document.getElementById('val-temperature').textContent = parseFloat(this.value).toFixed(2);
        scheduleConfigSave({ temperature: parseFloat(this.value) });
    });
    document.getElementById('cfg-top-p').addEventListener('input', function () {
        document.getElementById('val-top-p').textContent = parseFloat(this.value).toFixed(2);
        scheduleConfigSave({ top_p: parseFloat(this.value) });
    });
    document.getElementById('cfg-rep-penalty').addEventListener('input', function () {
        document.getElementById('val-rep-penalty').textContent = parseFloat(this.value).toFixed(2);
        scheduleConfigSave({ repetition_penalty: parseFloat(this.value) });
    });
    document.getElementById('cfg-rag-similarity-threshold').addEventListener('input', function () {
        document.getElementById('val-rag-similarity-threshold').textContent = parseFloat(this.value).toFixed(2);
        scheduleConfigSave({ rag_similarity_threshold: parseFloat(this.value) });
    });
    document.getElementById('cfg-pdf-text-batch').addEventListener('input', function () {
        document.getElementById('val-pdf-text-batch').textContent = this.value;
        scheduleConfigSave({ pdf_text_pages_per_batch: parseInt(this.value) });
    });
    document.getElementById('cfg-pdf-image-batch').addEventListener('input', function () {
        document.getElementById('val-pdf-image-batch').textContent = this.value;
        scheduleConfigSave({ pdf_image_pages_per_batch: parseInt(this.value) });
    });
    document.getElementById('cfg-image-res').addEventListener('change', function () {
        scheduleConfigSave({ image_generation_resolution: this.value });
    });
    document.getElementById('cfg-rolling-max-tokens').addEventListener('input', function () {
        document.getElementById('val-rolling-max-tokens').textContent = this.value;
        scheduleConfigSave({ rolling_window_max_tokens: parseInt(this.value) });
    });
    document.getElementById('cfg-summary-max-tokens').addEventListener('input', function () {
        document.getElementById('val-summary-max-tokens').textContent = this.value;
        scheduleConfigSave({ summary_max_tokens: parseInt(this.value) });
    });
    document.getElementById('cfg-context-window').addEventListener('input', function () {
        document.getElementById('val-context-window').textContent = this.value + '%';
        scheduleConfigSave({ context_window_pct: parseInt(this.value) });
    });
}

// --- Settings: Model Library ---

export async function loadSettingsModels() {
    const list = document.getElementById('settings-models-list');
    list.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">Loading...</p>';
    try {
        const res = await fetch(`${API_URL}/api/models`);
        if (!res.ok) throw new Error('Failed to load models');
        const models = await res.json();

        list.innerHTML = '';
        if (!models.length) {
            list.innerHTML = '<p style="color:var(--text-muted);font-size:13px;">No models added yet.</p>';
            return;
        }

        models.forEach(m => {
            const parts = m.name.split('/');
            const org = parts[0] || '';
            const name = parts[1] || m.name;

            const item = document.createElement('div');
            item.className = 'settings-model-item';

            const typeLabel = m.type === 'vlm' ? 'VLM' : m.type === 'lm' ? 'LM' : '?';
            const typeTitle = m.type === 'vlm' ? 'Vision-Language Model' : m.type === 'lm' ? 'Text-Only Model' : 'Not yet verified — load the model first';
            const typeBadge = `<span class="settings-model-type ${m.type || 'unknown'}" title="${typeTitle}">${typeLabel}</span>`;

            item.innerHTML = `
                <div class="settings-model-info">
                    <span class="settings-model-name" title="${m.name}">${name}</span>
                    <span class="settings-model-org">${org}</span>
                </div>
                ${typeBadge}
                ${m.active ? '<span class="settings-model-active-badge">Active</span>' : ''}
                ${m.name === 'mlx-community/gemma-4-e2b-it-4bit' ? '<span class="settings-model-active-badge" style="background:rgba(255,170,80,0.12);color:#f0a040;border-color:rgba(255,170,80,0.3);">Fallback</span>' : ''}
                <button class="delete-model-btn" title="Delete model" ${m.active || m.name === 'mlx-community/gemma-4-e2b-it-4bit' ? 'disabled' : ''}>
                    <i data-lucide="trash-2"></i>
                </button>
            `;

            const btn = item.querySelector('.delete-model-btn');
            if (!m.active && m.name !== 'mlx-community/gemma-4-e2b-it-4bit') {
                btn.addEventListener('click', () => confirmDeleteModel(m.name, btn));
            }

            list.appendChild(item);
        });
        lucide.createIcons({ elements: Array.from(list.querySelectorAll('[data-lucide]')) });
    } catch (err) {
        list.innerHTML = `<p style="color:#ff5555;font-size:13px;">Error: ${err.message}</p>`;
    }
}

async function confirmDeleteModel(modelName, btn) {
    const shortName = modelName.split('/').pop();
    if (!confirm(`Delete "${shortName}"?\n\nThis will remove it from the list AND delete it from disk.`)) return;

    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader"></i>';
    lucide.createIcons({ elements: [btn.querySelector('[data-lucide]')] });

    try {
        const res = await fetch(`${API_URL}/api/models/${encodeURIComponent(modelName)}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Delete failed');

        await loadModels();
        await loadSettingsModels();
    } catch (err) {
        showToast(`Failed to delete model: ${err.message}`, "error");
        btn.disabled = false;
        lucide.createIcons({ elements: [btn.querySelector('[data-lucide]')] });
    }
}

// --- HuggingFace Token Management ---

async function checkHfTokenStatus() {
    try {
        const res = await fetch(`${API_URL}/api/hf-token/status`);
        const data = await res.json();
        const input = document.getElementById('hf-token-input');
        const saveBtn = document.getElementById('hf-token-save-btn');
        const deleteBtn = document.getElementById('hf-token-delete-btn');
        const statusEl = document.getElementById('hf-token-status');

        if (data.stored) {
            input.value = '';
            input.placeholder = 'Token stored in Keychain';
            input.disabled = true;
            saveBtn.disabled = true;
            saveBtn.style.opacity = '0.4';
            deleteBtn.style.display = 'inline-flex';
            statusEl.textContent = '✅ Token is stored securely in macOS Keychain. Remove it to add a new one.';
            statusEl.style.color = '#50fa7b';
        } else {
            input.placeholder = 'hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx';
            input.disabled = false;
            saveBtn.disabled = false;
            saveBtn.style.opacity = '1';
            deleteBtn.style.display = 'none';
            statusEl.textContent = '';
        }
    } catch (err) {
        console.error('HF token status check failed:', err);
    }
}

async function saveHfToken() {
    const input = document.getElementById('hf-token-input');
    const saveBtn = document.getElementById('hf-token-save-btn');
    const statusEl = document.getElementById('hf-token-status');
    const token = input.value.trim();

    if (!token) {
        showToast('Enter a HuggingFace token.', 'warning');
        return;
    }

    input.disabled = true;
    saveBtn.disabled = true;
    statusEl.textContent = 'Verifying token...';
    statusEl.style.color = 'var(--text-muted)';

    try {
        const res = await fetch(`${API_URL}/api/hf-token/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });

        if (res.ok) {
            const data = await res.json();
            showToast(data.message, 'success');
            input.value = '';
            await checkHfTokenStatus();
        } else {
            const err = await res.json();
            statusEl.textContent = `❌ ${err.detail}`;
            statusEl.style.color = '#ff5555';
            showToast(err.detail, 'error', 0);
            input.disabled = false;
            saveBtn.disabled = false;
        }
    } catch (err) {
        statusEl.textContent = `❌ ${err.message}`;
        statusEl.style.color = '#ff5555';
        showToast(`Failed to save token: ${err.message}`, 'error');
        input.disabled = false;
        saveBtn.disabled = false;
    }
}

async function deleteHfToken() {
    if (!confirm('Remove the stored HuggingFace token?')) return;

    try {
        await fetch(`${API_URL}/api/hf-token`, { method: 'DELETE' });
        showToast('Token removed.', 'success');
        await checkHfTokenStatus();
    } catch (err) {
        showToast(`Failed to remove token: ${err.message}`, 'error');
    }
}

export function initHfTokenUI() {
    checkHfTokenStatus();
    document.getElementById('hf-token-save-btn').addEventListener('click', saveHfToken);
    document.getElementById('hf-token-delete-btn').addEventListener('click', deleteHfToken);
}

// --- Model Cache Deletion ---

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(1024));
    const val = (bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0);
    return `${val} ${units[i]}`;
}

export async function loadHfCacheInfo() {
    const infoEl = document.getElementById('hf-cache-info');
    if (!infoEl) return;
    try {
        const res = await fetch(`${API_URL}/api/hf-cache/info`);
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        if (data.size_bytes === 0) {
            infoEl.textContent = 'No app data or model cache found on disk.';
        } else {
            infoEl.textContent = `Total size: ${formatBytes(data.size_bytes)}`;
        }
    } catch (err) {
        infoEl.textContent = 'Could not determine data size.';
    }
}

export function initHfCacheUI() {
    const startBtn = document.getElementById('hf-cache-delete-start-btn');
    const confirmArea = document.getElementById('hf-cache-confirm-area');
    const confirmInput = document.getElementById('hf-cache-confirm-input');
    const confirmBtn = document.getElementById('hf-cache-confirm-btn');
    const deleteArea = document.getElementById('hf-cache-delete-area');

    if (!startBtn) return;

    // Step 1: Show confirmation area
    startBtn.addEventListener('click', () => {
        deleteArea.style.display = 'none';
        confirmArea.style.display = 'block';
        confirmInput.value = '';
        confirmBtn.disabled = true;
        confirmInput.focus();
    });

    // Step 2: Enable confirm button only when exact text is typed
    confirmInput.addEventListener('input', () => {
        confirmBtn.disabled = confirmInput.value.trim() !== 'DELETE ALL APP DATA';
    });

    // Step 3: Execute deletion
    confirmBtn.addEventListener('click', async () => {
        if (confirmInput.value.trim() !== 'DELETE ALL APP DATA') return;

        startBtn.disabled = true;
        confirmBtn.disabled = true;
        confirmBtn.textContent = 'Deleting…';

        try {
            const res = await fetch(`${API_URL}/api/hf-cache`, { method: 'DELETE' });
            const data = await res.json();

            if (data.error) {
                showToast(data.error, 'error', 0);
            } else {
                const freed = formatBytes(data.deleted_bytes || 0);
                showToast(`Model cache deleted (${freed} freed).`, 'success', 5000);
            }
        } catch (err) {
            showToast(`Failed to delete cache: ${err.message}`, 'error');
        }

        // Reset UI
        confirmArea.style.display = 'none';
        deleteArea.style.display = 'block';
        startBtn.disabled = false;
        confirmBtn.textContent = 'Confirm Delete';
        await loadHfCacheInfo();
    });
}
