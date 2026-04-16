/**
 * Utility functions — markdown rendering, clipboard, scroll management.
 */

import { state, elements } from './state.js';

// Helper to strip common LLM control tokens from the UI
export const stripControlTokens = (text) => {
    if (!text) return "";
    return text
        .replace(/<\|im_end\|>/g, '')
        .replace(/<\|im_start\|>/g, '')
        .replace(/<\|endoftext\|>/g, '')
        .trim();
};

// DOMPurify config: allow `id` on code elements and `onclick` on copy buttons.
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
export function renderMarkdown(content) {
    if (!content) return "";
    const clean = stripControlTokens(content);
    const html = marked.parse(clean);
    return window.DOMPurify ? DOMPurify.sanitize(html, DOMPURIFY_CONFIG) : html;
}

// Configure Marked.js — custom code block renderer with copy buttons
export function initMarked() {
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
                const safeLang = (lang || 'text').replace(/[^a-z0-9-]/gi, '');
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
}

// --- Scroll Management ---

export function scrollToBottom(force = false) {
    if (force || !state._userScrolledUp) {
        elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
    }
}

export function initScrollTracking() {
    elements.messagesContainer.addEventListener('scroll', () => {
        const distanceFromBottom =
            elements.messagesContainer.scrollHeight -
            elements.messagesContainer.scrollTop -
            elements.messagesContainer.clientHeight;
        state._userScrolledUp = distanceFromBottom > 10;
    }, { passive: true });
}

// --- Clipboard Functions ---

export async function copyCode(elementId) {
    const codeElement = document.getElementById(elementId);
    if (!codeElement) return;

    const text = codeElement.innerText;
    const container = codeElement.closest('.code-container');
    const btn = container.querySelector('.copy-btn');

    try {
        await navigator.clipboard.writeText(text);

        btn.classList.add('copied');

        let icon = btn.querySelector('i, svg');
        if (icon) {
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

export async function copyToClipboard(text, btn) {
    try {
        await navigator.clipboard.writeText(text);

        let icon = btn.querySelector('i, svg');
        if (icon) {
            const originalIconName = icon.getAttribute('data-lucide') || 'copy';
            const newIcon = document.createElement('i');
            newIcon.setAttribute('data-lucide', 'check');
            newIcon.style.cssText = 'width: 14px; height: 14px; color: #50fa7b;';
            icon.replaceWith(newIcon);
            lucide.createIcons({ elements: [newIcon] });

            setTimeout(() => {
                const backIcon = document.createElement('i');
                backIcon.setAttribute('data-lucide', originalIconName);
                backIcon.style.cssText = 'width: 14px; height: 14px;';
                newIcon.replaceWith(backIcon);
                lucide.createIcons({ elements: [backIcon] });
            }, 2000);
        }
    } catch (err) {
        console.error('Failed to copy text:', err);
    }
}
