// Constants
const API_URL = '';
let currentChatId = null;
let isRecording = false;
let abortController = null;

// DOM Elements
const sidebar = document.getElementById('sidebar');
const chatHistory = document.getElementById('chat-history');
const messagesContainer = document.getElementById('messages-container');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const newChatBtn = document.getElementById('new-chat-btn');
const welcomeScreen = document.getElementById('welcome-screen');
const voiceBtn = document.getElementById('voice-btn');
const autoSpeakToggle = document.getElementById('auto-speak-toggle');
const currentChatTitle = document.getElementById('current-chat-title');
const menuToggle = document.getElementById('menu-toggle');
const sidebarOverlay = document.getElementById('sidebar-overlay');
const stopBtn = document.getElementById('stop-btn');
const modalContainer = document.getElementById('modal-container');
const modalCancelBtn = document.getElementById('modal-cancel-btn');
const modalConfirmBtn = document.getElementById('modal-confirm-btn');
const modelSelect = document.getElementById('model-select');
const newModelInput = document.getElementById('new-model-input');
const addModelBtn = document.getElementById('add-model-btn');
const modelBadge = document.querySelector('.model-badge');

// Initialize Lucide icons
lucide.createIcons();

// Helper to strip common LLM control tokens from the UI
const stripControlTokens = (text) => {
    if (!text) return "";
    return text
        .replace(/<\|im_end\|>/g, '')
        .replace(/<\|im_start\|>/g, '')
        .replace(/<\|endoftext\|>/g, '')
        .trim();
};

// DOMPurify config: allow `id` on code elements and `onclick` on copy buttons.
// We must explicitly whitelist these because DOMPurify strips them by default.
const DOMPURIFY_CONFIG = {
    ADD_ATTR: ['id', 'title'],
    ALLOWED_TAGS: [
        'p', 'br', 'b', 'i', 'em', 'strong', 'a', 'ul', 'ol', 'li',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'blockquote', 'pre', 'code', 'hr',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'div', 'span', 'button', 'img',
        'del', 'ins', 'sup', 'sub'
    ]
};

// Function to safely render markdown with HTML escaping and sanitization
function renderMarkdown(content) {
    if (!content) return "";
    const clean = stripControlTokens(content);
    const html = marked.parse(clean);
    return window.DOMPurify ? DOMPurify.sanitize(html, DOMPURIFY_CONFIG) : html;
}

// Global Marked configuration
marked.use({
    // Prevent rendering of raw HTML tags by converting them to plain text tokens
    walkTokens(token) {
        if (token.type === 'html') {
            token.type = 'text';
        }
    },
    renderer: {
        code({ text, lang }) {
            const id = 'code-' + Math.random().toString(36).substr(2, 9);
            // Sanitize lang to only safe alphanumeric/dash chars before injecting into HTML
            const safeLang = (lang || 'text').replace(/[^a-z0-9-]/gi, '');
            // We MUST escape code content because we are building a string for innerHTML
            const escaped = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');

            return `
                <div class="code-container">
                    <button class="copy-btn" title="Copy to clipboard">
                        <i data-lucide="copy"></i>
                    </button>
                    <pre><code id="${id}" class="language-${safeLang}">${escaped}</code></pre>
                </div>
            `;
        }
    }
});

// Load data on startup
document.addEventListener('DOMContentLoaded', () => {
    loadChatHistory();
    loadModels();
    loadConfig();
});

// Event Listeners
newChatBtn.addEventListener('click', startNewChat);
sendBtn.addEventListener('click', () => sendMessage());
chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

voiceBtn.addEventListener('click', toggleRecording);
stopBtn.addEventListener('click', stopGeneration);
addModelBtn.addEventListener('click', addNewModel);
modelSelect.addEventListener('change', () => switchModel(modelSelect.value));

// Event delegation for copy buttons — handles cases where DOMPurify strips inline onclick
messagesContainer.addEventListener('click', (e) => {
    const btn = e.target.closest('.copy-btn');
    if (!btn) return;
    const container = btn.closest('.code-container');
    if (!container) return;
    const codeEl = container.querySelector('code[id]');
    if (codeEl) copyCode(codeEl.id);
});

