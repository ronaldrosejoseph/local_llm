/**
 * Chat functions — message sending, SSE stream parsing, message rendering.
 */

import { state, elements, API_URL } from './state.js';
import { renderMarkdown, scrollToBottom, highlightCode } from './utils.js';
import { loadChatHistory } from './sidebar.js';
import { speakResponse, stopSpeaking } from './speech.js';

// --- Send Message ---

export async function sendMessage(text = null) {
    const content = text || elements.chatInput.value.trim();
    if (!content) return;

    // UI Updates
    if (elements.welcomeScreen.style.display !== 'none') {
        elements.welcomeScreen.style.display = 'none';
    }

    appendMessage('user', content);
    elements.chatInput.value = '';
    elements.chatInput.style.height = 'auto';

    // Check if we need to show the model switching state
    if (elements.sendBtn.disabled) {
        alert("Please wait for the model to finish loading.");
        return;
    }

    const typingIndicator = appendTypingIndicator();
    state._userScrolledUp = false;
    scrollToBottom();

    // Toggle buttons
    elements.sendBtn.style.display = 'none';
    elements.stopBtn.style.display = 'flex';

    // Buttons to lock while generating
    const lockButtons = [
        elements.modelSelect,
        elements.chatInput,
        elements.attachBtn,
        elements.voiceBtn
    ];

    let requestChatId = state.currentChatId;
    state.abortController = new AbortController();

    // Hoist stream state
    let streamRenderTimer = null;
    let assistantMessageDiv = null;
    let contentDiv = null;
    let fullContent = "";

    // Lock UI immediately for ALL generations
    document.querySelectorAll('#chat-history, .new-chat-btn, #settings-open-btn').forEach(item => {
        item.style.pointerEvents = 'none';
        item.style.opacity = '0.5';
    });
    lockButtons.forEach(element => {
        if (element && typeof element.disabled !== 'undefined') {
            element.disabled = true;
        }
    });

    try {
        const ragStatusEl = document.getElementById('rag-status');
        if (ragStatusEl) ragStatusEl.style.display = 'none';

        const body = { message: content };
        if (!requestChatId) {
            body.system_prompt = elements.systemPromptInput.value.trim();
        }

        const response = await fetch(`${API_URL}/api/chat${requestChatId ? `?chat_id=${requestChatId}` : ''}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: state.abortController.signal
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
                            updateRagStatusUI(data.rag_status);
                        }

                        // Image model badge updates (during /imagine and /edit)
                        if (data.model_badge) {
                            if (!window._savedBadgeText) {
                                window._savedBadgeText = elements.modelBadge.textContent;
                            }
                            elements.modelBadge.textContent = data.model_badge;
                            elements.modelBadge.style.opacity = data.model_badge_pulse ? '0.6' : '1';
                            elements.modelBadge.style.fontStyle = data.model_badge_pulse ? 'italic' : 'normal';
                        }
                        if (data.model_badge_restore) {
                            if (window._savedBadgeText) {
                                elements.modelBadge.textContent = window._savedBadgeText;
                                window._savedBadgeText = null;
                            } else {
                                const activeOpt = elements.modelSelect.options[elements.modelSelect.selectedIndex];
                                if (activeOpt) elements.modelBadge.textContent = activeOpt.textContent;
                            }
                            elements.modelBadge.style.opacity = '1';
                            elements.modelBadge.style.fontStyle = 'normal';
                        }

                        if (data.chat_id && !requestChatId) {
                            requestChatId = data.chat_id;
                            if (!state.currentChatId) state.currentChatId = data.chat_id;
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

                            if (state.currentChatId === requestChatId) {
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
                                        <button onclick="copyToClipboard(this.parentElement.previousElementSibling.textContent, this)" title="Copy to clipboard" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="copy" style="width: 14px; height: 14px;"></i></button>
                                    `;
                                    assistantMessageDiv.appendChild(actionsDiv);
                                    elements.messagesContainer.appendChild(assistantMessageDiv);
                                    lucide.createIcons({ elements: Array.from(actionsDiv.querySelectorAll('[data-lucide]')) });
                                }
                                // Throttle markdown re-renders to ~20fps
                                if (!streamRenderTimer) {
                                    streamRenderTimer = setTimeout(() => {
                                        streamRenderTimer = null;
                                        contentDiv.innerHTML = renderMarkdown(fullContent);
                                        highlightCode(contentDiv);
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
        if (elements.autoSpeakToggle.checked && state.currentChatId === requestChatId) {
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
        // Flush any pending debounced render
        clearTimeout(streamRenderTimer);
        if (contentDiv && fullContent) {
            contentDiv.innerHTML = renderMarkdown(fullContent);
            highlightCode(contentDiv);
            lucide.createIcons({ elements: Array.from(contentDiv.querySelectorAll('[data-lucide]')) });
        }

        elements.sendBtn.style.display = 'flex';
        elements.stopBtn.style.display = 'none';
        state.abortController = null;

        // Safety-net: restore the model badge
        if (window._savedBadgeText) {
            elements.modelBadge.textContent = window._savedBadgeText;
            elements.modelBadge.style.opacity = '1';
            elements.modelBadge.style.fontStyle = 'normal';
            window._savedBadgeText = null;
        }

        // Release UI locks
        document.querySelectorAll('#chat-history, .new-chat-btn, #settings-open-btn').forEach(item => {
            item.style.pointerEvents = 'auto';
            item.style.opacity = '1';
        });
        lockButtons.forEach(element => {
            if (element && typeof element.disabled !== 'undefined') {
                element.disabled = false;
            }
        });

        // Auto-generate title if it's a new conversation
        if (elements.currentChatTitle.textContent === "New Conversation" && state.currentChatId === requestChatId && fullContent.trim().length > 0) {
            fetch(`${API_URL}/api/chats/${requestChatId}/generate-title`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.title) {
                        elements.currentChatTitle.textContent = data.title;
                        loadChatHistory();
                    }
                })
                .catch(err => console.error("Could not auto-generate title:", err));
        }
    }
}

// --- Stop Generation ---

export async function stopGeneration() {
    if (state.abortController) {
        state.abortController.abort();
    }
    await stopSpeaking();
}

// --- Message Rendering ---

export function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    const formattedContent = renderMarkdown(content);
    div.innerHTML = `
        <div class="message-content">${formattedContent}</div>
        <div class="message-actions" style="margin-top: 5px; opacity: 0.5; display: flex; gap: 10px;">
            ${role === 'assistant' ? `
                <button onclick="speakResponse(this.parentElement.previousElementSibling.textContent)" title="Read out loud" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="volume-2" style="width: 14px; height: 14px;"></i></button>
                <button onclick="stopSpeaking()" title="Stop speaking" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="square" style="width: 14px; height: 14px;"></i></button>
                <button onclick="copyToClipboard(this.parentElement.previousElementSibling.textContent, this)" title="Copy to clipboard" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="copy" style="width: 14px; height: 14px;"></i></button>
            ` : ''}
        </div>
    `;
    elements.messagesContainer.appendChild(div);
    const contentContainer = div.querySelector('.message-content');
    highlightCode(contentContainer);
    lucide.createIcons({ elements: Array.from(div.querySelectorAll('[data-lucide]')) });
}

export function appendTypingIndicator() {
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = `
        <div class="typing">
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
            <div class="typing-dot"></div>
        </div>
    `;
    elements.messagesContainer.appendChild(div);
    return div;
}

export function updateRagStatusUI(rs) {
    const container = document.getElementById('rag-status-container');
    const textEl = document.getElementById('rag-status-text');
    const slider = document.getElementById('rag-slider');
    const toggle = document.getElementById('rag-search-toggle');

    if (container && textEl && slider) {
        if (rs.total > 0) {
            slider.min = 0;
            slider.max = Math.max(0, rs.total - 1);
            slider.step = rs.limit;
            slider.value = rs.offset;

            const end = Math.min(rs.offset + rs.limit, rs.total);
            const prefix = rs.search_mode ? `Search "${rs.search_query}": ` : "Context: ";
            textEl.textContent = `${prefix}${rs.offset + 1}-${end} / ${rs.total}`;
            container.style.display = 'flex';

            if (toggle) {
                toggle.style.background = rs.search_mode ? 'rgba(85,170,255,0.2)' : 'none';
                toggle.style.border = rs.search_mode ? '1px solid rgba(85,170,255,0.4)' : 'none';
                toggle.innerHTML = `<i data-lucide="${rs.search_mode ? 'search-x' : 'search'}" style="width: 14px; height: 14px;"></i>`;
                lucide.createIcons({ elements: [toggle.querySelector('[data-lucide]')] });
                toggle.title = rs.search_mode ? "Clear Search" : "Toggle Similarity Search";
            }
        } else {
            container.style.display = 'none';
        }
    }
}
