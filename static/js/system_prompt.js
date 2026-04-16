/**
 * System prompt management — per-chat persona/instruction editing.
 */

import { state, elements, API_URL } from './state.js';

const panel = document.getElementById('system-prompt-panel');
const input = document.getElementById('system-prompt-input');
const toggle = document.getElementById('system-prompt-toggle');
const saveBtn = document.getElementById('system-prompt-save');

export function toggleSystemPrompt() {
    const isHidden = !panel.classList.contains('open');
    panel.classList.toggle('open', isHidden);
    toggle.classList.toggle('active', isHidden);
    if (isHidden) input.focus();
}

export async function loadSystemPrompt(chatId) {
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
    if (!state.currentChatId) return;

    const prompt = input.value.trim();

    try {
        const res = await fetch(`${API_URL}/api/chats/${state.currentChatId}/system-prompt`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ system_prompt: prompt })
        });

        if (!res.ok) throw new Error('Failed to save');

        toggle.classList.toggle('has-prompt', !!prompt);

        // Visual feedback
        const originalHTML = saveBtn.innerHTML;
        saveBtn.textContent = '✓ Saved';
        saveBtn.style.color = '#50fa7b';
        setTimeout(() => {
            saveBtn.innerHTML = originalHTML;
            saveBtn.style.color = '';
            lucide.createIcons({ elements: Array.from(saveBtn.querySelectorAll('[data-lucide]')) });
        }, 1500);
    } catch (err) {
        console.error('Error saving system prompt:', err);
    }
}
