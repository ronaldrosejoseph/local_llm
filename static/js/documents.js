/**
 * Document upload — file attachment and processing UI.
 */

import { state, elements, API_URL } from './state.js';
import { appendMessage } from './chat.js';
import { loadChatHistory, startNewChat } from './sidebar.js';
import { showToast } from './toast.js';

export function initDocumentUpload() {
    elements.attachBtn.addEventListener('click', () => {
        elements.fileUpload.click();
    });

    elements.fileUpload.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        if (!state.currentChatId) {
            await startNewChat();
            state.currentChatId = crypto.randomUUID();
        }

        const formData = new FormData();
        formData.append('file', file);
        formData.append('chat_id', state.currentChatId);

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

                if (data.scanned_no_vision) {
                    showToast("This PDF appears to be a scanned document (no digital text). "
                        + "Switch to a Vision model (VLM) to read the content.", "warning", 0);
                }

                const msg = data.vision ? `[Attached Image: ${file.name}]` : `[Attached Document: ${file.name}]`;
                appendMessage('user', msg);
                elements.welcomeScreen.style.display = 'none';

                loadChatHistory();

                if (data.rag_status) {
                    const chatModule = await import('./chat.js');
                    chatModule.updateRagStatusUI(data.rag_status);
                }
            }
        } catch (err) {
            elements.attachmentContainer.style.display = 'none';
            showToast('Error uploading document: ' + err.message, "error");
        } finally {
            e.target.value = '';
        }
    });
}
