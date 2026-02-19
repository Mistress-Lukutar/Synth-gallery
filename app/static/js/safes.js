/**
 * Safes module - Safe unlock and navigation
 * Phase 6: Safes Module
 */

(function() {
    let userSafes = [];

    // Load safe thumbnails (placeholder for E2E decryption)
    window.loadSafeThumbnails = function() {
        document.querySelectorAll('img[data-safe-thumbnail]').forEach(img => {
            const photoId = img.dataset.safeThumbnail;
            const safeId = img.dataset.safeId;
            // E2E decryption would happen here
            console.log('[safes.js] Would load safe thumbnail for', photoId);
        });
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

    // Export
    window.userSafes = userSafes;

    console.log('[safes.js] Loaded');
})();
