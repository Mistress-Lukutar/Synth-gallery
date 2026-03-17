/**
 * Login page functionality
 * - Hardware key authentication (WebAuthn)
 * - Recovery key login
 */

const BASE_URL = window.SYNTH_BASE_URL || '';

// Get 'next' parameter from URL for post-login redirect
function getNextParam() {
    const params = new URLSearchParams(window.location.search);
    return params.get('next') || '';
}

const NEXT_URL = getNextParam();

// Elements
const usernameInput = document.getElementById('username');
const hardwareKeySection = document.getElementById('hardware-key-section');
const hardwareKeyBtn = document.getElementById('hardware-key-btn');
const recoveryKeyLink = document.getElementById('recovery-key-link');
const recoveryModal = document.getElementById('recovery-modal');
const recoveryKeyInput = document.getElementById('recovery-key-input');
const recoveryUsernameInput = document.getElementById('recovery-username');
const recoveryCancelBtn = document.getElementById('recovery-cancel-btn');
const recoverySubmitBtn = document.getElementById('recovery-submit-btn');
const recoveryError = document.getElementById('recovery-error');
const recoverySuccess = document.getElementById('recovery-success');

let checkTimeout = null;
let lastCheckedUsername = '';

// ============================================================================
// Hardware Key Authentication
// ============================================================================

async function checkHardwareKeys(username) {
    if (!username || username.length < 2) {
        hardwareKeySection.classList.remove('visible');
        return;
    }

    try {
        const resp = await fetch(`${BASE_URL}/api/webauthn/check/${encodeURIComponent(username)}`);
        const data = await resp.json();

        if (data.has_keys) {
            hardwareKeySection.classList.add('visible');
            lastCheckedUsername = username;
        } else {
            hardwareKeySection.classList.remove('visible');
        }
    } catch (err) {
        console.error('Failed to check hardware keys:', err);
        hardwareKeySection.classList.remove('visible');
    }
}

