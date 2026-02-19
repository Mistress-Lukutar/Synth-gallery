/**
 * Initialization module - Page load setup
 * Phase 7: Initialization
 */

document.addEventListener('DOMContentLoaded', () => {
    console.log('[init.js] DOM ready');

    // Set current folder ID from URL
    const urlParams = new URLSearchParams(window.location.search);
    window.currentFolderId = urlParams.get('folder_id');

    // Load initial folder if provided by server
    if (window.INITIAL_FOLDER_ID && typeof navigateToFolder === 'function') {
        console.log('[init.js] Loading initial folder:', window.INITIAL_FOLDER_ID);
        navigateToFolder(window.INITIAL_FOLDER_ID, false);
    }

    console.log('[init.js] Initialization complete');
});

console.log('[init.js] Loaded');
