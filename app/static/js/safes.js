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
                    // Safe is unlocked, fetch decrypted thumbnail
                    const resp = await fetch(`${getBaseUrl()}/thumbnails/${photoId}.jpg?safe=${safeId}`);
                    if (resp.ok) {
                        const blob = await resp.blob();
                        img.src = URL.createObjectURL(blob);
                        img.style.opacity = '1';
                        if (img.previousElementSibling?.classList.contains('gallery-placeholder')) {
                            img.previousElementSibling.style.display = 'none';
                        }
                        continue;
                    }
                }
                
                // Safe locked or failed to load - show placeholder or lock icon
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

        if (title) title.textContent = `Unlock ${safeName || 'Safe'}`;
        if (modal) {
            modal.dataset.safeId = safeId;
            modal.classList.remove('hidden');
        }
    };

    // Close safe unlock modal
    window.closeSafeUnlockModal = function() {
        const modal = document.getElementById('safe-unlock-modal');
        if (modal) modal.classList.add('hidden');
    };

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
