/**
 * Settings page functionality
 * - Hardware key management (WebAuthn)
 * - Profile settings (display name, password)
 * - Recovery key management
 */

// Get base URL from global or empty
const BASE_URL = window.SYNTH_BASE_URL || '';

// State
let credentials = [];
let renamingCredentialId = null;

// Elements cache
const elements = {};

function getEl(id) {
    if (!elements[id]) {
        elements[id] = document.getElementById(id);
    }
    return elements[id];
}

// ============================================================================
// Hardware Key Management
// ============================================================================

async function loadCredentials() {
    const listEl = getEl('credentials-list');
    try {
        const resp = await fetch(`${BASE_URL}/api/webauthn/credentials`);
        credentials = await resp.json();
        renderCredentials();
    } catch (err) {
        console.error('Failed to load credentials:', err);
        listEl.innerHTML = '<p class="no-credentials">Failed to load keys</p>';
    }
}

function renderCredentials() {
    const listEl = getEl('credentials-list');
    if (credentials.length === 0) {
        listEl.innerHTML = '<p class="no-credentials">No hardware keys registered</p>';
        return;
    }

    listEl.innerHTML = credentials.map(cred => `
        <div class="credential-item" data-id="${cred.id}">
            <div class="credential-info">
                <span class="credential-name">${escapeHtml(cred.name)}</span>
                <span class="credential-date">Added ${formatDate(cred.created_at)}</span>
            </div>
            <div style="display: flex; align-items: center; gap: 1rem;">
                ${cred.has_dek
                    ? '<span class="status-badge has-dek">Encryption linked</span>'
                    : '<span class="status-badge no-dek">No encryption</span>'
                }
                <div class="credential-actions">
                    <button data-action="rename" data-id="${cred.id}" data-name="${escapeHtml(cred.name)}" title="Rename">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                        </svg>
                    </button>
                    <button class="btn-delete" data-action="delete" data-id="${cred.id}" title="Delete">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <polyline points="3 6 5 6 21 6"/>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                        </svg>
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

async function addKey() {
    const keyNameInput = getEl('key-name-input');
    const addKeyBtn = getEl('add-key-btn');
    const keyName = keyNameInput.value.trim() || 'Security Key';

    addKeyBtn.disabled = true;
    addKeyBtn.innerHTML = '<span class="loading-spinner"></span>';

    try {
        // Step 1: Get registration options
        const optionsResp = await fetch(`${BASE_URL}/api/webauthn/register/begin`);
        if (!optionsResp.ok) {
            throw new Error('Failed to get registration options');
        }
        const { options, challenge } = await optionsResp.json();

        // Options is already a JSON object from the server
        const publicKeyOptions = options;

        // Convert base64url strings to ArrayBuffers
        publicKeyOptions.challenge = base64urlToBuffer(publicKeyOptions.challenge);
        publicKeyOptions.user.id = base64urlToBuffer(publicKeyOptions.user.id);
        if (publicKeyOptions.excludeCredentials) {
            publicKeyOptions.excludeCredentials = publicKeyOptions.excludeCredentials.map(cred => ({
                ...cred,
                id: base64urlToBuffer(cred.id)
            }));
        }

        // Step 2: Create credential with browser API
        const credential = await navigator.credentials.create({
            publicKey: publicKeyOptions
        });

        // Step 3: Send credential to server
        const credentialData = {
            id: bufferToBase64url(credential.rawId),
            rawId: bufferToBase64url(credential.rawId),
            type: credential.type,
            response: {
                clientDataJSON: bufferToBase64url(credential.response.clientDataJSON),
                attestationObject: bufferToBase64url(credential.response.attestationObject)
            }
        };

        const verifyResp = await csrfFetch(`${BASE_URL}/api/webauthn/register/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                credential: credentialData,
                challenge: challenge,
                name: keyName
            })
        });

        if (!verifyResp.ok) {
            const err = await verifyResp.json();
            throw new Error(err.detail || 'Registration failed');
        }

        keyNameInput.value = '';
        await loadCredentials();

    } catch (err) {
        console.error('Registration error:', err);
        if (err.name === 'NotAllowedError') {
            alert('Registration was cancelled or timed out');
        } else {
            alert('Failed to register key: ' + err.message);
        }
    } finally {
        addKeyBtn.disabled = false;
        addKeyBtn.textContent = 'Add Key';
    }
}

