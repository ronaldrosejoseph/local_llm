/**
 * System prompt management — per-chat persona editing + reusable templates.
 */

import { state, elements, API_URL } from './state.js';

// --- Panel toggle ---

export function toggleSystemPrompt() {
    const panel = elements.systemPromptPanel;
    const toggle = elements.systemPromptToggle;
    const isHidden = !panel.classList.contains('open');
    panel.classList.toggle('open', isHidden);
    toggle.classList.toggle('active', isHidden);
    if (isHidden) elements.systemPromptInput.focus();
}

// --- Per-chat load / save (existing) ---

export async function loadSystemPrompt(chatId) {
    const input = elements.systemPromptInput;
    const toggle = elements.systemPromptToggle;
    const panel = elements.systemPromptPanel;

    if (!chatId) {
        input.value = '';
        panel.classList.remove('open');
        toggle.classList.remove('active', 'has-prompt');
        return;
    }

    try {
        const res = await fetch(`${API_URL}/api/chats/${chatId}/system-prompt`);
        if (!res.ok) return;
        const data = await res.json();
        input.value = data.system_prompt || '';
        toggle.classList.toggle('has-prompt', !!data.system_prompt);
    } catch (err) {
        console.error('Error loading system prompt:', err);
    }
}

export async function saveSystemPrompt() {
    const input = elements.systemPromptInput;
    const toggle = elements.systemPromptToggle;
    const prompt = input.value.trim();

    if (!state.currentChatId) {
        toggle.classList.toggle('has-prompt', !!prompt);
        showSaveFeedback('Applied', '#50fa7b');
        return;
    }

    try {
        const res = await fetch(`${API_URL}/api/chats/${state.currentChatId}/system-prompt`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ system_prompt: prompt })
        });
        if (!res.ok) throw new Error('Failed to save');
        toggle.classList.toggle('has-prompt', !!prompt);
        showSaveFeedback('Saved', '#50fa7b');
    } catch (err) {
        console.error('Error saving system prompt:', err);
    }
}

// --- Template search ---

let _searchTimer = null;
let _searchSeq = 0;  // monotonic counter — ignore stale async responses

export function initSystemPromptSearch() {
    const searchInput = elements.systemPromptSearch;
    const resultsEl = elements.systemPromptSearchResults;

    searchInput.addEventListener('input', () => {
        clearTimeout(_searchTimer);
        const q = searchInput.value.trim();
        if (!q) {
            _searchSeq++;  // invalidate any in-flight search
            resultsEl.classList.remove('visible');
            resultsEl.innerHTML = '';
            return;
        }
        _searchTimer = setTimeout(() => searchTemplates(q), 250);
    });

    searchInput.addEventListener('focus', () => {
        const q = searchInput.value.trim();
        if (q) {
            searchTemplates(q);
        } else {
            _searchSeq++;  // invalidate any in-flight search from stale focus
        }
    });

    // Hide results when clicking outside
    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !resultsEl.contains(e.target)) {
            resultsEl.classList.remove('visible');
        }
    });

    // Save as Template button
    elements.systemPromptTemplateSave.addEventListener('click', saveAsTemplate);
}

async function searchTemplates(query) {
    const seq = ++_searchSeq;  // capture current sequence number
    const resultsEl = elements.systemPromptSearchResults;
    try {
        const res = await fetch(`${API_URL}/api/system-prompts?q=${encodeURIComponent(query)}`);
        if (!res.ok) return;
        const templates = await res.json();
        // Ignore stale responses: either superseded by a newer search, or the
        // input has been cleared (user already picked a template and closed the dropdown)
        if (seq !== _searchSeq) return;
        if (!elements.systemPromptSearch.value.trim()) return;

        if (!templates.length) {
            resultsEl.innerHTML = '<div class="sp-search-empty">No templates found</div>';
        } else {
            resultsEl.innerHTML = templates.map(t => `
                <div class="sp-search-item" data-id="${t.id}" data-content="${escapeHtml(t.content)}">
                    <div class="sp-search-item-body">
                        <div class="sp-search-item-name">${escapeHtml(t.name)}</div>
                        <div class="sp-search-item-preview">${escapeHtml(truncate(t.content, 80))}</div>
                    </div>
                    <button class="sp-search-item-delete" data-id="${t.id}" title="Delete template">
                        <i data-lucide="trash-2"></i>
                    </button>
                </div>
            `).join('');
            lucide.createIcons({ elements: Array.from(resultsEl.querySelectorAll('[data-lucide]')) });
        }
        resultsEl.classList.add('visible');
    } catch (err) {
        console.error('Error searching templates:', err);
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function truncate(str, maxLen) {
    return str.length > maxLen ? str.slice(0, maxLen) + '...' : str;
}

// Load template or delete via event delegation
elements.systemPromptSearchResults.addEventListener('click', async (e) => {
    // Delete button clicked
    const deleteBtn = e.target.closest('.sp-search-item-delete');
    if (deleteBtn) {
        e.stopPropagation();
        const id = deleteBtn.dataset.id;
        const name = deleteBtn.parentElement.querySelector('.sp-search-item-name')?.textContent || 'this template';
        if (!confirm(`Delete "${name}"?`)) return;

        try {
            const res = await fetch(`${API_URL}/api/system-prompts/${id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error('Failed to delete');
            // Refresh search results
            const q = elements.systemPromptSearch.value.trim();
            if (q) searchTemplates(q);
        } catch (err) {
            console.error('Error deleting template:', err);
            const { showToast } = await import('./toast.js');
            showToast('Failed to delete template.', 'error');
        }
        return;
    }

    // Load template into textarea
    const item = e.target.closest('.sp-search-item');
    if (!item) return;

    const content = item.dataset.content;
    elements.systemPromptInput.value = content;
    elements.systemPromptInput.focus();

    // Auto-expand textarea
    elements.systemPromptInput.style.height = 'auto';
    elements.systemPromptInput.style.height = elements.systemPromptInput.scrollHeight + 'px';

    // Close search results
    elements.systemPromptSearchResults.classList.remove('visible');
    elements.systemPromptSearch.value = '';
});

// --- Save as Template ---

async function saveAsTemplate() {
    const content = elements.systemPromptInput.value.trim();
    if (!content) {
        import('./toast.js').then(m => m.showToast('Enter a system prompt to save.', 'warning'));
        return;
    }

    // Use first line / sentence as default name
    const firstLine = content.split('\n')[0].trim();
    const defaultName = firstLine.length > 50 ? firstLine.slice(0, 47) + '...' : firstLine;

    const name = prompt('Template name:', defaultName);
    if (!name || !name.trim()) return;

    try {
        const res = await fetch(`${API_URL}/api/system-prompts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name.trim(), content })
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || 'Failed to save template');
        }

        const { showToast } = await import('./toast.js');
        showToast('Template saved!', 'success', 3000);
    } catch (err) {
        console.error('Error saving template:', err);
        const { showToast } = await import('./toast.js');
        showToast(`Failed to save template: ${err.message}`, 'error');
    }
}

// --- Feedback helper ---

function showSaveFeedback(text, color) {
    const saveBtn = elements.systemPromptSave;
    const originalHTML = saveBtn.innerHTML;
    saveBtn.textContent = text;
    saveBtn.style.color = color;
    setTimeout(() => {
        saveBtn.innerHTML = originalHTML;
        saveBtn.style.color = '';
        lucide.createIcons({ elements: Array.from(saveBtn.querySelectorAll('[data-lucide]')) });
    }, 1500);
}
