/**
 * Chat functions — message sending, SSE stream parsing, message rendering.
 */

import { state, elements, API_URL } from './state.js';
import { renderMarkdown, scrollToBottom, highlightCode, extractThinking } from './utils.js';
import { loadChatHistory } from './sidebar.js';
import { speakResponse, stopSpeaking } from './speech.js';
import { showToast } from './toast.js';

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
        showToast("Please wait for the model to finish loading.", "warning");
        return;
    }

    // Check if current model is a thinking model
    const activeOpt = elements.modelSelect.options[elements.modelSelect.selectedIndex];
    const isThinkingModel = activeOpt && activeOpt.dataset.hasThinking === '1';
    const typingIndicator = appendTypingIndicator(isThinkingModel);
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
    const isNewChat = !requestChatId;
    state.abortController = new AbortController();

    // Hoist stream state
    let streamRenderTimer = null;
    let assistantMessageDiv = null;
    let contentDiv = null;
    let actionsDiv = null;
    let fullContent = "";
    let tokenCount = 0;
    let genStartTime = 0;  // set on first token — excludes prefill/thinking time
    let serverStats = null;  // populated from SSE gen_stats event (more accurate)

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

                        if (data.model_crash) {
                            // Remove typing indicator — model is gone
                            if (typingIndicator && document.contains(typingIndicator)) {
                                typingIndicator.remove();
                            }
                            // Build toast message with crash diagnostics
                            let crashMsg = `Model process crashed.\n\n`;
                            if (data.detail) {
                                crashMsg += `${data.detail}\n\n`;
                            }
                            crashMsg += `Falling back to ${data.fallback_model_display}.`;
                            showToast(crashMsg, "error", 0);  // never auto-close
                            // Update badge from SSE data (always correct)
                            elements.modelBadge.textContent = data.fallback_model_display;
                            elements.modelBadge.style.opacity = '1';
                            elements.modelBadge.style.fontStyle = 'normal';
                            // Update select dropdown to show fallback model as selected
                            const fallbackFull = data.fallback_model;
                            let found = false;
                            for (const opt of elements.modelSelect.options) {
                                opt.selected = (opt.value === fallbackFull);
                                if (opt.value === fallbackFull) found = true;
                            }
                            // If fallback not in dropdown, add it
                            if (!found && fallbackFull) {
                                const opt = document.createElement('option');
                                opt.value = fallbackFull;
                                opt.textContent = data.fallback_model_display;
                                opt.selected = true;
                                elements.modelSelect.appendChild(opt);
                            }
                        }

                        if (data.gen_stats) {
                            serverStats = data.gen_stats;
                            continue;
                        }

                        if (data.thinking_start) {
                            // Remove typing indicator, create thinking section
                            if (typingIndicator && document.contains(typingIndicator)) typingIndicator.remove();
                            if (!contentDiv || !document.contains(contentDiv)) {
                                assistantMessageDiv = document.createElement('div');
                                assistantMessageDiv.className = 'message assistant';
                                const thinkingDiv = document.createElement('div');
                                thinkingDiv.className = 'message-thinking thinking-active';
                                thinkingDiv.id = `thinking-${requestChatId || 'new'}`;
                                thinkingDiv.innerHTML = `
                                    <div class="thinking-header" onclick="this.parentElement.classList.toggle('collapsed')">
                                        <i data-lucide="brain"></i>
                                        <span>Thinking...</span>
                                        <i data-lucide="chevron-down" class="thinking-chevron"></i>
                                    </div>
                                    <div class="thinking-body"></div>`;
                                assistantMessageDiv.appendChild(thinkingDiv);
                                contentDiv = document.createElement('div');
                                contentDiv.className = 'message-content';
                                contentDiv.style.display = 'none';
                                assistantMessageDiv.appendChild(contentDiv);
                                actionsDiv = document.createElement('div');
                                actionsDiv.className = 'message-actions';
                                actionsDiv.style.cssText = 'margin-top: 5px; opacity: 0.5; display: flex; gap: 10px; align-items: center;';
                                actionsDiv.innerHTML = `
                                    <button onclick="speakResponse(this.parentElement.previousElementSibling.textContent)" title="Read out loud" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="volume-2" style="width: 14px; height: 14px;"></i></button>
                                    <button onclick="stopSpeaking()" title="Stop speaking" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="square" style="width: 14px; height: 14px;"></i></button>
                                    <button onclick="copyToClipboard(this.parentElement.previousElementSibling.textContent, this)" title="Copy to clipboard" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="copy" style="width: 14px; height: 14px;"></i></button>`;
                                assistantMessageDiv.appendChild(actionsDiv);
                                elements.messagesContainer.appendChild(assistantMessageDiv);
                                lucide.createIcons({ elements: Array.from(assistantMessageDiv.querySelectorAll('[data-lucide]')) });
                            }
                            continue;
                        }

                        if (data.thinking) {
                            const thinkingDiv = assistantMessageDiv && assistantMessageDiv.querySelector('.message-thinking');
                            if (thinkingDiv) {
                                const body = thinkingDiv.querySelector('.thinking-body');
                                if (body) body.textContent += data.thinking;
                                // Strip tags for display — collapse closed XML tags
                                const cleanDisplay = body.textContent
                                    .replace(/<[^>]*>/g, '')
                                    .trim();
                                body.textContent = cleanDisplay;
                            }
                            continue;
                        }

                        if (data.thinking_done) {
                            const thinkingDiv = assistantMessageDiv && assistantMessageDiv.querySelector('.message-thinking');
                            if (thinkingDiv) {
                                thinkingDiv.classList.remove('thinking-active');
                                thinkingDiv.classList.add('collapsed');
                                const span = thinkingDiv.querySelector('.thinking-header span');
                                if (span) span.textContent = 'Thought';
                                const body = thinkingDiv.querySelector('.thinking-body');
                                if (body && body.textContent.trim()) {
                                    body.innerHTML = renderMarkdown(body.textContent);
                                }
                                lucide.createIcons({ elements: Array.from(thinkingDiv.querySelectorAll('[data-lucide]')) });
                            }
                            if (contentDiv) contentDiv.style.display = '';
                            continue;
                        }

                        if (data.chat_id && !requestChatId) {
                            requestChatId = data.chat_id;
                            if (!state.currentChatId) state.currentChatId = data.chat_id;
                            loadChatHistory();
                        }
                        if (data.clear) {
                            fullContent = "";
                            tokenCount = 0;
                            genStartTime = 0;
                            if (contentDiv) { contentDiv.innerHTML = ""; contentDiv.style.display = ''; }
                            const thinkingDiv = assistantMessageDiv && assistantMessageDiv.querySelector('.message-thinking');
                            if (thinkingDiv) { thinkingDiv.style.display = 'none'; thinkingDiv.innerHTML = ''; thinkingDiv.classList.remove('thinking-active'); }
                            continue;
                        }
                        if (data.replace || data.content) {
                            if (data.replace) {
                                fullContent = data.replace;
                                tokenCount = 0;
                                genStartTime = 0;
                            }
                            if (data.content) {
                                if (!genStartTime) genStartTime = performance.now();
                                fullContent += data.content;
                                tokenCount++;
                            }

                            if (state.currentChatId === requestChatId) {
                                if (!contentDiv || !document.contains(contentDiv)) {
                                    if (typingIndicator && document.contains(typingIndicator)) typingIndicator.remove();

                                    assistantMessageDiv = document.createElement('div');
                                    assistantMessageDiv.className = 'message assistant';
                                    contentDiv = document.createElement('div');
                                    contentDiv.className = 'message-content';
                                    assistantMessageDiv.appendChild(contentDiv);

                                    actionsDiv = document.createElement('div');
                                    actionsDiv.className = 'message-actions';
                                    actionsDiv.style.cssText = 'margin-top: 5px; opacity: 0.5; display: flex; gap: 10px; align-items: center;';
                                    // Skip speak/copy buttons for image generation results
                                    const isImageGen = fullContent.trim().startsWith('![');
                                    if (!isImageGen) {
                                        actionsDiv.innerHTML = `
                                            <button onclick="speakResponse(this.parentElement.previousElementSibling.textContent)" title="Read out loud" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="volume-2" style="width: 14px; height: 14px;"></i></button>
                                            <button onclick="stopSpeaking()" title="Stop speaking" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="square" style="width: 14px; height: 14px;"></i></button>
                                            <button onclick="copyToClipboard(this.parentElement.previousElementSibling.textContent, this)" title="Copy to clipboard" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="copy" style="width: 14px; height: 14px;"></i></button>
                                        `;
                                    }
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
            appendMessage('assistant', 'Sorry, I encountered an error. Please restart the server/app and try again.');
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

        // Generation stats — prefer server-side timing (excludes network jitter)
        if (actionsDiv && fullContent) {
            let tokens, timeS, tps;
            if (serverStats && serverStats.tokens > 0) {
                tokens = serverStats.tokens;
                timeS = serverStats.time_s;
                tps = serverStats.tps;
            } else if (tokenCount > 0 && genStartTime > 0) {
                tokens = tokenCount;
                timeS = (performance.now() - genStartTime) / 1000;
                tps = timeS > 0 ? (tokenCount / timeS) : 0;
                timeS = Math.round(timeS * 10) / 10;
                tps = Math.round(tps * 10) / 10;
            }
            if (tokens > 0) {
                const timeStr = timeS >= 1
                    ? `${timeS.toFixed(1)}s`
                    : `${Math.round(timeS * 1000)}ms`;
                const oldStats = actionsDiv.querySelector('.message-stats');
                if (oldStats) oldStats.remove();
                const statsSpan = document.createElement('span');
                statsSpan.className = 'message-stats';
                statsSpan.textContent = `${tokens} tokens · ${timeStr} · ${tps.toFixed(1)} t/s`;
                actionsDiv.appendChild(statsSpan);
            }
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
        elements.chatInput.focus();

        // Auto-generate/Refine title (First turn + every 3 turns)
        const userMsgCount = elements.messagesContainer.querySelectorAll('.message.user').length;
        const isFirstTurn = isNewChat || elements.currentChatTitle.textContent === "New Conversation";
        const shouldRefine = isFirstTurn || (userMsgCount > 1 && userMsgCount % 3 === 0);

        if (shouldRefine && state.currentChatId === requestChatId && fullContent.trim().length > 0) {
            fetch(`${API_URL}/api/chats/${requestChatId}/generate-title`, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if (data.title) {
                        if (state.currentChatId === requestChatId) {
                            elements.currentChatTitle.textContent = data.title;
                        }
                        // Update sidebar item directly
                        const item = document.querySelector(`.history-item[data-chat-id="${requestChatId}"]`);
                        if (item) {
                            const contentDiv = item.querySelector('.history-item-content');
                            if (contentDiv) {
                                const textNode = [...contentDiv.childNodes].find(n => n.nodeType === Node.TEXT_NODE);
                                if (textNode) textNode.textContent = data.title;
                            }
                        }
                        loadChatHistory();
                    }
                })
                .catch(err => console.error("Could not refine title:", err));
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

export function appendMessage(role, content, stats = null) {
    const div = document.createElement('div');
    div.className = `message ${role}`;

    // For assistant messages with stored thinking, show collapsible section
    let thinkingHtml = '';
    let displayContent = content;
    if (role === 'assistant' && stats && stats.thinking_content) {
        const { thinking, content: cleanContent } = extractThinking(stats.thinking_content);
        if (thinking) {
            thinkingHtml = `
                <div class="message-thinking collapsed">
                    <div class="thinking-header" onclick="this.parentElement.classList.toggle('collapsed')">
                        <i data-lucide="brain"></i>
                        <span>Thought</span>
                        <i data-lucide="chevron-down" class="thinking-chevron"></i>
                    </div>
                    <div class="thinking-body">${renderMarkdown(thinking)}</div>
                </div>`;
            displayContent = cleanContent || content;
        }
    }

    let statsHtml = '';
    if (stats && stats.token_count > 0) {
        const elapsedS = (stats.generation_time_ms || 0) / 1000;
        const tps = elapsedS > 0 ? (stats.token_count / elapsedS) : 0;
        const timeStr = elapsedS >= 1
            ? `${elapsedS.toFixed(1)}s`
            : `${Math.round(elapsedS * 1000)}ms`;
        statsHtml = `<span class="message-stats">${stats.token_count} tokens · ${timeStr} · ${tps.toFixed(1)} t/s</span>`;
    }

    div.innerHTML = `
        ${thinkingHtml}
        <div class="message-content">${renderMarkdown(displayContent)}</div>
        <div class="message-actions" style="margin-top: 5px; opacity: 0.5; display: flex; gap: 10px; align-items: center;">
            ${role === 'assistant' ? `
                <button onclick="speakResponse(this.parentElement.previousElementSibling.textContent)" title="Read out loud" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="volume-2" style="width: 14px; height: 14px;"></i></button>
                <button onclick="stopSpeaking()" title="Stop speaking" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="square" style="width: 14px; height: 14px;"></i></button>
                <button onclick="copyToClipboard(this.parentElement.previousElementSibling.textContent, this)" title="Copy to clipboard" style="background:none; border:none; color:inherit; cursor:pointer; font-size: 12px; display: flex; align-items: center;"><i data-lucide="copy" style="width: 14px; height: 14px;"></i></button>
            ` : ''}
            ${statsHtml}
        </div>
    `;
    elements.messagesContainer.appendChild(div);
    const contentContainer = div.querySelector('.message-content');
    highlightCode(contentContainer);
    lucide.createIcons({ elements: Array.from(div.querySelectorAll('[data-lucide]')) });
}

export function appendTypingIndicator(isThinking = false) {
    const div = document.createElement('div');
    div.className = 'message assistant';
    if (isThinking) {
        div.innerHTML = `
            <div class="typing thinking-indicator">
                <i data-lucide="brain" style="width:16px;height:16px;color:#a78bfa;"></i>
                <span>Thinking</span>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>`;
    } else {
        div.innerHTML = `
            <div class="typing">
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
                <div class="typing-dot"></div>
            </div>`;
    }
    elements.messagesContainer.appendChild(div);
    lucide.createIcons({ elements: Array.from(div.querySelectorAll('[data-lucide]')) });
    return div;
}

export function updateRagStatusUI(rs) {
    const container = document.getElementById('rag-status-container');
    const textEl = document.getElementById('rag-status-text');
    const slider = document.getElementById('rag-slider');
    const toggle = document.getElementById('rag-search-toggle');

    if (container && textEl && slider) {
        if (rs.total > 0) {
            // Only show slider if there's more than one batch to navigate
            slider.style.display = rs.total <= rs.limit ? 'none' : 'block';

            slider.min = 0;
            // Fix: Calculate max to be exactly the last valid step start
            slider.max = Math.floor((rs.total - 1) / rs.limit) * rs.limit;
            slider.step = rs.limit;
            slider.value = rs.offset;

            const end = Math.min(rs.offset + rs.limit, rs.total);
            let prefix = "Context: ";
            if (rs.search_mode) {
                prefix = `Search "${rs.search_query}": `;
            } else if (rs.is_vision) {
                prefix = "Pages: ";
            }

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