async function deleteCredential(id) {
    if (!confirm('Delete this hardware key? You will not be able to use it for login anymore.')) {
        return;
    }

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/webauthn/credentials/${id}`, {
            method: 'DELETE'
        });

        if (!resp.ok) {
            throw new Error('Failed to delete');
        }

        await loadCredentials();
    } catch (err) {
        console.error('Delete error:', err);
        alert('Failed to delete key');
    }
}

// Rename modal
function openRenameModal(id, currentName) {
    renamingCredentialId = id;
    const renameInput = getEl('rename-input');
    const renameModal = getEl('rename-modal');
    renameInput.value = currentName;
    renameModal.classList.remove('hidden');
    renameInput.focus();
}

function closeRenameModal() {
    getEl('rename-modal').classList.add('hidden');
    renamingCredentialId = null;
}

async function saveRename() {
    const renameInput = getEl('rename-input');
    const newName = renameInput.value.trim();
    if (!newName) {
        alert('Name cannot be empty');
        return;
    }

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/webauthn/credentials/${renamingCredentialId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName })
        });

        if (!resp.ok) {
            throw new Error('Failed to rename');
        }

        closeRenameModal();
        await loadCredentials();
    } catch (err) {
        console.error('Rename error:', err);
        alert('Failed to rename key');
    }
}

// Helper functions for WebAuthn
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

function formatDate(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

// ============================================================================
// Profile & Security Settings
// ============================================================================

async function updateDisplayName() {
    const input = getEl('display-name-input');
    const newName = input.value.trim();

    if (!newName) {
        alert('Display name cannot be empty');
        return;
    }

    const btn = getEl('update-name-btn');
    btn.disabled = true;
    btn.textContent = 'Saving...';

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/user/profile/display-name`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ display_name: newName })
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to update');
        }

        getEl('current-display-name').textContent = newName;
        btn.textContent = 'Updated!';
        setTimeout(() => {
            btn.textContent = 'Update Name';
            btn.disabled = false;
        }, 2000);
    } catch (err) {
        alert('Error: ' + err.message);
        btn.textContent = 'Update Name';
        btn.disabled = false;
    }
}

async function changePassword() {
    const oldPass = getEl('old-password').value;
    const newPass = getEl('new-password').value;
    const confirmPass = getEl('confirm-password').value;

    if (!oldPass || !newPass) {
        alert('Please fill in all password fields');
        return;
    }

    if (newPass.length < 4) {
        alert('New password must be at least 4 characters');
        return;
    }

    if (newPass !== confirmPass) {
        alert('New passwords do not match');
        return;
    }

    const btn = getEl('change-password-btn');
    btn.disabled = true;
    btn.textContent = 'Changing...';

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/user/profile/change-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                old_password: oldPass,
                new_password: newPass
            })
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to change password');
        }

        // Clear inputs
        getEl('old-password').value = '';
        getEl('new-password').value = '';
        getEl('confirm-password').value = '';

        btn.textContent = 'Changed!';
        setTimeout(() => {
            btn.textContent = 'Change Password';
            btn.disabled = false;
        }, 2000);
    } catch (err) {
        alert('Error: ' + err.message);
        btn.textContent = 'Change Password';
        btn.disabled = false;
    }
}

// ============================================================================
// Recovery Key
// ============================================================================

async function loadRecoveryKeyStatus() {
    const statusEl = getEl('recovery-key-status');
    try {
        const resp = await fetch(`${BASE_URL}/api/user/recovery-key/status`);
        if (!resp.ok) throw new Error('Failed to load status');

        const data = await resp.json();
        const actionsEl = getEl('recovery-key-actions');
        const noKeyEl = getEl('recovery-key-no-key');
        const hasKeyEl = getEl('recovery-key-has-key');

        statusEl.classList.add('hidden');
        actionsEl.classList.remove('hidden');

        if (data.has_recovery_key) {
            hasKeyEl.classList.remove('hidden');
            noKeyEl.classList.add('hidden');
        } else {
            noKeyEl.classList.remove('hidden');
            hasKeyEl.classList.add('hidden');
        }
    } catch (err) {
        console.error('Failed to load recovery key status:', err);
        statusEl.textContent = 'Failed to load status';
    }
}

