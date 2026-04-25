/**
 * Settings modal — config sliders, model library management.
 */

import { state, elements, API_URL } from './state.js';
import { loadModels } from './models.js';

// --- Settings Modal ---

export function openSettings() {
    loadSettingsModels();
    elements.settingsModal.style.display = 'flex';
    setTimeout(() => elements.settingsModal.classList.add('active'), 10);
}

export function closeSettings() {
    elements.settingsModal.classList.remove('active');
    setTimeout(() => elements.settingsModal.style.display = 'none', 300);
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
    set('cfg-pdf-text-batch', 'val-pdf-text-batch', cfg.pdf_text_pages_per_batch || 50);
    set('cfg-pdf-image-batch', 'val-pdf-image-batch', cfg.pdf_image_pages_per_batch || 5);
    set('cfg-image-res', null, cfg.image_generation_resolution || "720x720");
    set('cfg-rolling-max-tokens', 'val-rolling-max-tokens', cfg.rolling_window_max_tokens ?? 3200);
    set('cfg-summary-max-tokens', 'val-summary-max-tokens', cfg.summary_max_tokens ?? 600);
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

            item.innerHTML = `
                <div class="settings-model-info">
                    <span class="settings-model-name" title="${m.name}">${name}</span>
                    <span class="settings-model-org">${org}</span>
                </div>
                ${m.active ? '<span class="settings-model-active-badge">Active</span>' : ''}
                <button class="delete-model-btn" title="Delete model" ${m.active ? 'disabled' : ''}>
                    <i data-lucide="trash-2"></i>
                </button>
            `;

            const btn = item.querySelector('.delete-model-btn');
            if (!m.active) {
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
        alert(`Failed to delete model: ${err.message}`);
        btn.disabled = false;
        lucide.createIcons({ elements: [btn.querySelector('[data-lucide]')] });
    }
}