const fileUpload = document.getElementById('file-upload');
const attachBtn = document.getElementById('attach-btn');
const attachmentContainer = document.getElementById('attachment-pill-container');
const attachmentName = document.getElementById('attachment-name');

// Toggle sidebar on mobile
menuToggle.addEventListener('click', toggleSidebar);
sidebarOverlay.addEventListener('click', closeSidebar);

// --- Settings Modal ---
const settingsModal = document.getElementById('settings-modal');
const settingsOpenBtn = document.getElementById('settings-open-btn');
const settingsCloseBtn = document.getElementById('settings-close-btn');

function openSettings() {
    loadSettingsModels();   // refresh model list each time
    settingsModal.style.display = 'flex';
    setTimeout(() => settingsModal.classList.add('active'), 10);
}

function closeSettings() {
    settingsModal.classList.remove('active');
    setTimeout(() => settingsModal.style.display = 'none', 300);
}

settingsOpenBtn.addEventListener('click', openSettings);
settingsCloseBtn.addEventListener('click', closeSettings);
settingsModal.addEventListener('click', (e) => {
    if (e.target === settingsModal) closeSettings();
});

// --- Config (Generation Settings) ---

async function loadConfig() {
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
}

// Debounced PATCH so we don't spam the server on every slider tick
let _configSaveTimer = null;
function scheduleConfigSave(patch) {
    clearTimeout(_configSaveTimer);
    _configSaveTimer = setTimeout(async () => {
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

// Wire each slider
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

// --- Settings: Model Library ---

async function loadSettingsModels() {
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

        // Refresh both the sidebar model select and the settings list
        await loadModels();
        await loadSettingsModels();
    } catch (err) {
        alert(`Failed to delete model: ${err.message}`);
        btn.disabled = false;
        lucide.createIcons({ elements: [btn.querySelector('[data-lucide]')] });
    }
}

// Document Upload Logic
attachBtn.addEventListener('click', () => {
    fileUpload.click();
});

fileUpload.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    if (!currentChatId) {
        await startNewChat();
        // Need to wait until chat id generates after first message for pure alignment,
        // but for now, generate UUID client side or force a message.
        currentChatId = crypto.randomUUID(); // optimistic
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('chat_id', currentChatId);

    // Show loading UI
    attachmentContainer.style.display = 'block';
    attachmentName.textContent = `Uploading ${file.name}...`;

    try {
        const res = await fetch(`${API_URL}/api/upload-document`, {
            method: 'POST',
            body: formData
        });

        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || "Upload failed");
        }

        const data = await res.json();

        if (data.status === 'ok') {
            attachmentName.textContent = `${file.name} (${data.chunks} chunks)`;
            console.log("Document processed securely.");

            // 1. Immediately append to the chat UI
            const msg = data.vision ? `[Attached Image: ${file.name}]` : `[Attached Document: ${file.name}]`;
            appendMessage('user', msg);

            // 2. Hide welcome screen if this was the first action
            welcomeScreen.style.display = 'none';

            // 3. Refresh chat history in sidebar to show the new chat if it was just created
            loadChatHistory();
        }
    } catch (err) {
        attachmentContainer.style.display = 'none';
        alert('Error uploading document: ' + err.message);
    } finally {
        // Reset file input so same file can be uploaded again if needed
        e.target.value = '';
    }
});

// Modal event listeners
modalCancelBtn.addEventListener('click', hideDeleteModal);
modalContainer.addEventListener('click', (e) => {
    if (e.target === modalContainer) hideDeleteModal();
});

// Auto-expand textarea
chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = chatInput.scrollHeight + 'px';
});

// --- Chat History Functions ---

