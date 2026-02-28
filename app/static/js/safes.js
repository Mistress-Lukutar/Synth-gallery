/**
 * Safes module - Safe unlock and navigation
 * Phase 6: Safes Module
 */

(function() {
    let userSafes = [];

    // Load safe thumbnails - same logic as regular thumbnails, just decrypt first
    window.loadSafeThumbnails = async function() {
        const images = document.querySelectorAll('img[data-safe-thumbnail]');
        if (images.length === 0) return;
        

        
        for (const img of images) {
            const photoId = img.dataset.safeThumbnail;
            const safeId = img.dataset.safeId;
            
            if (!photoId || !safeId) continue;
            
            // Skip if already handled (error or loaded)
            if (img.dataset.errorHandled) continue;
            
            try {
                // Check if safe is unlocked via SafeCrypto
                if (typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && SafeCrypto.isUnlocked(safeId)) {
                    // Fetch encrypted thumbnail
                    const resp = await fetch(`${getBaseUrl()}/thumbnails/${photoId}?safe=${safeId}`);
                    if (resp.ok) {
                        const encryptedBlob = await resp.blob();
                        
                        // Decrypt using SafeCrypto
                        try {
                            const decryptedBlob = await SafeCrypto.decryptFileFromSafe(
                                encryptedBlob, 
                                safeId, 
                                'image/jpeg'
                            );
                            // Set src - onload in navigation.js will handle placeholder and masonry
                            img.src = URL.createObjectURL(decryptedBlob);
                        } catch (decryptErr) {
                            console.error('[safes.js] Decrypt failed:', decryptErr);
                            if (typeof handleImageError === 'function') {
                                handleImageError(img, 'locked');
                            }
                        }
                        continue;
                    }
                }
                
                // Safe locked or failed to load - show locked placeholder
                if (typeof handleImageError === 'function') {
                    handleImageError(img, 'locked');
                }
                
            } catch (err) {
                console.error('[safes.js] Failed to load safe thumbnail:', err);
                if (typeof handleImageError === 'function') {
                    handleImageError(img, 'locked');
                }
            }
        }
    };

    // Navigate to safe - go to safe root folder (folder with safe_id and no parent)
    window.navigateToSafe = async function(safeId) {

        if (!window.folderTree || typeof navigateToFolder !== 'function') return;
        
        const safeRoot = window.folderTree.find(f => f.safe_id === safeId && !f.parent_id);
        if (safeRoot) {
            navigateToFolder(safeRoot.id, false);
        }
    };

    // Open safe unlock modal
    window.openSafeUnlock = function(safeId, safeName, unlockType, credentialName) {
        const modal = document.getElementById('safe-unlock-modal');
        const title = document.getElementById('safe-unlock-title');
        const description = document.getElementById('safe-unlock-description');
        const passwordSection = document.getElementById('safe-unlock-password-section');
        const webauthnSection = document.getElementById('safe-unlock-webauthn-section');
        const passwordInput = document.getElementById('safe-unlock-password-input');
        const submitBtn = document.getElementById('safe-unlock-submit-btn');

        currentUnlockSafeId = safeId;
        currentUnlockType = unlockType || 'password';
        
        if (title) title.textContent = `Unlock ${safeName || 'Safe'}`;
        
        // Show appropriate section based on unlock type
        if (currentUnlockType === 'webauthn') {
            const keyName = credentialName || 'Hardware Key';
            if (description) description.textContent = `Use your ${keyName} to unlock`;
            if (passwordSection) passwordSection.classList.add('hidden');
            if (webauthnSection) webauthnSection.classList.remove('hidden');
            if (submitBtn) submitBtn.textContent = 'Authenticate with Key';
        } else {
            if (description) description.textContent = 'Enter password to unlock';
            if (passwordSection) passwordSection.classList.remove('hidden');
            if (webauthnSection) webauthnSection.classList.add('hidden');
            if (submitBtn) submitBtn.textContent = 'Unlock';
            // Focus password input
            if (passwordInput) {
                passwordInput.value = '';
                setTimeout(() => passwordInput.focus(), 100);
            }
        }
        
        if (modal) {
            modal.dataset.safeId = safeId;
            modal.classList.remove('hidden');
        }
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('safe-unlock-modal', closeSafeUnlockModal);
        }
    };

    // Close safe unlock modal
    window.closeSafeUnlockModal = function() {
        // Unregister from BackButtonManager first (skipHistoryBack = true for button clicks)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('safe-unlock-modal', true);
        }
        
        const modal = document.getElementById('safe-unlock-modal');
        if (modal) modal.classList.add('hidden');
        currentUnlockSafeId = null;
        currentUnlockType = null;
    };

    // Current safe being unlocked
    let currentUnlockSafeId = null;
    let currentUnlockType = null;

    // Handle safe unlock form submit
    document.addEventListener('DOMContentLoaded', () => {
        const unlockForm = document.getElementById('safe-unlock-form');
        if (unlockForm) {
            unlockForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                
                if (!currentUnlockSafeId) {
                    const modal = document.getElementById('safe-unlock-modal');
                    currentUnlockSafeId = modal?.dataset.safeId;
                }
                
                if (!currentUnlockSafeId) {
                    console.error('[safes.js] No safe ID for unlock');
                    return;
                }

                const submitBtn = document.getElementById('safe-unlock-submit-btn');
                
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Unlocking...';
                }

                try {
                    // Get challenge from server
                    const challengeResp = await csrfFetch(`${getBaseUrl()}/api/safes/unlock`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ safe_id: currentUnlockSafeId })
                    });

                    if (!challengeResp.ok) {
                        const errorText = await challengeResp.text();
                        throw new Error(`Server error: ${challengeResp.status} - ${errorText}`);
                    }

                    const challengeData = await challengeResp.json();


                    if (challengeData.status !== 'challenge') {
                        throw new Error(`Unexpected status: ${challengeData.status}`);
                    }

                    // Store encrypted_dek immediately to avoid scope issues
                    const encryptedDekFromServer = challengeData.encrypted_dek;


                    let unlockData;
                    
                    if (currentUnlockType === 'webauthn' || challengeData.type === 'webauthn') {
                        // WebAuthn unlock with PRF (zero-trust)
                        if (!challengeData.options || !encryptedDekFromServer) {
                            throw new Error('WebAuthn challenge data not provided');
                        }
                        
                        // Get credential ID from options
                        const allowCredentials = challengeData.options.allowCredentials;
                        if (!allowCredentials || allowCredentials.length === 0) {
                            throw new Error('No credentials available for WebAuthn unlock');
                        }
                        const credentialId = allowCredentials[0].id;
                        
                        // Authenticate with WebAuthn (includes PRF for decryption)
                        const credential = await navigator.credentials.get({
                            publicKey: {
                                ...challengeData.options,
                                challenge: base64ToBuffer(challengeData.options.challenge),
                                allowCredentials: allowCredentials.map(c => ({
                                    ...c,
                                    id: base64ToBuffer(c.id)
                                })),
                                extensions: {
                                    prf: { eval: { first: new Uint8Array(32).fill(0x01) } }
                                }
                            }
                        });
                        
                        if (!credential) {
                            throw new Error('WebAuthn authentication cancelled');
                        }
                        
                        // Decrypt DEK locally using PRF output
                        let prfResult = credential.clientExtensionResults?.prf?.results?.first;
                        
                        // Fallback: try getClientExtensionResults()
                        if (!prfResult && typeof credential.getClientExtensionResults === 'function') {
                            const extResults = credential.getClientExtensionResults();

                            prfResult = extResults?.prf?.results?.first;
                        }
                        
                        if (!prfResult) {
                            console.error('[safes.js] PRF results not available:', credential?.clientExtensionResults);
                            throw new Error('PRF not supported by this hardware key or not enabled. Please use a YubiKey 5 with firmware 5.2.3+ or similar device.');
                        }
                        
                        const encryptionKey = new Uint8Array(prfResult);
                        if (!encryptedDekFromServer) {
                            throw new Error('encrypted_dek not provided by server');
                        }
                        
                        const encryptedDEK = base64ToBuffer(encryptedDekFromServer);
                        
                        // Convert ArrayBuffer to Uint8Array for XOR operation
                        const encryptedDEKView = new Uint8Array(encryptedDEK);
                        const rawDEK = new Uint8Array(encryptedDEKView.length);
                        for (let i = 0; i < encryptedDEKView.length; i++) {
                            rawDEK[i] = encryptedDEKView[i] ^ encryptionKey[i % encryptionKey.length];
                        }
                        

                        
                        // Verify DEK size before import
                        if (rawDEK.length !== 32) {
                            throw new Error(`Invalid DEK size: ${rawDEK.length} bytes, expected 32`);
                        }
                        
                        // Import DEK and create session data
                        const tempCrypto = window.crypto || window.msCrypto;
                        const safeDEK = await tempCrypto.subtle.importKey(
                            'raw',
                            rawDEK,
                            { name: 'AES-GCM', length: 256 },
                            false,
                            ['encrypt', 'decrypt']
                        );
                        
                        // Store DEK temporarily for session creation
                        const sessionKey = tempCrypto.getRandomValues(new Uint8Array(32));
                        const iv = tempCrypto.getRandomValues(new Uint8Array(12));
                        const encryptedDEKForSession = await tempCrypto.subtle.encrypt(
                            { name: 'AES-GCM', iv },
                            await tempCrypto.subtle.importKey('raw', sessionKey, 'AES-GCM', false, ['encrypt']),
                            rawDEK
                        );
                        
                        const sessionEncryptedDEK = btoa(String.fromCharCode(...new Uint8Array(encryptedDEKForSession)));
                        const sessionKeyBase64 = btoa(String.fromCharCode(...sessionKey));
                        const ivBase64 = btoa(String.fromCharCode(...iv));
                        
                        // Send to server for verification and session creation
                        const completeResp = await csrfFetch(`${getBaseUrl()}/api/safes/unlock/complete`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                safe_id: currentUnlockSafeId,
                                credential: {
                                    id: credential.id,
                                    rawId: bufferToBase64(credential.rawId),
                                    type: credential.type,
                                    response: {
                                        authenticatorData: bufferToBase64(credential.response.authenticatorData),
                                        clientDataJSON: bufferToBase64(credential.response.clientDataJSON),
                                        signature: bufferToBase64(credential.response.signature),
                                        userHandle: credential.response.userHandle ? bufferToBase64(credential.response.userHandle) : null
                                    }
                                },
                                challenge: challengeData.options.challenge,
                                session_encrypted_dek: `${ivBase64}.${sessionEncryptedDEK}`,
                                session_key: sessionKeyBase64
                            })
                        });
                        
                        if (!completeResp.ok) {
                            const errorData = await completeResp.json().catch(() => ({}));
                            throw new Error(errorData.detail || 'WebAuthn authentication failed');
                        }
                        
                        // Store DEK in SafeCrypto
                        if (typeof SafeCrypto !== 'undefined' && SafeCrypto.storeSafeDEKDirect) {
                            SafeCrypto.storeSafeDEKDirect(currentUnlockSafeId, safeDEK);

                        } else {
                            console.error('[safes.js] SafeCrypto.storeSafeDEKDirect not available');
                        }
                        
                    } else {
                        // Password unlock
                        const passwordInput = document.getElementById('safe-unlock-password-input');
                        const password = passwordInput?.value;
                        if (!password) {
                            alert('Please enter password');
                            return;
                        }
                        
                        unlockData = await SafeCrypto.unlockWithPassword(
                            currentUnlockSafeId,
                            password,
                            challengeData.encrypted_dek,
                            challengeData.salt
                        );

                        await csrfFetch(`${getBaseUrl()}/api/safes/unlock/complete`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                safe_id: currentUnlockSafeId,
                                session_encrypted_dek: unlockData.session_encrypted_dek
                            })
                        });

                        await SafeCrypto.storeSafeDEKFromSession(
                            currentUnlockSafeId,
                            unlockData.session_encrypted_dek,
                            unlockData.session_key
                        );
                    }

                    // Save safeId before closing modal (closeSafeUnlockModal clears it)
                    const unlockedSafeId = currentUnlockSafeId;
                    closeSafeUnlockModal();
                    
                    // Reload folder tree and then navigate to safe root

                    if (typeof loadFolderTree === 'function') {
                        await loadFolderTree();
                    }
                    
                    // Navigate to safe root folder after folder tree is updated
                    if (typeof navigateToFolder === 'function' && window.folderTree) {
                        const safeRoot = window.folderTree.find(f => f.safe_id === unlockedSafeId && !f.parent_id);
                        if (safeRoot) {
                            navigateToFolder(safeRoot.id, true);
                        } else {
    
                        }
                    } else {

                    }
                    
                } catch (err) {
                    console.error('[safes.js] Unlock failed:', err);
                    alert('Unlock failed: ' + err.message);
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = currentUnlockType === 'webauthn' ? 'Authenticate with Key' : 'Unlock';
                    }
                }
            });
        }
    });

    // Open create safe modal
    window.openCreateSafe = function() {
        const modal = document.getElementById('safe-modal');
        if (modal) modal.classList.remove('hidden');
        
        // Reset WebAuthn credential ID
        const credentialIdInput = document.getElementById('safe-webauthn-credential-id');
        const statusText = document.getElementById('safe-webauthn-status');
        if (credentialIdInput) credentialIdInput.value = '';
        if (statusText) statusText.textContent = '';
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('safe-modal', closeSafeModal);
        }
    };

    // Close safe modal
    window.closeSafeModal = function() {
        // Unregister from BackButtonManager first (skipHistoryBack = true for button clicks)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('safe-modal', true);
        }
        
        const modal = document.getElementById('safe-modal');
        if (modal) modal.classList.add('hidden');
    };

    // Create safe form handler - attach after DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        const safeForm = document.getElementById('safe-form');
        
        // Handle protection method toggle
        const protectionRadios = document.querySelectorAll('input[name="safe-protection"]');
        const passwordSection = document.getElementById('safe-password-section');
        const webauthnSection = document.getElementById('safe-webauthn-section');
        const webauthnCreateBtn = document.getElementById('safe-webauthn-create-btn');
        const webauthnCredentialId = document.getElementById('safe-webauthn-credential-id');
        const webauthnStatus = document.getElementById('safe-webauthn-status');
        
        protectionRadios.forEach(radio => {
            radio.addEventListener('change', () => {
                if (radio.value === 'password') {
                    passwordSection?.classList.remove('hidden');
                    webauthnSection?.classList.add('hidden');
                } else {
                    passwordSection?.classList.add('hidden');
                    webauthnSection?.classList.remove('hidden');
                }
            });
        });
        
        // Handle WebAuthn credential creation button
        if (webauthnCreateBtn) {
            webauthnCreateBtn.addEventListener('click', async () => {
                try {
                    if (!window.PublicKeyCredential) {
                        alert('WebAuthn is not supported in this browser');
                        return;
                    }
                    
                    webauthnStatus.textContent = 'Touch your hardware key...';
                    webauthnCreateBtn.disabled = true;
                    
                    // Get challenge from server for new credential
                    const resp = await fetch(`${getBaseUrl()}/api/safes/webauthn/create-credential`);
                    if (!resp.ok) throw new Error('Failed to get challenge');
                    
                    const data = await resp.json();
                    
                    // Create new credential specifically for this safe
                    const options = data.options;
                    options.challenge = base64ToBuffer(options.challenge);
                    options.user.id = base64ToBuffer(options.user.id);
                    
                    // Request PRF extension
                    options.extensions = {
                        prf: { eval: { first: new Uint8Array(32).fill(0x01) } }
                    };
                    
                    const credential = await navigator.credentials.create({ publicKey: options });
                    if (!credential) {
                        throw new Error('Credential creation cancelled');
                    }
                    
                    // Check if PRF is supported (just check enabled flag)
                    const extResults = credential.getClientExtensionResults?.();

                    
                    const prfEnabled = extResults?.prf?.enabled;
                    if (!prfEnabled) {
                        throw new Error('Your hardware key does not support PRF extension required for safe encryption');
                    }
                    
                    // Now authenticate with the key to get PRF output
                    webauthnStatus.textContent = 'Key created! Touch it again to complete setup...';
                    
                    const prfInput = new Uint8Array(32);
                    prfInput.fill(0x01);
                    
                    const authCredential = await navigator.credentials.get({
                        publicKey: {
                            challenge: crypto.getRandomValues(new Uint8Array(32)),
                            allowCredentials: [{ id: credential.rawId, type: 'public-key' }],
                            userVerification: 'required',
                            extensions: {
                                prf: { eval: { first: prfInput } }
                            }
                        }
                    });
                    
                    if (!authCredential) {
                        throw new Error('Authentication cancelled');
                    }
                    
                    // Get PRF results from authentication
                    const authExtResults = authCredential.getClientExtensionResults?.();
                    const prfOutput = authExtResults?.prf?.results?.first;
                    if (!prfOutput) {
                        throw new Error('Failed to get PRF output from key. Make sure your key supports PRF.');
                    }
                    
                    // Store credential ID
                    webauthnCredentialId.value = credential.id;
                    webauthnStatus.textContent = 'Hardware key ready! You can now create the safe.';
                    webauthnStatus.style.color = 'var(--success)';
                    
                    // Get key name from input
                    const keyNameInput = document.getElementById('safe-webauthn-name');
                    const keyName = keyNameInput?.value?.trim() || 'Hardware Key';
                    
                    // Store credential data temporarily for form submission
                    webauthnCredentialId.dataset.credential = JSON.stringify({
                        id: credential.id,
                        rawId: bufferToBase64(credential.rawId),
                        type: credential.type,
                        response: {
                            clientDataJSON: bufferToBase64(credential.response.clientDataJSON),
                            attestationObject: bufferToBase64(credential.response.attestationObject)
                        }
                    });
                    // Challenge is already base64 string from server, no need to convert
                    // But if it's ArrayBuffer, convert it
                    let challengeStr = data.options.challenge;
                    if (challengeStr instanceof ArrayBuffer) {
                        challengeStr = bufferToBase64(challengeStr);
                    }
                    webauthnCredentialId.dataset.challenge = challengeStr;
                    webauthnCredentialId.dataset.keyName = keyName;
                    
                    // Store PRF output for encryption during safe creation
                    const prfBase64 = btoa(String.fromCharCode(...new Uint8Array(prfOutput)));
                    webauthnCredentialId.dataset.prfOutput = prfBase64;
                    
                } catch (err) {
                    console.error('[safes.js] Failed to create credential:', err);
                    webauthnStatus.textContent = 'Error: ' + err.message;
                    webauthnStatus.style.color = 'var(--danger)';
                    webauthnCreateBtn.disabled = false;
                }
            });
        }
        
        if (safeForm) {
            safeForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                
                const nameInput = document.getElementById('safe-name-input');
                const passwordInput = document.getElementById('safe-password-input');
                const webauthnCredentialId = document.getElementById('safe-webauthn-credential-id');
                const submitBtn = document.getElementById('safe-submit-btn');
                const protectionType = document.querySelector('input[name="safe-protection"]:checked')?.value;
                
                const name = nameInput?.value.trim();
                
                if (!name) {
                    alert('Please enter a safe name');
                    return;
                }
                
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Creating...';
                }
                
                try {
                    if (!SafeCrypto || !SafeCrypto.isAvailable) {
                        throw new Error('SafeCrypto not available. Please use HTTPS or localhost.');
                    }
                    
                    let safeData;
                    
                    if (protectionType === 'webauthn') {
                        // Check if credential was created
                        const credentialId = webauthnCredentialId?.value;
                        if (!credentialId) {
                            throw new Error('Please touch your hardware key first');
                        }
                        
                        // Get stored PRF output from credential creation
                        const prfBase64 = webauthnCredentialId.dataset.prfOutput;
                        if (!prfBase64) {
                            throw new Error('PRF output not available. Please recreate the credential.');
                        }
                        
                        // Get credential data
                        const credentialData = JSON.parse(webauthnCredentialId.dataset.credential);
                        
                        // Create a mock credential object with PRF results for SafeCrypto
                        const prfOutput = base64ToBuffer(prfBase64);

                        
                        const mockCredential = {
                            id: credentialId,
                            clientExtensionResults: {
                                prf: {
                                    results: {
                                        first: prfOutput
                                    }
                                }
                            }
                        };
                        
                        // Create safe with WebAuthn (using stored PRF output)
                        safeData = await SafeCrypto.createSafeWithWebAuthn(
                            name, 
                            credentialId, 
                            mockCredential
                        );
                        
                        // Include credential data for server storage
                        safeData.credential_data = credentialData;
                        safeData.credential_challenge = webauthnCredentialId.dataset.challenge;
                        safeData.credential_name = webauthnCredentialId.dataset.keyName || 'Hardware Key';
                        
                    } else {
                        // Create safe with password
                        const password = passwordInput?.value;
                        if (!password || password.length < 8) {
                            throw new Error('Password must be at least 8 characters');
                        }
                        safeData = await SafeCrypto.createSafeWithPassword(name, password);
                    }
                    
                    const resp = await csrfFetch(`${getBaseUrl()}/api/safes`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(safeData)
                    });
                    
                    if (!resp.ok) {
                        const errorData = await resp.json().catch(() => ({}));
                        throw new Error(errorData.detail || 'Failed to create safe');
                    }
                    
                    closeSafeModal();
                    if (typeof loadFolderTree === 'function') {
                        loadFolderTree();
                    }
                } catch (err) {
                    console.error('Failed to create safe:', err);
                    alert('Failed to create safe: ' + err.message);
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Create Safe';
                    }
                }
            });
        }
    });

    // Safe Edit Modal functions
    let currentEditingSafeId = null;

    // Open edit safe modal
    window.openSafeEditModal = async function(safeId) {
        // Try to find safe in local cache first
        let safe = userSafes.find(s => s.id === safeId);
        
        // If not found, fetch from server
        if (!safe) {

            try {
                const resp = await csrfFetch(`${getBaseUrl()}/api/safes/${safeId}`);
                if (resp.ok) {
                    safe = await resp.json();
                    // Add to local cache
                    userSafes.push(safe);
                } else {
                    console.error('[safes.js] Failed to fetch safe:', await resp.text());
                    alert('Safe not found');
                    return;
                }
            } catch (err) {
                console.error('[safes.js] Error fetching safe:', err);
                alert('Failed to load safe');
                return;
            }
        }
        
        currentEditingSafeId = safeId;
        
        const modal = document.getElementById('safe-edit-modal');
        const nameInput = document.getElementById('safe-edit-name-input');
        
        if (nameInput) nameInput.value = safe.name || '';
        if (modal) modal.classList.remove('hidden');
        
        // Register with BackButtonManager
        if (window.BackButtonManager) {
            window.BackButtonManager.register('safe-edit-modal', closeSafeEditModal);
        }
    };

    // Close edit safe modal
    window.closeSafeEditModal = function() {
        // Unregister from BackButtonManager
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('safe-edit-modal', true);
        }
        
        const modal = document.getElementById('safe-edit-modal');
        if (modal) modal.classList.add('hidden');
        
        currentEditingSafeId = null;
    };

    // Navigate to safe root folder
    window.navigateToSafeRoot = async function(safeId) {

        
        // Check if safe is unlocked on client
        const safeKey = safeKeyStorage.get(safeId);
        if (!safeKey) {
            // Safe is locked - open unlock modal
            const safe = userSafes.find(s => s.id === safeId);
            if (safe) {
                window.openSafeUnlock(safeId, safe.name, safe.unlock_type);
            }
            return;
        }
        
        // Find the root folder for this safe
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/folders?safe_id=${safeId}&parent_id=null`);
            const data = await resp.json();
            
            if (data.folders && data.folders.length > 0) {
                const rootFolder = data.folders[0];
                // Navigate to the folder
                if (typeof navigateToFolder === 'function') {
                    navigateToFolder(rootFolder.id, rootFolder.name);
                } else {
                    window.location.href = `${getBaseUrl()}/gallery?folder=${rootFolder.id}`;
                }
            } else {
                alert('Safe root folder not found');
            }
        } catch (err) {
            console.error('[safes.js] Error navigating to safe root:', err);
            alert('Failed to navigate to safe');
        }
    };

    // Rename safe
    async function renameSafe() {
        if (!currentEditingSafeId) return;
        
        const nameInput = document.getElementById('safe-edit-name-input');
        const submitBtn = document.getElementById('safe-edit-submit-btn');
        const name = nameInput?.value.trim();
        
        if (!name) {
            alert('Please enter a safe name');
            return;
        }
        
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Saving...';
        }
        
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/safes/${currentEditingSafeId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name })
            });
            
            if (!resp.ok) throw new Error('Failed to rename safe');
            
            closeSafeEditModal();
            // Reload folder tree to show new name
            if (typeof loadFolderTree === 'function') {
                loadFolderTree();
            }
        } catch (err) {
            console.error('[safes.js] Rename failed:', err);
            alert('Failed to rename safe: ' + err.message);
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Save';
            }
        }
    }

    // Delete safe
    async function deleteSafe() {
        if (!currentEditingSafeId) return;
        
        if (!confirm('Are you sure you want to delete this safe and all its contents? This cannot be undone.')) {
            return;
        }
        
        const deleteBtn = document.getElementById('delete-safe-btn');
        if (deleteBtn) {
            deleteBtn.disabled = true;
            deleteBtn.textContent = 'Deleting...';
        }
        
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/safes/${currentEditingSafeId}`, {
                method: 'DELETE'
            });
            
            if (!resp.ok) throw new Error('Failed to delete safe');
            
            closeSafeEditModal();
            // Clear DEK from SafeCrypto
            if (typeof SafeCrypto !== 'undefined' && SafeCrypto.lockSafe) {
                SafeCrypto.lockSafe(currentEditingSafeId);
            }
            // Reload folder tree
            if (typeof loadFolderTree === 'function') {
                loadFolderTree();
            }
            // Navigate to default folder
            if (typeof navigateToDefaultFolder === 'function') {
                navigateToDefaultFolder();
            }
        } catch (err) {
            console.error('[safes.js] Delete failed:', err);
            alert('Failed to delete safe: ' + err.message);
        } finally {
            if (deleteBtn) {
                deleteBtn.disabled = false;
                deleteBtn.textContent = 'Delete Safe';
            }
        }
    }

    // Lock safe
    async function lockSafe() {
        if (!currentEditingSafeId) return;
        
        const lockBtn = document.getElementById('lock-safe-btn');
        if (lockBtn) {
            lockBtn.disabled = true;
            lockBtn.textContent = 'Locking...';
        }
        
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/safes/${currentEditingSafeId}/lock`, {
                method: 'POST'
            });
            
            if (!resp.ok) throw new Error('Failed to lock safe');
            
            // Clear DEK from SafeCrypto
            if (typeof SafeCrypto !== 'undefined' && SafeCrypto.lockSafe) {
                SafeCrypto.lockSafe(currentEditingSafeId);
            }
            
            closeSafeEditModal();
            // Reload folder tree
            if (typeof loadFolderTree === 'function') {
                loadFolderTree();
            }
            // Navigate to default folder
            if (typeof navigateToDefaultFolder === 'function') {
                navigateToDefaultFolder();
            }
        } catch (err) {
            console.error('[safes.js] Lock failed:', err);
            alert('Failed to lock safe: ' + err.message);
        } finally {
            if (lockBtn) {
                lockBtn.disabled = false;
                lockBtn.textContent = 'Lock Safe';
            }
        }
    }

    // Attach edit modal handlers after DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        // Save (rename) button
        const saveBtn = document.getElementById('safe-edit-submit-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', renameSafe);
        }
        
        // Cancel button
        const cancelBtn = document.getElementById('safe-edit-cancel-btn');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', closeSafeEditModal);
        }
        
        // Close button (X)
        const modal = document.getElementById('safe-edit-modal');
        const closeBtn = modal?.querySelector('.close');
        if (closeBtn) {
            closeBtn.addEventListener('click', closeSafeEditModal);
        }
        
        // Delete button
        const deleteBtn = document.getElementById('delete-safe-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', deleteSafe);
        }
        
        // Lock button
        const lockBtn = document.getElementById('lock-safe-btn');
        if (lockBtn) {
            lockBtn.addEventListener('click', lockSafe);
        }
    });

    // Helper functions for WebAuthn
    function base64ToBuffer(base64) {
        if (!base64 || typeof base64 !== 'string') {
            return new ArrayBuffer(0);
        }
        
        // Remove URL-safe chars and add padding
        const base64Std = base64.replace(/-/g, '+').replace(/_/g, '/');
        const padLen = (4 - (base64Std.length % 4)) % 4;
        const padded = base64Std + '='.repeat(padLen);
        
        const binary = atob(padded);
        const buffer = new ArrayBuffer(binary.length);
        const view = new Uint8Array(buffer);
        for (let i = 0; i < binary.length; i++) {
            view[i] = binary.charCodeAt(i);
        }
        return buffer;
    }
    
    function bufferToBase64(buffer) {
        const binary = String.fromCharCode(...new Uint8Array(buffer));
        return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    }

    // Export
    window.userSafes = userSafes;


})();
