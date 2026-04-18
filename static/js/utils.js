/**
 * Utility functions — markdown rendering, clipboard, scroll management.
 */

import { state, elements } from './state.js';

// LaTeX symbol mapping for Unicode conversion
const LATEX_SYMBOLS = {
    // Arrows
    '\\to': '→', '\\rightarrow': '→',
    '\\leftarrow': '←', '\\leftrightarrow': '↔',
    '\\Rightarrow': '⇒', '\\Leftarrow': '⇐', '\\Leftrightarrow': '⇔',
    '\\longrightarrow': '⟶', '\\Longrightarrow': '⟹',
    '\\longleftarrow': '⟵', '\\Longleftarrow': '⟸',
    '\\longleftrightarrow': '⟷', '\\Longleftrightarrow': '⟺',
    '\\mapsto': '↦',
    '\\uparrow': '↑', '\\downarrow': '↓', '\\updownarrow': '↕',
    '\\nearrow': '↗', '\\searrow': '↘', '\\swarrow': '↙', '\\nwarrow': '↖',

    // Logic / Proof
    '\\therefore': '∴', '\\because': '∵',
    '\\implies': '⇒', '\\impliedby': '⇐', '\\iff': '⇔',
    '\\neg': '¬', '\\land': '∧', '\\lor': '∨',
    '\\vdash': '⊢', '\\models': '⊨',

    // Basic Operators
    '\\times': '×', '\\div': '÷', '\\pm': '±', '\\mp': '∓',
    '\\cdot': '·', '\\ast': '∗', '\\star': '⋆',
    '\\le': '≤', '\\leq': '≤', '\\ge': '≥', '\\geq': '≥',
    '\\neq': '≠', '\\approx': '≈', '\\equiv': '≡',
    '\\cong': '≅', '\\propto': '∝',

    // Set Theory
    '\\in': '∈', '\\notin': '∉', '\\ni': '∋',
    '\\subset': '⊂', '\\supset': '⊃',
    '\\subseteq': '⊆', '\\supseteq': '⊇',
    '\\cap': '∩', '\\cup': '∪', '\\setminus': '∖',
    '\\emptyset': '∅',

    // Calculus / Algebra
    '\\partial': '∂', '\\nabla': '∇',
    '\\sum': '∑', '\\prod': '∏', '\\int': '∫',
    '\\infty': '∞', '\\surd': '√',

    // Geometry
    '\\angle': '∠', '\\degree': '°',
    '\\perp': '⊥', '\\parallel': '∥',

    // Greek (Lowercase)
    '\\alpha': 'α', '\\beta': 'β', '\\gamma': 'γ', '\\delta': 'δ',
    '\\epsilon': 'ε', '\\zeta': 'ζ', '\\eta': 'η', '\\theta': 'θ',
    '\\iota': 'ι', '\\kappa': 'κ', '\\lambda': 'λ', '\\mu': 'μ',
    '\\nu': 'ν', '\\xi': 'ξ', '\\pi': 'π', '\\rho': 'ρ',
    '\\sigma': 'σ', '\\tau': 'τ', '\\upsilon': 'υ',
    '\\phi': 'φ', '\\chi': 'χ', '\\psi': 'ψ', '\\omega': 'ω',

    // Greek (Uppercase)
    '\\Gamma': 'Γ', '\\Delta': 'Δ', '\\Theta': 'Θ', '\\Lambda': 'Λ',
    '\\Xi': 'Ξ', '\\Pi': 'Π', '\\Sigma': 'Σ', '\\Upsilon': 'Υ',
    '\\Phi': 'Φ', '\\Psi': 'Ψ', '\\Omega': 'Ω',

    // Functions (commonly used)
    '\\log': 'log',
    '\\ln': 'ln',
    '\\sin': 'sin', '\\cos': 'cos', '\\tan': 'tan',
    '\\csc': 'csc', '\\sec': 'sec', '\\cot': 'cot',
    '\\exp': 'exp',

    // Delimiters
    '\\langle': '⟨', '\\rangle': '⟩',
    '\\lceil': '⌈', '\\rceil': '⌉',
    '\\lfloor': '⌊', '\\rfloor': '⌋',

    // Modulo
    '\\mod': 'mod',
    '\\bmod': 'mod',
    '\\pmod': '(mod ',
    '\\pod': ' (mod ',

    // Misc
    '\\bullet': '•', '\\circ': '◦',
    '\\aleph': 'ℵ',
    '\\quad': ' ',
    '\\qquad': '  ',
};

// Unicode maps for superscripts and subscripts
const SUPER_MAP = {
    '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴', '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
    '+': '⁺', '-': '⁻', '=': '⁼', '(': '⁽', ')': '⁾', 'n': 'ⁿ', 'i': 'ⁱ', 'x': 'ˣ', 'y': 'ʸ', 'z': 'ᶻ',
    'a': 'ᵃ', 'b': 'ᵇ', 'c': 'ᶜ', 'd': 'ᵈ', 'e': 'ᵉ', 'f': 'ᶠ', 'g': 'ᵍ', 'h': 'ʰ', 'j': 'ʲ', 'k': 'ᵏ',
    'l': 'ˡ', 'm': 'ᵐ', 'o': 'ᵒ', 'p': 'ᵖ', 'r': 'ʳ', 's': 'ˢ', 't': 'ᵗ', 'u': 'ᵘ', 'v': 'ᵛ', 'w': 'ʷ'
};