async function authenticateWithHardwareKey() {
    const username = usernameInput.value.trim();
    if (!username) {
        alert('Please enter your username first');
        usernameInput.focus();
        return;
    }

    hardwareKeyBtn.disabled = true;
    hardwareKeyBtn.innerHTML = `
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="animation: spin 1s linear infinite;">
            <circle cx="12" cy="12" r="10" stroke-dasharray="32" stroke-dashoffset="32"/>
        </svg>
        Waiting for key...
    `;

    try {
        // Step 1: Get authentication options
        const optionsResp = await fetch(`${BASE_URL}/api/webauthn/authenticate/begin?username=${encodeURIComponent(username)}`);
        if (!optionsResp.ok) {
            const err = await optionsResp.json();
            throw new Error(err.detail || 'Failed to get options');
        }
        const { options, challenge } = await optionsResp.json();

        // Options is already a JSON object from the server
        const publicKeyOptions = options;

        // Convert base64url strings to ArrayBuffers
        publicKeyOptions.challenge = base64urlToBuffer(publicKeyOptions.challenge);
        if (publicKeyOptions.allowCredentials) {
            publicKeyOptions.allowCredentials = publicKeyOptions.allowCredentials.map(cred => ({
                ...cred,
                id: base64urlToBuffer(cred.id)
            }));
        }

        // Step 2: Get assertion from browser
        const assertion = await navigator.credentials.get({
            publicKey: publicKeyOptions
        });

        // Step 3: Send assertion to server
        const assertionData = {
            id: bufferToBase64url(assertion.rawId),
            rawId: bufferToBase64url(assertion.rawId),
            type: assertion.type,
            response: {
                clientDataJSON: bufferToBase64url(assertion.response.clientDataJSON),
                authenticatorData: bufferToBase64url(assertion.response.authenticatorData),
                signature: bufferToBase64url(assertion.response.signature),
                userHandle: assertion.response.userHandle
                    ? bufferToBase64url(assertion.response.userHandle)
                    : null
            }
        };

        const verifyResp = await fetch(`${BASE_URL}/api/webauthn/authenticate/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                credential: assertionData,
                challenge: challenge
            })
        });

        if (!verifyResp.ok) {
            const err = await verifyResp.json();
            throw new Error(err.detail || 'Authentication failed');
        }

        // Success - redirect to next URL or gallery
        const redirectUrl = NEXT_URL && NEXT_URL.startsWith('/') ? NEXT_URL : `${BASE_URL}/`;
        window.location.href = redirectUrl;

    } catch (err) {
        console.error('Hardware key auth error:', err);
        if (err.name === 'NotAllowedError') {
            alert('Authentication was cancelled or timed out');
        } else {
            alert('Authentication failed: ' + err.message);
        }
        hardwareKeyBtn.disabled = false;
        hardwareKeyBtn.innerHTML = `
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
                <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
            </svg>
            Sign in with Hardware Key
        `;
    }
}

// ============================================================================
// Recovery Key Login
// ============================================================================

function openRecoveryModal() {
    recoveryModal.classList.add('visible');
    recoveryKeyInput.value = '';
    recoveryUsernameInput.value = usernameInput.value || '';
    recoveryError.classList.add('hidden');
    recoverySuccess.classList.add('hidden');
    if (recoveryUsernameInput.value) {
        recoveryKeyInput.focus();
    } else {
        recoveryUsernameInput.focus();
    }
}

function closeRecoveryModal() {
    recoveryModal.classList.remove('visible');
}

async function submitRecoveryKey() {
    const username = recoveryUsernameInput.value.trim();
    const recoveryKey = recoveryKeyInput.value.trim().toUpperCase();

    if (!username) {
        recoveryError.textContent = 'Please enter your username';
        recoveryError.classList.remove('hidden');
        return;
    }

    if (!recoveryKey || recoveryKey.length < 20) {
        recoveryError.textContent = 'Please enter a valid recovery key';
        recoveryError.classList.remove('hidden');
        return;
    }

    recoveryError.classList.add('hidden');
    recoverySubmitBtn.disabled = true;
    recoverySubmitBtn.textContent = 'Verifying...';

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/auth/recover`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, recovery_key: recoveryKey })
        });

        const data = await resp.json();

        if (resp.ok) {
            recoverySuccess.textContent = 'Success! Redirecting...';
            recoverySuccess.classList.remove('hidden');
            // Redirect to password reset page
            window.location.href = `${BASE_URL}/reset-password?token=` + encodeURIComponent(data.reset_token);
        } else {
            recoveryError.textContent = data.detail || 'Invalid recovery key';
            recoveryError.classList.remove('hidden');
            recoverySubmitBtn.disabled = false;
            recoverySubmitBtn.textContent = 'Sign In with Recovery Key';
        }
    } catch (err) {
        recoveryError.textContent = 'Network error. Please try again.';
        recoveryError.classList.remove('hidden');
        recoverySubmitBtn.disabled = false;
        recoverySubmitBtn.textContent = 'Sign In with Recovery Key';
    }
}

// ============================================================================
// WebAuthn Helper Functions
// ============================================================================

function base64urlToBuffer(base64url) {
    let base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    const padding = 4 - base64.length % 4;
    if (padding !== 4) {
        base64 += '='.repeat(padding);
    }
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes.buffer;
}

function bufferToBase64url(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) {
        binary += String.fromCharCode(bytes[i]);
    }
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
}

// ============================================================================
// Event Listeners
// ============================================================================

// Check hardware keys on username input
usernameInput.addEventListener('input', (e) => {
    clearTimeout(checkTimeout);
    checkTimeout = setTimeout(() => {
        checkHardwareKeys(e.target.value.trim());
    }, 300);
});

// Check on page load if username is pre-filled
if (usernameInput.value) {
    checkHardwareKeys(usernameInput.value.trim());
}

// Hardware key button
hardwareKeyBtn.addEventListener('click', authenticateWithHardwareKey);

// Recovery key link
recoveryKeyLink.addEventListener('click', (e) => {
    e.preventDefault();
    openRecoveryModal();
});

// Recovery modal buttons
recoveryCancelBtn.addEventListener('click', closeRecoveryModal);

recoveryModal.addEventListener('click', (e) => {
    if (e.target === recoveryModal) {
        closeRecoveryModal();
    }
});

recoverySubmitBtn.addEventListener('click', submitRecoveryKey);

// Allow Enter key to submit recovery form
recoveryKeyInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        submitRecoveryKey();
    }
});

recoveryUsernameInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        recoveryKeyInput.focus();
    }
});

