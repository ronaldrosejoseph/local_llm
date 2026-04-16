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

// Initialize modules that need setup
initScrollTracking();
initConfigSliders();
initDocumentUpload();
initSpeechRecognition();
