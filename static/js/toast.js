/**
 * Toast notification component.
 *
 * Usage: import { showToast } from './toast.js';
 *        showToast("message", "error");
 *        showToast("message", "warning", 4000);
 *        showToast("message", "success");
 *        showToast("message", "info");
 */

const TOAST_TYPES = {
    error:   { icon: 'circle-alert',  color: '#ff5555', bg: 'rgba(255,85,85,0.12)',   border: 'rgba(255,85,85,0.3)' },
    warning: { icon: 'triangle-alert', color: '#f0a040', bg: 'rgba(240,160,64,0.12)',  border: 'rgba(240,160,64,0.3)' },
    success: { icon: 'circle-check',   color: '#50fa7b', bg: 'rgba(80,250,123,0.12)',  border: 'rgba(80,250,123,0.3)' },
    info:    { icon: 'info',           color: '#55aaff', bg: 'rgba(85,170,255,0.12)',  border: 'rgba(85,170,255,0.3)' },
};

function getContainer() {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        document.body.appendChild(container);
    }
    return container;
}

export function showToast(message, type = 'info', duration = 5000) {
    const config = TOAST_TYPES[type] || TOAST_TYPES.info;
    const container = getContainer();

    const toast = document.createElement('div');
    toast.className = 'toast-notification';
    toast.style.cssText = `
        --toast-color: ${config.color};
        --toast-bg: ${config.bg};
        --toast-border: ${config.border};
    `;

    toast.innerHTML = `
        <i data-lucide="${config.icon}" class="toast-icon"></i>
        <span class="toast-message">${message}</span>
        <button class="toast-close" title="Dismiss">
            <i data-lucide="x"></i>
        </button>
    `;

    container.appendChild(toast);
    lucide.createIcons({ elements: Array.from(toast.querySelectorAll('[data-lucide]')) });

    // Trigger enter animation
    requestAnimationFrame(() => {
        requestAnimationFrame(() => toast.classList.add('visible'));
    });

    // Dismiss handlers
    const dismiss = () => {
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 300);
    };

    toast.querySelector('.toast-close').addEventListener('click', dismiss);

    if (duration > 0) {
        setTimeout(dismiss, duration);
    }
}
