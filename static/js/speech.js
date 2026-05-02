/**
 * Speech functions — text-to-speech and speech-to-text.
 * 
 * Supports two speech-to-text backends:
 *   1. Web Speech API (webkitSpeechRecognition) — used in regular browsers.
 *   2. Native macOS SFSpeechRecognizer — used inside the WKWebView app wrapper,
 *      bridged via WKScriptMessageHandler.
 */

import { state, elements, API_URL } from './state.js';

// --- Text-to-Speech ---

export async function speakResponse(text) {
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

export async function stopSpeaking() {
    try {
        console.log("Requesting stop-say from server...");
        const res = await fetch(`${API_URL}/api/stop-say`, { method: 'POST' });
        const data = await res.json();
        console.log("Stop-say response:", data);
    } catch (err) {
        console.error('Error stopping speech:', err);
    }
}

// --- Speech-to-Text ---

// Detect if we're running inside the macOS WKWebView wrapper
const isNativeApp = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.speechRecognition);

let recognition = null;

// --- Native macOS SFSpeechRecognizer bridge (WKWebView) ---

function setupNativeSpeechBridge() {
    // Callbacks invoked by the Swift side via evaluateJavaScript
    window._nativeSpeechStarted = () => {
        state.isRecording = true;
        elements.voiceBtn.classList.add('recording');
    };

    window._nativeSpeechPartialResult = (transcript) => {
        elements.chatInput.value = transcript;
        // Auto-expand textarea
        elements.chatInput.style.height = 'auto';
        elements.chatInput.style.height = elements.chatInput.scrollHeight + 'px';
    };

    window._nativeSpeechError = (msg) => {
        console.error('Native speech error:', msg);
        stopRecording();
        alert(msg);
    };

    window._nativeSpeechEnded = () => {
        if (state.isRecording) {
            stopRecording();
            const text = elements.chatInput.value.trim();
            if (text) {
                import('./chat.js').then(({ sendMessage }) => {
                    sendMessage(text);
                });
            }
        }
    };
}

// --- Web Speech API (regular browser) ---

function setupWebSpeechRecognition() {
    if ('webkitSpeechRecognition' in window) {
        recognition = new webkitSpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-US';

        recognition.onstart = () => {
            state.isRecording = true;
            elements.voiceBtn.classList.add('recording');
        };

        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            elements.chatInput.value = transcript;
            // Import sendMessage dynamically to avoid circular dependency
            import('./chat.js').then(({ sendMessage }) => {
                sendMessage(transcript);
            });
        };

        recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            stopRecording();
        };

        recognition.onend = () => {
            stopRecording();
        };
    }
}

export function initSpeechRecognition() {
    if (isNativeApp) {
        console.log('🎙️ Using native macOS speech recognition');
        setupNativeSpeechBridge();
    } else {
        setupWebSpeechRecognition();
    }
}

export function toggleRecording() {
    if (isNativeApp) {
        // Use native macOS SFSpeechRecognizer via the Swift bridge
        if (state.isRecording) {
            window.webkit.messageHandlers.speechRecognition.postMessage('stop');
            stopRecording();
            const text = elements.chatInput.value.trim();
            if (text) {
                import('./chat.js').then(({ sendMessage }) => {
                    sendMessage(text);
                });
            }
        } else {
            elements.chatInput.value = '';
            window.webkit.messageHandlers.speechRecognition.postMessage('start');
        }
        return;
    }

    // Fallback: Web Speech API
    if (!recognition) {
        alert('Speech recognition is not supported in this browser.');
        return;
    }

    if (state.isRecording) {
        recognition.stop();
    } else {
        recognition.start();
    }
}

function stopRecording() {
    state.isRecording = false;
    elements.voiceBtn.classList.remove('recording');
}