async function loadChatHistory() {
    try {
        const response = await fetch(`${API_URL}/api/chats`);
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const chats = await response.json();

        chatHistory.innerHTML = '';
        chats.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.dataset.chatId = chat.id; // used by loadChat to apply active class in-place
            if (chat.id === currentChatId) item.classList.add('active');
            item.onclick = (e) => {
                if (e.target.closest('.delete-chat-btn')) return;
                loadChat(chat.id, chat.title);
            };

            const contentDiv = document.createElement('div');
            contentDiv.className = 'history-item-content';

            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', 'message-square');
            icon.style.cssText = 'width: 14px; height: 14px; vertical-align: middle; margin-right: 8px;';

            contentDiv.appendChild(icon);
            contentDiv.appendChild(document.createTextNode(chat.title));

            item.appendChild(contentDiv);

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'delete-chat-btn';
            deleteBtn.onclick = () => deleteChat(chat.id);
            deleteBtn.innerHTML = '<i data-lucide="trash-2" style="width: 14px; height: 14px;"></i>';

            item.appendChild(deleteBtn);
            chatHistory.appendChild(item);
        });
        lucide.createIcons({ elements: Array.from(chatHistory.querySelectorAll('[data-lucide]')) });
    } catch (error) {
        console.error('Error loading history:', error);
    }
}

async function loadChat(chatId, title) {
    currentChatId = chatId;
    currentChatTitle.textContent = title;
    welcomeScreen.style.display = 'none';
    messagesContainer.innerHTML = '';

    // Clear pending structural attachments instantly on switch
    attachmentContainer.style.display = 'none';
    fileUpload.value = '';

    // Apply active class in-place — avoids a redundant full sidebar refetch
    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.toggle('active', item.dataset.chatId === chatId);
    });

    try {
        const response = await fetch(`${API_URL}/api/chats/${chatId}/messages`);
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const messages = await response.json();

        messages.forEach(msg => {
            appendMessage(msg.role, msg.content);
        });

        // Hide RAG info on load; it will reappear if the next message triggers it
        const ragStatus = document.getElementById('rag-status');
        if (ragStatus) ragStatus.style.display = 'none';

        scrollToBottom(true);
        closeSidebar();
    } catch (error) {
        console.error('Error loading chat:', error);
    }
}

async function startNewChat() {
    currentChatId = null;
    currentChatTitle.textContent = 'New Conversation';
    messagesContainer.innerHTML = '';
    welcomeScreen.style.display = 'flex';

    // Clear pending structural attachments instantly on fresh slate
    attachmentContainer.style.display = 'none';
    fileUpload.value = '';
    
    // Reset RAG badge
    const ragStatus = document.getElementById('rag-status');
    if (ragStatus) ragStatus.style.display = 'none';

    loadChatHistory();
    closeSidebar();
    chatInput.value = '';
    chatInput.focus();

    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.remove('active');
    });
}

let deleteCallback = null;

async function deleteChat(chatId) {
    showDeleteModal(() => {
        performDelete(chatId);
    });
}

async function performDelete(chatId) {
    try {
        await fetch(`${API_URL}/api/chats/${chatId}`, { method: 'DELETE' });
        if (currentChatId === chatId) startNewChat();
        loadChatHistory();
        closeSidebar();
    } catch (err) {
        console.error('Error deleting chat:', err);
    }
}

function showDeleteModal(onConfirm) {
    deleteCallback = onConfirm;
    modalContainer.style.display = 'flex';
    setTimeout(() => {
        modalContainer.classList.add('active');
    }, 10);
}

function hideDeleteModal() {
    modalContainer.classList.remove('active');
    setTimeout(() => {
        modalContainer.style.display = 'none';
        deleteCallback = null;
    }, 300);
}

modalConfirmBtn.onclick = () => {
    if (deleteCallback) deleteCallback();
    hideDeleteModal();
};

function toggleSidebar() {
    sidebar.classList.toggle('active');
    sidebarOverlay.classList.toggle('active');
}

function closeSidebar() {
    sidebar.classList.remove('active');
    sidebarOverlay.classList.remove('active');
}

// --- Message Functions ---

