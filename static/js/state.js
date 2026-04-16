/**
 * Shared application state and DOM element references.
 * 
 * All modules import this to read/write shared state and access DOM elements.
 */

const API_URL = '';

// --- Mutable State ---
export const state = {
    currentChatId: null,
    abortController: null,
    isRecording: false,
    _userScrolledUp: false,
    deleteCallback: null,
    _configSaveTimer: null,
};

// --- DOM Element References ---
export const elements = {
    sidebar: document.getElementById('sidebar'),
    chatHistory: document.getElementById('chat-history'),
    messagesContainer: document.getElementById('messages-container'),
    chatInput: document.getElementById('chat-input'),
    sendBtn: document.getElementById('send-btn'),
    newChatBtn: document.getElementById('new-chat-btn'),
    welcomeScreen: document.getElementById('welcome-screen'),
    voiceBtn: document.getElementById('voice-btn'),
    autoSpeakToggle: document.getElementById('auto-speak-toggle'),
    currentChatTitle: document.getElementById('current-chat-title'),
    menuToggle: document.getElementById('menu-toggle'),
    sidebarOverlay: document.getElementById('sidebar-overlay'),
    stopBtn: document.getElementById('stop-btn'),
    modalContainer: document.getElementById('modal-container'),
    modalCancelBtn: document.getElementById('modal-cancel-btn'),
    modalConfirmBtn: document.getElementById('modal-confirm-btn'),
    modelSelect: document.getElementById('model-select'),
    newModelInput: document.getElementById('new-model-input'),
    addModelBtn: document.getElementById('add-model-btn'),
    modelBadge: document.querySelector('.model-badge'),
    fileUpload: document.getElementById('file-upload'),
    attachBtn: document.getElementById('attach-btn'),
    attachmentContainer: document.getElementById('attachment-pill-container'),
    attachmentName: document.getElementById('attachment-name'),
    settingsModal: document.getElementById('settings-modal'),
    settingsOpenBtn: document.getElementById('settings-open-btn'),
    settingsCloseBtn: document.getElementById('settings-close-btn'),
};

export { API_URL };
