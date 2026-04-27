/**
 * Document upload — file attachment and processing UI.
 */

import { state, elements, API_URL } from './state.js';
import { appendMessage } from './chat.js';
import { loadChatHistory, startNewChat } from './sidebar.js';

export function initDocumentUpload() {
    elements.attachBtn.addEventListener('click', () => {
        elements.fileUpload.click();
    });

    elements.fileUpload.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (!state.currentChatId) {
            await startNewChat();
            state.currentChatId = crypto.randomUUID(); // optimistic
        }

        const formData = new FormData();
        formData.append('file', file);
        formData.append('chat_id', state.currentChatId);

        // Show loading UI
        elements.attachmentContainer.style.display = 'block';
        elements.attachmentName.textContent = `Uploading ${file.name}...`;

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
                elements.attachmentName.textContent = `${file.name} (${data.chunks} chunks)`;
                console.log("Document processed securely.");

                // 1. Immediately append to the chat UI
                const msg = data.vision ? `[Attached Image: ${file.name}]` : `[Attached Document: ${file.name}]`;
                appendMessage('user', msg);

                // 2. Hide welcome screen if this was the first action
                elements.welcomeScreen.style.display = 'none';

                // 3. Refresh chat history in sidebar
                loadChatHistory();

                // 4. Update RAG Slider immediately
                if (data.rag_status) {
                    const chatModule = await import('./chat.js');
                    chatModule.updateRagStatusUI(data.rag_status);
                }
            }
        } catch (err) {
            elements.attachmentContainer.style.display = 'none';
            alert('Error uploading document: ' + err.message);
        } finally {
            e.target.value = '';
        }
    });
}