async function generateRecoveryKey() {
    const passwordInput = getEl('recovery-password');
    const password = passwordInput.value;

    if (!password) {
        alert('Please enter your password');
        return;
    }

    const btn = getEl('generate-recovery-btn');
    btn.disabled = true;
    btn.textContent = 'Generating...';

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/user/recovery-key/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to generate');
        }

        const data = await resp.json();

        // Show the key
        getEl('recovery-key-value').textContent = data.recovery_key;
        getEl('recovery-key-result').classList.remove('hidden');
        getEl('recovery-key-no-key').classList.add('hidden');
        passwordInput.value = '';

    } catch (err) {
        alert('Error: ' + err.message);
        btn.textContent = 'Generate Recovery Key';
        btn.disabled = false;
    }
}

function finishRecoveryKey() {
    getEl('recovery-key-result').classList.add('hidden');
    getEl('recovery-key-has-key').classList.remove('hidden');
    getEl('generate-recovery-btn').textContent = 'Generate Recovery Key';
    getEl('generate-recovery-btn').disabled = false;
}

async function regenerateRecoveryKey() {
    if (!confirm('WARNING: This will invalidate your current recovery key.\n\nIf you have not saved the current key and lose your password, your files will be UNRECOVERABLE.\n\nAre you sure you want to generate a new recovery key?')) {
        return;
    }

    const passwordInput = getEl('recovery-password');
    const password = passwordInput.value;

    if (!password) {
        // Show password input if hidden
        getEl('recovery-key-no-key').classList.remove('hidden');
        getEl('recovery-key-has-key').classList.add('hidden');
        passwordInput.focus();
        alert('Please enter your password to generate a new recovery key');
        return;
    }

    const btn = getEl('regenerate-recovery-btn');
    btn.disabled = true;
    btn.textContent = 'Generating...';

    try {
        const resp = await csrfFetch(`${BASE_URL}/api/user/recovery-key/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password })
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to generate');
        }

        const data = await resp.json();

        // Show the new key
        getEl('recovery-key-value').textContent = data.recovery_key;
        getEl('recovery-key-result').classList.remove('hidden');
        getEl('recovery-key-no-key').classList.add('hidden');
        getEl('recovery-key-has-key').classList.add('hidden');
        passwordInput.value = '';

    } catch (err) {
        alert('Error: ' + err.message);
        btn.textContent = 'Generate New Recovery Key';
        btn.disabled = false;
    }
}

// ============================================================================
// Initialization
// ============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // Hardware key section
    loadCredentials();

    getEl('add-key-btn').addEventListener('click', addKey);

    // Credentials list delegation
    getEl('credentials-list').addEventListener('click', (e) => {
        const btn = e.target.closest('[data-action]');
        if (!btn) return;

        const action = btn.dataset.action;
        const id = parseInt(btn.dataset.id, 10);

        if (action === 'delete') {
            deleteCredential(id);
        } else if (action === 'rename') {
            openRenameModal(id, btn.dataset.name);
        }
    });

    // Rename modal
    getEl('rename-cancel-btn').addEventListener('click', closeRenameModal);
    getEl('rename-save-btn').addEventListener('click', saveRename);
    getEl('rename-modal').addEventListener('click', (e) => {
        if (e.target === getEl('rename-modal')) {
            closeRenameModal();
        }
    });

    // Profile settings
    getEl('update-name-btn').addEventListener('click', updateDisplayName);
    getEl('change-password-form').addEventListener('submit', (e) => {
        e.preventDefault();
        changePassword();
    });

    // Recovery key
    getEl('recovery-key-form').addEventListener('submit', (e) => {
        e.preventDefault();
        generateRecoveryKey();
    });
    getEl('recovery-key-done-btn').addEventListener('click', finishRecoveryKey);
    getEl('regenerate-recovery-btn').addEventListener('click', regenerateRecoveryKey);
    loadRecoveryKeyStatus();

    // Bind Enter keys for non-form inputs
    if (window.bindEnterKey) {
        bindEnterKey('display-name-input', 'update-name-btn');
        bindEnterKey('key-name-input', 'add-key-btn');
        bindEnterKey('rename-input', 'rename-save-btn');
    }
});

console.log('[settings.js] Loaded');
