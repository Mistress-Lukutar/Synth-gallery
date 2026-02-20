/**
 * Safes module - Safe unlock and navigation
 * Phase 6: Safes Module
 */

(function() {
    let userSafes = [];

    // Load safe thumbnails
    window.loadSafeThumbnails = async function() {
        const images = document.querySelectorAll('img[data-safe-thumbnail]');
        if (images.length === 0) return;
        
        console.log('[safes.js] Loading', images.length, 'safe thumbnails');
        
        for (const img of images) {
            const photoId = img.dataset.safeThumbnail;
            const safeId = img.dataset.safeId;
            
            if (!photoId || !safeId) continue;
            
            try {
                // Check if safe is unlocked via SafeCrypto
                if (typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && SafeCrypto.isUnlocked(safeId)) {
                    // Fetch encrypted thumbnail
                    const resp = await fetch(`${getBaseUrl()}/thumbnails/${photoId}.jpg?safe=${safeId}`);
                    if (resp.ok) {
                        const encryptedBlob = await resp.blob();
                        
                        // Decrypt using SafeCrypto
                        try {
                            const decryptedBlob = await SafeCrypto.decryptFileFromSafe(
                                encryptedBlob, 
                                safeId, 
                                'image/jpeg'
                            );
                            img.src = URL.createObjectURL(decryptedBlob);
                            img.style.opacity = '1';
                            if (img.previousElementSibling?.classList.contains('gallery-placeholder')) {
                                img.previousElementSibling.style.display = 'none';
                            }
                        } catch (decryptErr) {
                            console.error('[safes.js] Decrypt failed:', decryptErr);
                            img.style.opacity = '0.3';
                        }
                        continue;
                    }
                }
                
                // Safe locked or failed to load
                console.log('[safes.js] Safe locked or failed for photo:', photoId);
                img.style.opacity = '0.3';
                
            } catch (err) {
                console.error('[safes.js] Failed to load safe thumbnail:', err);
                img.style.opacity = '0.3';
            }
        }
    };

    // Navigate to safe
    window.navigateToSafe = async function(safeId) {
        console.log('[safes.js] Navigating to safe:', safeId);
        // Implementation depends on SafeCrypto
        // For now, just log
    };

    // Open safe unlock modal
    window.openSafeUnlock = function(safeId, safeName, unlockType) {
        const modal = document.getElementById('safe-unlock-modal');
        const title = document.getElementById('safe-unlock-title');
        const passwordInput = document.getElementById('safe-unlock-password-input');

        currentUnlockSafeId = safeId;
        
        if (title) title.textContent = `Unlock ${safeName || 'Safe'}`;
        if (modal) {
            modal.dataset.safeId = safeId;
            modal.classList.remove('hidden');
        }
        
        // Focus password input
        if (passwordInput) {
            passwordInput.value = '';
            setTimeout(() => passwordInput.focus(), 100);
        }
    };

    // Close safe unlock modal
    window.closeSafeUnlockModal = function() {
        const modal = document.getElementById('safe-unlock-modal');
        if (modal) modal.classList.add('hidden');
        currentUnlockSafeId = null;
    };

    // Current safe being unlocked
    let currentUnlockSafeId = null;

    // Handle safe unlock form submit
    document.addEventListener('DOMContentLoaded', () => {
        const unlockForm = document.getElementById('safe-unlock-form');
        if (unlockForm) {
            unlockForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                
                if (!currentUnlockSafeId) {
                    // Get from modal dataset if not set
                    const modal = document.getElementById('safe-unlock-modal');
                    currentUnlockSafeId = modal?.dataset.safeId;
                }
                
                if (!currentUnlockSafeId) {
                    console.error('[safes.js] No safe ID for unlock');
                    return;
                }

                const submitBtn = document.getElementById('safe-unlock-submit-btn');
                const passwordInput = document.getElementById('safe-unlock-password-input');
                
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Unlocking...';
                }

                try {
                    const password = passwordInput?.value;
                    if (!password) {
                        alert('Please enter password');
                        return;
                    }

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
                    console.log('[safes.js] Challenge data:', challengeData);

                    if (challengeData.status !== 'challenge') {
                        throw new Error(`Unexpected status: ${challengeData.status}`);
                    }

                    // Unlock with password using SafeCrypto
                    const unlockData = await SafeCrypto.unlockWithPassword(
                        currentUnlockSafeId,
                        password,
                        challengeData.encrypted_dek,
                        challengeData.salt
                    );

                    // Complete unlock on server
                    await csrfFetch(`${getBaseUrl()}/api/safes/unlock/complete`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            safe_id: currentUnlockSafeId,
                            session_encrypted_dek: unlockData.session_encrypted_dek
                        })
                    });

                    // Store locally
                    await SafeCrypto.storeSafeDEKFromSession(
                        currentUnlockSafeId,
                        unlockData.session_encrypted_dek,
                        unlockData.session_key
                    );

                    closeSafeUnlockModal();
                    
                    // Reload folder tree to show unlocked safe contents
                    if (typeof loadFolderTree === 'function') {
                        loadFolderTree();
                    }
                    
                    // Refresh thumbnails
                    if (typeof loadSafeThumbnails === 'function') {
                        loadSafeThumbnails();
                    }
                    
                } catch (err) {
                    console.error('[safes.js] Unlock failed:', err);
                    alert('Unlock failed: ' + err.message);
                } finally {
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.textContent = 'Unlock';
                    }
                }
            });
        }
    });

    // Open create safe modal
    window.openCreateSafe = function() {
        const modal = document.getElementById('safe-modal');
        if (modal) modal.classList.remove('hidden');
    };

    // Close safe modal
    window.closeSafeModal = function() {
        const modal = document.getElementById('safe-modal');
        if (modal) modal.classList.add('hidden');
    };

    // Create safe form handler - attach after DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        const safeForm = document.getElementById('safe-form');
        if (safeForm) {
            safeForm.addEventListener('submit', async (e) => {
                e.preventDefault();
                
                const nameInput = document.getElementById('safe-name-input');
                const passwordInput = document.getElementById('safe-password-input');
                const submitBtn = document.getElementById('safe-submit-btn');
                
                const name = nameInput?.value.trim();
                const password = passwordInput?.value;
                
                if (!name) {
                    alert('Please enter a safe name');
                    return;
                }
                if (!password || password.length < 8) {
                    alert('Password must be at least 8 characters');
                    return;
                }
                
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Creating...';
                }
                
                try {
                    const resp = await csrfFetch(`${getBaseUrl()}/api/safes`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            name: name,
                            password: password,
                            unlock_type: 'password'
                        })
                    });
                    
                    if (!resp.ok) throw new Error('Failed to create safe');
                    
                    closeSafeModal();
                    // Reload to show new safe
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

    // Export
    window.userSafes = userSafes;

    console.log('[safes.js] Loaded');
})();
