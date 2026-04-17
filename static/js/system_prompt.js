/**
 * System prompt management — per-chat persona/instruction editing.
 */

import { state, elements, API_URL } from './state.js';

export function toggleSystemPrompt() {
    const panel = elements.systemPromptPanel;
    const input = elements.systemPromptInput;
    const toggle = elements.systemPromptToggle;

    const isHidden = !panel.classList.contains('open');
    panel.classList.toggle('open', isHidden);
    toggle.classList.toggle('active', isHidden);
    if (isHidden) input.focus();
}

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
    const saveBtn = elements.systemPromptSave;
    const prompt = input.value.trim();

    // If it's a new chat, we just keep it in the UI and show "Applied"
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
        showSaveFeedback('✓ Saved', '#50fa7b');
    } catch (err) {
        console.error('Error saving system prompt:', err);
    }
}

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
