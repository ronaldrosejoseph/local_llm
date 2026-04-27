/**
 * Main application entry point.
 * 
 * Imports all modules, initializes the app on DOMContentLoaded,
 * wires up event listeners, and exposes globals for inline handlers.
 */

import { state, elements } from './state.js';
import { initMarked, initScrollTracking, copyCode, copyToClipboard } from './utils.js';
import { sendMessage, stopGeneration } from './chat.js';
import { loadChatHistory, startNewChat, hideDeleteModal, toggleSidebar, closeSidebar } from './sidebar.js';
import { loadModels, addNewModel, switchModel } from './models.js';
import { openSettings, closeSettings, loadConfig, initConfigSliders } from './settings.js';
import { initDocumentUpload } from './documents.js';
import { speakResponse, stopSpeaking, toggleRecording, initSpeechRecognition } from './speech.js';
import { loadSystemPrompt, saveSystemPrompt, toggleSystemPrompt } from './system_prompt.js';

// --- Expose globals for inline onclick handlers in dynamically created HTML ---
window.speakResponse = speakResponse;
window.stopSpeaking = stopSpeaking;
window.copyToClipboard = copyToClipboard;
window.copyCode = copyCode;

// --- Initialize ---

// Initialize Lucide icons
lucide.createIcons();

// Configure Marked.js
initMarked();

// Load data on startup
document.addEventListener('DOMContentLoaded', () => {
    loadChatHistory();
    loadModels();
    loadConfig();
});

// --- Event Listeners ---

// Sidebar
elements.newChatBtn.addEventListener('click', startNewChat);
elements.menuToggle.addEventListener('click', toggleSidebar);
elements.sidebarOverlay.addEventListener('click', closeSidebar);

let searchTimeout;
document.getElementById('chat-search-input').addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
        loadChatHistory(e.target.value.trim());
    }, 300);
});

// Chat input
elements.sendBtn.addEventListener('click', () => sendMessage());
elements.chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Auto-expand textarea
elements.chatInput.addEventListener('input', () => {
    elements.chatInput.style.height = 'auto';
    elements.chatInput.style.height = elements.chatInput.scrollHeight + 'px';
});

// Stop & Voice
elements.stopBtn.addEventListener('click', stopGeneration);
elements.voiceBtn.addEventListener('click', toggleRecording);

// System Prompt
document.getElementById('system-prompt-toggle').addEventListener('click', toggleSystemPrompt);
document.getElementById('system-prompt-save').addEventListener('click', saveSystemPrompt);

// Models
elements.addModelBtn.addEventListener('click', addNewModel);
elements.modelSelect.addEventListener('change', () => switchModel(elements.modelSelect.value));

// Settings
elements.settingsOpenBtn.addEventListener('click', openSettings);
elements.settingsCloseBtn.addEventListener('click', closeSettings);
elements.settingsModal.addEventListener('click', (e) => {
    if (e.target === elements.settingsModal) closeSettings();
});

// Delete modal
elements.modalCancelBtn.addEventListener('click', hideDeleteModal);
elements.modalContainer.addEventListener('click', (e) => {
    if (e.target === elements.modalContainer) hideDeleteModal();
});
elements.modalConfirmBtn.onclick = () => {
    if (state.deleteCallback) state.deleteCallback();
    hideDeleteModal();
};

// Event delegation for copy buttons
elements.messagesContainer.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const container = btn.closest('.code-container');
    if (!container) return;
    const codeEl = container.querySelector('code[id]');
    if (codeEl) copyCode(codeEl.id);
});

// RAG Slider
const ragSlider = document.getElementById('rag-slider');
if (ragSlider) {
    ragSlider.addEventListener('input', async (e) => {
        const offset = parseInt(e.target.value);
        const limit = parseInt(e.target.step || 50);
        const total = parseInt(e.target.max) + 1; // max was set to total - 1
        const end = Math.min(offset + limit, total);
        
        document.getElementById('rag-status-text').textContent = `Context: ${offset + 1}-${end} / ${total}`;
        
        if (state.currentChatId) {
            try {
                // Import API_URL from state directly or use relative
                await fetch(`/api/chats/${state.currentChatId}/rag-status`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ offset: offset })
                });
            } catch (err) {
                console.error("Failed to update RAG offset", err);
            }
        }
    });
}

// Initialize modules that need setup
initScrollTracking();
initConfigSliders();
initDocumentUpload();
initSpeechRecognition();
