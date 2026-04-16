/**
 * Model management — loading, adding, and switching models.
 */

import { state, elements, API_URL } from './state.js';
import { loadSettingsModels } from './settings.js';

// --- Load Models ---

export async function loadModels() {
    try {
        const response = await fetch(`${API_URL}/api/models`);
        const models = await response.json();

        elements.modelSelect.innerHTML = '';
        models.forEach(m => {
            const option = document.createElement('option');
            option.value = m.name;
            option.textContent = m.name.split('/').pop();
            option.selected = m.active;
            elements.modelSelect.appendChild(option);

            if (m.active) {
                elements.modelBadge.textContent = option.textContent;
            }
        });
    } catch (error) {
        console.error('Error loading models:', error);
    }
}

// --- Add New Model ---

export async function addNewModel() {
    const name = elements.newModelInput.value.trim();
    if (!name) return;

    if (!name.includes('mlx-community')) {
        alert("Model must be from the 'mlx-community' organization on Hugging Face.");
        return;
    }

    const addBtn = document.getElementById('add-model-btn');
    const progressContainer = document.getElementById('model-download-progress');
    const statusText = document.getElementById('progress-status');
    const percentText = document.getElementById('progress-percent');
    const barFill = document.getElementById('progress-bar-fill');

    // Reset and show progress UI
    addBtn.disabled = true;
    elements.newModelInput.disabled = true;
    progressContainer.style.display = 'block';
    barFill.style.width = '0%';
    statusText.textContent = 'Initializing...';
    percentText.textContent = '0%';

    try {
        const response = await fetch(`${API_URL}/api/models`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.detail || 'Error starting download');
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        outer: while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const lines = decoder.decode(value).split('\n');
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6).trim();
                if (raw === '[DONE]') break outer;

                try {
                    const data = JSON.parse(raw);
                    console.log("Model progress:", data);

                    if (data.status === 'checking') {
                        statusText.textContent = data.message;
                    } else if (data.status === 'downloading') {
                        statusText.textContent = data.message;
                        const pct = data.percent || 0;
                        percentText.textContent = `${pct}%`;
                        barFill.style.width = `${pct}%`;
                    } else if (data.status === 'ready') {
                        statusText.textContent = 'Success!';
                        percentText.textContent = '100%';
                        barFill.style.width = '100%';
                        elements.newModelInput.value = '';

                        setTimeout(async () => {
                            progressContainer.style.display = 'none';
                            addBtn.disabled = false;
                            elements.newModelInput.disabled = false;
                            await loadModels();
                            await loadSettingsModels();
                        }, 1500);
                    } else if (data.status === 'error') {
                        throw new Error(data.message || 'Unknown error');
                    }
                } catch (e) {
                    if (e.message !== 'Unexpected end of JSON input') {
                        throw e;
                    }
                }
            }
        }
    } catch (error) {
        console.error('Error adding model:', error);
        alert(`Failed to add model: ${error.message}`);
        progressContainer.style.display = 'none';
        addBtn.disabled = false;
        elements.newModelInput.disabled = false;
    }
}

// --- Switch Model ---

export async function switchModel(modelName) {
    elements.sendBtn.disabled = true;
    elements.chatInput.disabled = true;
    const originalBadgeText = elements.modelBadge.textContent;

    // Lock UI
    document.querySelectorAll('#chat-history, .new-chat-btn, #settings-open-btn').forEach(item => {
        item.style.pointerEvents = 'none';
        item.style.opacity = '0.5';
    });

    const setBadge = (text, pulse = true) => {
        elements.modelBadge.textContent = text;
        elements.modelBadge.style.opacity = pulse ? '0.6' : '1';
        elements.modelBadge.style.fontStyle = pulse ? 'italic' : 'normal';
    };

    setBadge('Checking...');

    try {
        const response = await fetch(`${API_URL}/api/models/active`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: modelName })
        });

        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            alert(data.detail || 'Error switching model');
            setBadge(originalBadgeText, false);
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        outer: while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const lines = decoder.decode(value).split('\n');
            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                const raw = line.slice(6).trim();
                if (raw === '[DONE]') break outer;

                try {
                    const data = JSON.parse(raw);
                    if (data.status === 'downloading') {
                        setBadge(data.message || 'Downloading...');
                    } else if (data.status === 'loading') {
                        setBadge(data.message || 'Loading...');
                    } else if (data.status === 'ready') {
                        if (data.fallback) {
                            alert(`Error loading model "${data.requested.split('/').pop()}": ${data.error}\n\nFalling back to default model: ${data.model}`);
                        }
                        setBadge(data.model, false);
                        console.log(`Switched to ${data.full}`);
                        await loadModels();
                    } else if (data.status === 'error') {
                        alert(data.message || 'Error loading model');
                        setBadge(originalBadgeText, false);
                    }
                } catch (_) { }
            }
        }
    } catch (error) {
        console.error('Error switching model:', error);
        setBadge(originalBadgeText, false);
    } finally {
        elements.sendBtn.disabled = false;
        elements.chatInput.disabled = false;
        elements.modelBadge.style.fontStyle = 'normal';

        // Unlock UI
        document.querySelectorAll('#chat-history, .new-chat-btn, #settings-open-btn').forEach(item => {
            item.style.pointerEvents = 'auto';
            item.style.opacity = '1';
        });
    }
}
