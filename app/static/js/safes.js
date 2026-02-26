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
        
        console.log('[safes.js] Loading', images.length, 'safe thumbnails');
        
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
        console.log('[safes.js] Navigating to safe:', safeId);
        if (!window.folderTree || typeof navigateToFolder !== 'function') return;
        
        const safeRoot = window.folderTree.find(f => f.safe_id === safeId && !f.parent_id);
        if (safeRoot) {
            navigateToFolder(safeRoot.id, false);
        }
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

                    // Save safeId before closing modal (closeSafeUnlockModal clears it)
                    const unlockedSafeId = currentUnlockSafeId;
                    closeSafeUnlockModal();
                    
                    // Reload folder tree and then navigate to safe root
                    console.log('[safes.js] Unlock successful, safeId:', unlockedSafeId);
                    if (typeof loadFolderTree === 'function') {
                        await loadFolderTree();
                    }
                    
                    // Navigate to safe root folder after folder tree is updated
                    console.log('[safes.js] Folder tree after reload:', window.folderTree?.length, 'folders');
                    console.log('[safes.js] Looking for safe root, safe_id:', unlockedSafeId);
                    
                    if (typeof navigateToFolder === 'function' && window.folderTree) {
                        // List all folders with this safe_id
                        const safeFolders = window.folderTree.filter(f => f.safe_id === unlockedSafeId);
                        console.log('[safes.js] Found folders with this safe_id:', safeFolders.map(f => ({id: f.id, name: f.name, parent_id: f.parent_id})));
                        
                        const safeRoot = window.folderTree.find(f => f.safe_id === unlockedSafeId && !f.parent_id);
                        console.log('[safes.js] Safe root found:', safeRoot);
                        
                        if (safeRoot) {
                            console.log('[safes.js] Navigating to:', safeRoot.id, safeRoot.name);
                            navigateToFolder(safeRoot.id, true);
                        } else {
                            console.error('[safes.js] Safe root not found!');
                        }
                    } else {
                        console.error('[safes.js] Cannot navigate: navigateToFolder=', typeof navigateToFolder, 'folderTree=', !!window.folderTree);
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