const SUB_MAP = {
    '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄', '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
    '+': '₊', '-': '₋', '=': '₌', '(': '₍', ')': '₎', 'a': 'ₐ', 'e': 'ₑ', 'o': 'ₒ', 'x': 'ₓ', 'h': 'ₕ',
    'k': 'ₖ', 'l': 'ₗ', 'm': 'ₘ', 'n': 'ₙ', 'p': 'ₚ', 's': 'ₛ', 't': 'ₜ', 'i': 'ᵢ', 'j': 'ⱼ', 'r': 'ᵣ',
    'u': 'ᵤ', 'v': 'ᵥ'
};

// Helper to convert LaTeX snippets in text to Unicode
export function latexToUnicode(text) {
    if (!text) return "";

    // Replace LaTeX blocks wrapped in $ or $$
    return text.replace(/\$\$?([\s\S]+?)\$\$?/g, (match, content) => {
        let result = content;

        // Apply symbol replacements (sort by length descending to match longer commands first)
        const sortedKeys = Object.keys(LATEX_SYMBOLS).sort((a, b) => b.length - a.length);

        for (const latex of sortedKeys) {
            const unicode = LATEX_SYMBOLS[latex];
            // Match the command followed by a non-letter or end of string
            const regex = new RegExp(latex.replace(/\\/g, '\\\\') + '(?![a-zA-Z])', 'g');
            result = result.replace(regex, unicode);
        }

        // Handle Superscripts (^2 or ^{12})
        result = result.replace(/\^\{?([0-9a-zA-Z+-=]+)\}?/g, (m, p1) => {
            return p1.split('').map(c => SUPER_MAP[c] || c).join('');
        });

        // Handle Subscripts (_0 or _{12})
        result = result.replace(/\_\{?([0-9a-zA-Z+-=]+)\}?/g, (m, p1) => {
            return p1.split('').map(c => SUB_MAP[c] || c).join('');
        });

        // Clean up common formatting
        result = result.replace(/\\text\{([\s\S]+?)\}/g, '$1');
        result = result.replace(/\\mathrm\{([\s\S]+?)\}/g, '$1');
        result = result.replace(/\\mathbf\{([\s\S]+?)\}/g, '$1');
        result = result.replace(/\\sqrt\{([\s\S]+?)\}/g, '√($1)');
        result = result.replace(/\\frac\{([\s\S]+?)\}\{([\s\S]+?)\}/g, '($1/$2)');

        // Strip remaining curly braces often used for grouping
        result = result.replace(/\{([\s\S]+?)\}/g, '$1');

        return result.trim();
    });
}

// Helper for simple subscripts and superscripts outside of explicit LaTeX blocks
export function convertSimpleSubSuper(text) {
    if (!text) return "";

    let processed = text;

    // Subscripts: x_1, n_0, log_2, x_{12}

    // Braced subscript: x_{12} -> x₁₂
    processed = processed.replace(/([a-zA-Z0-9])_\{([0-9a-z+-=]+)\}/g, (m, p1, p2) => {
        const converted = p2.split('').map(c => SUB_MAP[c] || c).join('');
        return p1 + converted;
    });

    // Single char subscript: x_1 -> x₁ 
    // Heuristic: only if preceded by a single letter at word start or common functions
    processed = processed.replace(/(\b[a-zA-Z]|log|ln|sin|cos|tan)_([0-9a-z])/g, (m, p1, p2) => {
        return p1 + (SUB_MAP[p2] || "_" + p2);
    });

    // Superscripts: x^2, x^{12}

    // Braced superscript: x^{12} -> x¹²
    processed = processed.replace(/([a-zA-Z0-9])\^\{([0-9a-z+-=]+)\}/g, (m, p1, p2) => {
        const converted = p2.split('').map(c => SUPER_MAP[c] || c).join('');
        return p1 + converted;
    });

    // Single char superscript: x^2 -> x²
    processed = processed.replace(/([a-zA-Z0-9])\^([0-9a-z+-=])/g, (m, p1, p2) => {
        return p1 + (SUPER_MAP[p2] || "^" + p2);
    });

    return processed;
}

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
            // Convert LaTeX snippets and simple sub/superscripts in text tokens to Unicode
            if (token.type === 'text' && token.text) {
                token.text = latexToUnicode(token.text);
                token.text = convertSimpleSubSuper(token.text);
            }
        },
        renderer: {
            code({ text, lang }) {
                const id = 'code-' + Math.random().toString(36).substr(2, 9);
                const safeLang = (lang || 'text').replace(/[^a-z0-9-]/gi, '');

                // DOMPurify will sanitize the HTML, so we just need minimal escaping here
                // to prevent marked from breaking. Prism handles the rest.
                const escaped = text.replace(/</g, '&lt;').replace(/>/g, '&gt;');

                return `
                    <div class="code-container">
                        <button class="copy-btn" title="Copy to clipboard" onclick="copyCode('${id}')">
                            <i data-lucide="copy"></i>
                        </button>
                        <pre><code id="${id}" class="language-${safeLang}">${escaped}</code></pre>
                    </div>
                `;
            }
        }
    });
}

// Function to trigger Prism.js syntax highlighting
export function highlightCode(container) {
    if (window.Prism && container) {
        Prism.highlightAllUnder(container);
    }
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
