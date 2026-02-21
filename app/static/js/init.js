/**
 * Initialization module - Page load setup
 * Phase 7: Initialization
 */

document.addEventListener('DOMContentLoaded', () => {
    console.log('[init.js] DOM ready');

    // Set current folder ID from URL
    const urlParams = new URLSearchParams(window.location.search);
    window.currentFolderId = urlParams.get('folder_id');
    const photoId = urlParams.get('photo_id');

    // Load initial folder if provided by server
    // Wait for gallery element to be present (from gallery.html)
    if (window.INITIAL_FOLDER_ID && typeof navigateToFolder === 'function') {
        const gallery = document.getElementById('gallery');
        if (gallery) {
            console.log('[init.js] Loading initial folder:', window.INITIAL_FOLDER_ID);
            // If photo_id is in URL, open it after folder loads
            if (photoId) {
                console.log('[init.js] Will open photo:', photoId);
                navigateToFolder(window.INITIAL_FOLDER_ID, false).then(() => {
                    // Wait a bit for gallery to render, then open photo
                    setTimeout(() => {
                        if (typeof window.openPhoto === 'function') {
                            window.openPhoto(photoId);
                        }
                    }, 300);
                });
            } else {
                navigateToFolder(window.INITIAL_FOLDER_ID, false);
            }
        } else {
            console.log('[init.js] Gallery not ready, will retry...');
            // Retry after short delay
            setTimeout(() => {
                if (document.getElementById('gallery')) {
                    if (photoId) {
                        navigateToFolder(window.INITIAL_FOLDER_ID, false).then(() => {
                            setTimeout(() => window.openPhoto?.(photoId), 300);
                        });
                    } else {
                        navigateToFolder(window.INITIAL_FOLDER_ID, false);
                    }
                }
            }, 100);
        }
    }

    console.log('[init.js] Initialization complete');
});

console.log('[init.js] Loaded');