async function sendMessage(text = null) {
    const content = text || chatInput.value.trim();
    if (!content) return;

    // UI Updates
    if (welcomeScreen.style.display !== 'none') {
        welcomeScreen.style.display = 'none';
    }

    appendMessage('user', content);
    chatInput.value = '';
    chatInput.style.height = 'auto';

    // Check if we need to show the model switching state
    if (sendBtn.disabled) {
        alert("Please wait for the model to finish loading.");
        return;
    }

    const typingIndicator = appendTypingIndicator();
    // A new message always snaps to the bottom; reset the user-scroll flag so
    // the typing indicator and first tokens are visible.
    _userScrolledUp = false;
    scrollToBottom();

    // Toggle buttons
    sendBtn.style.display = 'none';
    stopBtn.style.display = 'flex';

    let requestChatId = currentChatId;
    abortController = new AbortController();

    // Hoist stream state so the finally block can flush any pending debounced render
    let streamRenderTimer = null;
    let assistantMessageDiv = null;
    let contentDiv = null;
    let fullContent = "";

    // Lock UI immediately for ALL generations
    document.querySelectorAll('#chat-history, .new-chat-btn, #add-model-btn').forEach(item => {
        item.style.pointerEvents = 'none';
        item.style.opacity = '0.5';
    });
    modelSelect.disabled = true;

    try {
        const response = await fetch(`${API_URL}/api/chat${requestChatId ? `?chat_id=${requestChatId}` : ''}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: content }),
            signal: abortController.signal
        });

        const reader = response.body.getReader();
        const decoder = new TextDecoder();

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            const lines = chunk.split('\n');

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6).trim();
                    if (dataStr === '[DONE]') continue;

                    try {
                        const data = JSON.parse(dataStr);
                        
                        if (data.rag_status) {
                            const rs = data.rag_status;
                            const el = document.getElementById('rag-status');
                            if (el) {
                                const end = Math.min(rs.offset + rs.limit, rs.total);
                                el.textContent = `Context: ${rs.offset + 1}-${end} / ${rs.total}`;
                                el.style.display = 'inline-block';
                            }
                        }

                        if (data.chat_id && !requestChatId) {
                            requestChatId = data.chat_id;
                            if (!currentChatId) currentChatId = data.chat_id;
                            loadChatHistory();
                        }
                        if (data.clear) {
                            fullContent = "";
                            if (contentDiv) contentDiv.innerHTML = "";
                            continue;
                        }
                        if (data.replace || data.content) {
                            if (data.replace) fullContent = data.replace;
                            if (data.content) fullContent += data.content;

                            if (currentChatId === requestChatId) {
                                if (!contentDiv || !document.contains(contentDiv)) {
                                    if (typingIndicator && document.contains(typingIndicator)) typingIndicator.remove();

                                    assistantMessageDiv = document.createElement('div');
                                    assistantMessageDiv.className = 'message assistant';
                                    contentDiv = document.createElement('div');
                                    contentDiv.className = 'message-content';
                                    assistantMessageDiv.appendChild(contentDiv);

                                    const actionsDiv = document.createElement('div');
                                    actionsDiv.className = 'message-actions';
                                    actionsDiv.style.cssText = 'margin-top: 5px; opacity: 0.5; display: flex; gap: 10px;';
                                    actionsDiv.innerHTML = `
                                        <button onclick="speakResponse(this.parentElement.previousElementSibling.textContent)" title="Read out loud" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="volume-2" style="width: 14px; height: 14px;"></i></button>
                                        <button onclick="stopSpeaking()" title="Stop speaking" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="square" style="width: 14px; height: 14px;"></i></button>
                                    `;
                                    assistantMessageDiv.appendChild(actionsDiv);
                                    messagesContainer.appendChild(assistantMessageDiv);
                                    // Scope icon creation to just the new actions div
                                    lucide.createIcons({ elements: Array.from(actionsDiv.querySelectorAll('[data-lucide]')) });
                                }
                                // Throttle markdown re-renders to ~20fps — only schedule a new render
                                // if one isn't already queued. This way tokens accumulate and the
                                // scheduled callback always reads the latest fullContent when it fires.
                                if (!streamRenderTimer) {
                                    streamRenderTimer = setTimeout(() => {
                                        streamRenderTimer = null;
                                        contentDiv.innerHTML = renderMarkdown(fullContent);
                                        lucide.createIcons({ elements: Array.from(contentDiv.querySelectorAll('[data-lucide]')) });
                                    }, 50);
                                }
                            }
                            continue;
                        }
                    } catch (e) {
                        console.error('Error parsing stream chunk:', e);
                    }
                }
            }
        }

        // Auto-speak if toggled
        if (autoSpeakToggle.checked && currentChatId === requestChatId) {
            speakResponse(fullContent);
        }

    } catch (error) {
        if (error.name === 'AbortError') {
            console.log('Generation aborted');
            if (typingIndicator) typingIndicator.remove();
        } else {
            if (typingIndicator) typingIndicator.remove();
            appendMessage('assistant', 'Sorry, I encountered an error. Please make sure the server is running with the MLX model.');
            console.error('Error:', error);
        }
    } finally {
        // Flush any pending debounced render so the final state is always displayed
        clearTimeout(streamRenderTimer);
        if (contentDiv && fullContent) {
            contentDiv.innerHTML = renderMarkdown(fullContent);
            lucide.createIcons({ elements: Array.from(contentDiv.querySelectorAll('[data-lucide]')) });
        }

        sendBtn.style.display = 'flex';
        stopBtn.style.display = 'none';
        abortController = null;

        // Release UI locks
        document.querySelectorAll('#chat-history, .new-chat-btn, #add-model-btn').forEach(item => {
            item.style.pointerEvents = 'auto';
            item.style.opacity = '1';
        });
        modelSelect.disabled = false;
    }
}

async function stopGeneration() {
    if (abortController) {
        abortController.abort();
    }
    await stopSpeaking();
}

async function stopSpeaking() {
    try {
        console.log("Requesting stop-say from server...");
        const res = await fetch(`${API_URL}/api/stop-say`, { method: 'POST' });
        const data = await res.json();
        console.log("Stop-say response:", data);
    } catch (err) {
        console.error('Error stopping speech:', err);
    }
}

function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    const formattedContent = renderMarkdown(content);
    div.innerHTML = `
        <div class="message-content">${formattedContent}</div>
        <div class="message-actions" style="margin-top: 5px; opacity: 0.5; display: flex; gap: 10px;">
            ${role === 'assistant' ? `
                <button onclick="speakResponse(this.parentElement.previousElementSibling.textContent)" title="Read out loud" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="volume-2" style="width: 14px; height: 14px;"></i></button>
                <button onclick="stopSpeaking()" title="Stop speaking" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="square" style="width: 14px; height: 14px;"></i></button>
            ` : ''}
        </div>
    `;
    messagesContainer.appendChild(div);
    lucide.createIcons({ elements: Array.from(div.querySelectorAll('[data-lucide]')) });
}

function appendTypingIndicator() {
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = `
        <div class="typing">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
    `;
    messagesContainer.appendChild(div);
    return div;
}

// Track whether the user has manually scrolled away from the bottom.
// Auto-scroll only fires when they are already near the bottom, so
// reading mid-response won't get interrupted.
let _userScrolledUp = false;

messagesContainer.addEventListener('scroll', () => {
    const distanceFromBottom =
        messagesContainer.scrollHeight -
        messagesContainer.scrollTop -
        messagesContainer.clientHeight;
    // Consider "at bottom" if within 10px (handles rounding & sub-pixel alignment)
    _userScrolledUp = distanceFromBottom > 10;
}, { passive: true });

function scrollToBottom(force = false) {
    if (force || !_userScrolledUp) {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

// --- Voice and Audio Functions ---

async function speakResponse(text) {
    try {
        await fetch(`${API_URL}/api/say`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text })
        });
    } catch (error) {
        console.error('Error in speech:', error);
    }
}

// Speech to Text (Web Speech API)
let recognition = null;
if ('webkitSpeechRecognition' in window) {
    recognition = new webkitSpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
        isRecording = true;
        voiceBtn.classList.add('recording');
    };

    recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        chatInput.value = transcript;
        sendMessage(transcript);
    };

    recognition.onerror = (event) => {
        console.error('Speech recognition error:', event.error);
        stopRecording();
    };

    recognition.onend = () => {
        stopRecording();
    };
}

function toggleRecording() {
    if (!recognition) {
        alert('Speech recognition is not supported in this browser.');
        return;
    }

    if (isRecording) {
        recognition.stop();
    } else {
        recognition.start();
    }
}

function stopRecording() {
    isRecording = false;
    voiceBtn.classList.remove('recording');
}

// --- Model Management Functions ---

async function loadModels() {
    try {
        const response = await fetch(`${API_URL}/api/models`);
        const models = await response.json();

        modelSelect.innerHTML = '';
        models.forEach(m => {
            const option = document.createElement('option');
            option.value = m.name;
            option.textContent = m.name.split('/').pop(); // Show only the model name
            option.selected = m.active;
            modelSelect.appendChild(option);

            if (m.active) {
                modelBadge.textContent = option.textContent;
            }
        });
    } catch (error) {
        console.error('Error loading models:', error);
    }
}

async function addNewModel() {
    const name = newModelInput.value.trim();
    if (!name) return;

    if (!name.includes('mlx-community')) {
        alert("Model must be from the 'mlx-community' organization on Hugging Face.");
        return;
    }

    const originalBadgeText = modelBadge.textContent;
    modelBadge.textContent = "Downloading...";
    modelBadge.style.opacity = "0.5";

    try {
        const response = await fetch(`${API_URL}/api/models`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        if (response.ok) {
            newModelInput.value = '';
            loadModels();
        } else {
            const data = await response.json();
            alert(data.detail || "Error adding model");
        }
    } catch (error) {
        console.error('Error adding model:', error);
    } finally {
        modelBadge.textContent = originalBadgeText;
        modelBadge.style.opacity = "1";
    }
}

async function switchModel(modelName) {
    sendBtn.disabled = true;
    chatInput.disabled = true;
    const originalBadgeText = modelBadge.textContent;

    // Animate the badge to show activity
    const setBadge = (text, pulse = true) => {
        modelBadge.textContent = text;
        modelBadge.style.opacity = pulse ? '0.6' : '1';
        modelBadge.style.fontStyle = pulse ? 'italic' : 'normal';
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

        // Consume the SSE progress stream
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
                        // Refresh the sidebar dropdown to highlight the new active model
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
        sendBtn.disabled = false;
        chatInput.disabled = false;
        modelBadge.style.fontStyle = 'normal';
    }
}

// --- Clipboard Functions ---

async function copyCode(elementId) {
    const codeElement = document.getElementById(elementId);
    if (!codeElement) return;

    const text = codeElement.innerText;
    const container = codeElement.closest('.code-container');
    const btn = container.querySelector('.copy-btn');

    try {
        await navigator.clipboard.writeText(text);

        // Visual feedback
        btn.classList.add('copied');

        // Find current icon (might be i or svg)
        let icon = btn.querySelector('i, svg');
        if (icon) {
            // Create a NEW i element to let Lucide swap it correctly
            const newIcon = document.createElement('i');
            newIcon.setAttribute('data-lucide', 'check');
            icon.replaceWith(newIcon);
            lucide.createIcons({ elements: [newIcon] });
        }

        setTimeout(() => {
            btn.classList.remove('copied');
            let currentIcon = btn.querySelector('i, svg');
            if (currentIcon) {
                const newIcon = document.createElement('i');
                newIcon.setAttribute('data-lucide', 'copy');
                currentIcon.replaceWith(newIcon);
                lucide.createIcons({ elements: [newIcon] });
            }
        }, 2000);
    } catch (err) {
        console.error('Failed to copy code:', err);
    }
}
