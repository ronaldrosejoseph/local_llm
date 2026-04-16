/**
 * Speech functions — text-to-speech and speech-to-text.
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

// --- Speech-to-Text (Web Speech API) ---

let recognition = null;

export function initSpeechRecognition() {
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

export function toggleRecording() {
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
