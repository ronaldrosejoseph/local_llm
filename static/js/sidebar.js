/**
 * Sidebar functions — chat history, navigation, delete modals, sidebar toggle.
 */

import { state, elements, API_URL } from './state.js';
import { renderMarkdown, scrollToBottom } from './utils.js';

// --- Chat History ---

export async function loadChatHistory() {
    try {
        const response = await fetch(`${API_URL}/api/chats`);
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const chats = await response.json();

        elements.chatHistory.innerHTML = '';
        chats.forEach(chat => {
            const item = document.createElement('div');
            item.className = 'history-item';
            item.dataset.chatId = chat.id;
            if (chat.id === state.currentChatId) item.classList.add('active');
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
            elements.chatHistory.appendChild(item);
        });
        lucide.createIcons({ elements: Array.from(elements.chatHistory.querySelectorAll('[data-lucide]')) });
    } catch (error) {
        console.error('Error loading history:', error);
    }
}

export async function loadChat(chatId, title) {
    state.currentChatId = chatId;
    elements.currentChatTitle.textContent = title;
    elements.welcomeScreen.style.display = 'none';
    elements.messagesContainer.innerHTML = '';

    // Clear pending structural attachments instantly on switch
    elements.attachmentContainer.style.display = 'none';
    elements.fileUpload.value = '';

    // Apply active class in-place
    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.toggle('active', item.dataset.chatId === chatId);
    });

    try {
        const response = await fetch(`${API_URL}/api/chats/${chatId}/messages`);
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const messages = await response.json();

        // Import appendMessage to avoid circular dep
        const { appendMessage } = await import('./chat.js');
        messages.forEach(msg => {
            appendMessage(msg.role, msg.content);
        });

        // Hide RAG info on load
        const ragStatus = document.getElementById('rag-status');
        if (ragStatus) ragStatus.style.display = 'none';

        scrollToBottom(true);
        closeSidebar();
    } catch (error) {
        console.error('Error loading chat:', error);
    }
}

export async function startNewChat() {
    state.currentChatId = null;
    elements.currentChatTitle.textContent = 'New Conversation';
    elements.messagesContainer.innerHTML = '';
    elements.welcomeScreen.style.display = 'flex';

    // Clear pending structural attachments
    elements.attachmentContainer.style.display = 'none';
    elements.fileUpload.value = '';

    // Reset RAG badge
    const ragStatus = document.getElementById('rag-status');
    if (ragStatus) ragStatus.style.display = 'none';

    loadChatHistory();
    closeSidebar();
    elements.chatInput.value = '';
    elements.chatInput.focus();

    document.querySelectorAll('.history-item').forEach(item => {
        item.classList.remove('active');
    });
}

// --- Delete Chat ---

export async function deleteChat(chatId) {
    showDeleteModal(() => {
        performDelete(chatId);
    });
}

async function performDelete(chatId) {
    try {
        await fetch(`${API_URL}/api/chats/${chatId}`, { method: 'DELETE' });
        if (state.currentChatId === chatId) startNewChat();
        loadChatHistory();
        closeSidebar();
    } catch (err) {
        console.error('Error deleting chat:', err);
    }
}

// --- Delete Modal ---

export function showDeleteModal(onConfirm) {
    state.deleteCallback = onConfirm;
    elements.modalContainer.style.display = 'flex';
    setTimeout(() => {
        elements.modalContainer.classList.add('active');
    }, 10);
}

export function hideDeleteModal() {
    elements.modalContainer.classList.remove('active');
    setTimeout(() => {
        elements.modalContainer.style.display = 'none';
        state.deleteCallback = null;
    }, 300);
}

// --- Sidebar Toggle ---

export function toggleSidebar() {
    elements.sidebar.classList.toggle('active');
    elements.sidebarOverlay.classList.toggle('active');
}

export function closeSidebar() {
    elements.sidebar.classList.remove('active');
    elements.sidebarOverlay.classList.remove('active');
}
