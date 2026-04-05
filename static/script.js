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

// Function to safely render markdown with HTML escaping and sanitization
function renderMarkdown(content) {
    if (!content) return "";
    const clean = stripControlTokens(content);
    const html = marked.parse(clean);
    return window.DOMPurify ? DOMPurify.sanitize(html) : html;
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
            // We MUST escape code content because we are building a string for innerHTML
            const escaped = text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
                
            return `
                <div class="code-container">
                    <button class="copy-btn" onclick="copyCode('${id}')" title="Copy to clipboard">
                        <i data-lucide="copy"></i>
                    </button>
                    <pre><code id="${id}" class="language-${lang || 'text'}">${escaped}</code></pre>
                </div>
            `;
        }
    }
});

// Load history on startup
// Load data on startup
document.addEventListener('DOMContentLoaded', () => {
    loadChatHistory();
    loadModels();
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

const fileUpload = document.getElementById('file-upload');
const attachBtn = document.getElementById('attach-btn');
const attachmentContainer = document.getElementById('attachment-pill-container');
const attachmentName = document.getElementById('attachment-name');

// Toggle sidebar on mobile
menuToggle.addEventListener('click', toggleSidebar);
sidebarOverlay.addEventListener('click', closeSidebar);

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
        const data = await res.json();
        
        if (data.status === 'ok') {
            attachmentName.textContent = `${file.name} (${data.chunks} chunks)`;
            console.log("Document processed securely.");
            lucide.createIcons();
        } else {
            throw new Error(data.detail || "Upload failed");
        }
    } catch (err) {
        attachmentContainer.style.display = 'none';
        alert('Error uploading document: ' + err.message);
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
        const chats = await response.json();
        
        chatHistory.innerHTML = '';
        chats.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'history-item';
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
        lucide.createIcons();
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
    
    // Highlight active chat in sidebar
    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.remove('active');
    });
    
    try {
        const response = await fetch(`${API_URL}/api/chats/${chatId}/messages`);
        const messages = await response.json();
        
        messages.forEach(msg => {
            appendMessage(msg.role, msg.content);
        });
        
        // Mark current active item
        loadChatHistory(); // This will refresh and apply active class
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
    scrollToBottom();
    
    // Toggle buttons
    sendBtn.style.display = 'none';
    stopBtn.style.display = 'flex';
    
    abortController = new AbortController();
    
    try {
        const response = await fetch(`${API_URL}/api/chat${currentChatId ? `?chat_id=${currentChatId}` : ''}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: content }),
            signal: abortController.signal
        });
        
        let assistantMessageDiv = null;
        let contentDiv = null;
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullContent = "";
        
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
                        if (data.chat_id && !currentChatId) {
                            currentChatId = data.chat_id;
                            loadChatHistory();
                        }
                        if (data.clear) {
                            fullContent = "";
                            if (contentDiv) contentDiv.innerHTML = "";
                            continue;
                        }
                        if (data.replace) {
                            if (!contentDiv) {
                                if (typingIndicator) typingIndicator.remove();
                                assistantMessageDiv = document.createElement('div');
                                assistantMessageDiv.className = 'message assistant';
                                contentDiv = document.createElement('div');
                                contentDiv.className = 'message-content';
                                assistantMessageDiv.appendChild(contentDiv);
                                messagesContainer.appendChild(assistantMessageDiv);
                            }
                            fullContent = data.replace;
                            contentDiv.innerHTML = renderMarkdown(fullContent);
                            continue;
                        }
                        if (data.content) {
                            if (!contentDiv) {
                                if (typingIndicator) typingIndicator.remove();
                                
                                // Create assistant message bubble
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
                                lucide.createIcons();
                            }
                            fullContent += data.content;
                            contentDiv.innerHTML = renderMarkdown(fullContent);
                            // scrollToBottom(); // Disabled auto-scroll during generation
                        }
                    } catch (e) {
                        console.error('Error parsing stream chunk:', e);
                    }
                }
            }
        }
        
        // Finalizing UI
        lucide.createIcons();
        
        // Auto-speak if toggled
        if (autoSpeakToggle.checked) {
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
        sendBtn.style.display = 'flex';
        stopBtn.style.display = 'none';
        abortController = null;
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
    lucide.createIcons();
    scrollToBottom();
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

function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
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
    // UI Feedback for switching
    sendBtn.disabled = true;
    chatInput.disabled = true;
    const originalBadgeText = modelBadge.textContent;
    modelBadge.textContent = "Loading...";
    modelBadge.style.opacity = "0.5";
    
    try {
        const response = await fetch(`${API_URL}/api/models/active`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: modelName })
        });
        
        if (response.ok) {
            const data = await response.json();
            modelBadge.textContent = data.current_model.split('/').pop();
            modelBadge.style.opacity = "1";
            console.log(`Switched to ${data.current_model}`);
        } else {
            const data = await response.json();
            alert(data.detail || "Error switching model");
            modelBadge.textContent = originalBadgeText;
            modelBadge.style.opacity = "1";
        }
    } catch (error) {
        console.error('Error switching model:', error);
        modelBadge.textContent = originalBadgeText;
        modelBadge.style.opacity = "1";
    } finally {
        sendBtn.disabled = false;
        chatInput.disabled = false;
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
            lucide.createIcons();
        }
        
        setTimeout(() => {
            btn.classList.remove('copied');
            let currentIcon = btn.querySelector('i, svg');
            if (currentIcon) {
                const newIcon = document.createElement('i');
                newIcon.setAttribute('data-lucide', 'copy');
                currentIcon.replaceWith(newIcon);
                lucide.createIcons();
            }
        }, 2000);
    } catch (err) {
        console.error('Failed to copy code:', err);
    }
}
