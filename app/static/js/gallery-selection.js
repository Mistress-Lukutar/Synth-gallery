/**
 * Gallery Selection module
 * Phase 2: Selection, Download, Delete
 */

(function() {
    let gallery = null;
    
    // Selection state
    const selectedPhotos = new Set();
    const selectedAlbums = new Set();

    window.selectedPhotos = selectedPhotos;
    window.selectedAlbums = selectedAlbums;

    function init() {
        gallery = document.getElementById('gallery');
        if (!gallery) {
            console.log('[gallery-selection] No gallery element');
            return;
        }
        setupEventListeners();
        console.log('[gallery-selection] Initialized');
    }

    function setupEventListeners() {
        // Click to select
        gallery.addEventListener('click', (e) => {
            const item = e.target.closest('.gallery-item');
            if (!item || e.target.closest('.gallery-link')) return;

            const photoId = item.dataset.photoId;
            const albumId = item.dataset.albumId;

            if (photoId) {
                selectedPhotos.has(photoId) ? selectedPhotos.delete(photoId) : selectedPhotos.add(photoId);
            } else if (albumId) {
                selectedAlbums.has(albumId) ? selectedAlbums.delete(albumId) : selectedAlbums.add(albumId);
            }

            window.updateSelectionUI();
        });

        // Select all
        const selectAllBtn = document.getElementById('select-all-btn');
        if (selectAllBtn) {
            selectAllBtn.addEventListener('click', () => {
                const items = Array.from(gallery.querySelectorAll('.gallery-item'));
                items.forEach(item => {
                    if (item.dataset.hidden === 'true') return;
                    if (item.dataset.itemType === 'album') {
                        selectedAlbums.add(item.dataset.albumId);
                    } else {
                        selectedPhotos.add(item.dataset.photoId);
                    }
                });
                window.updateSelectionUI();
            });
        }

        // Deselect all
        const deselectAllBtn = document.getElementById('deselect-all-btn');
        if (deselectAllBtn) {
            deselectAllBtn.addEventListener('click', () => {
                selectedPhotos.clear();
                selectedAlbums.clear();
                window.updateSelectionUI();
            });
        }

        // Download selected
        const downloadBtn = document.getElementById('download-selected-btn');
        if (downloadBtn) {
            downloadBtn.addEventListener('click', async () => {
                if (selectedPhotos.size === 0 && selectedAlbums.size === 0) return;

                downloadBtn.disabled = true;
                const originalText = downloadBtn.textContent;
                downloadBtn.textContent = 'Preparing...';

                try {
                    const resp = await csrfFetch(`${getBaseUrl()}/api/photos/batch-download`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ photo_ids: Array.from(selectedPhotos) })
                    });

                    if (!resp.ok) throw new Error('Download failed');

                    const blob = await resp.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = `photos-${Date.now()}.zip`;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    window.URL.revokeObjectURL(url);
                } catch (err) {
                    console.error('Download error:', err);
                    alert('Download failed: ' + err.message);
                } finally {
                    downloadBtn.disabled = false;
                    downloadBtn.textContent = originalText;
                }
            });
        }

        // Delete selected
        const deleteBtn = document.getElementById('delete-selected-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', async () => {
                const total = selectedPhotos.size + selectedAlbums.size;
                if (total === 0) return;
                if (!confirm(`Delete ${total} items?`)) return;

                // Delete photos
                for (const photoId of selectedPhotos) {
                    try {
                        await csrfFetch(`${getBaseUrl()}/api/photos/${photoId}`, { method: 'DELETE' });
                    } catch (err) {
                        console.error('Failed to delete photo:', photoId);
                    }
                }

                // Delete albums
                for (const albumId of selectedAlbums) {
                    try {
                        await csrfFetch(`${getBaseUrl()}/api/albums/${albumId}`, { method: 'DELETE' });
                    } catch (err) {
                        console.error('Failed to delete album:', albumId);
                    }
                }

                selectedPhotos.clear();
                selectedAlbums.clear();
                window.updateSelectionUI();

                // Refresh gallery
                if (window.currentFolderId && typeof navigateToFolder === 'function') {
                    navigateToFolder(window.currentFolderId, false);
                }
            });
        }
    }

    window.updateSelectionUI = function() {
        const selectionMenu = document.getElementById('selection-menu');
        const selectionCount = document.getElementById('selection-count');
        const total = selectedPhotos.size + selectedAlbums.size;

        document.querySelectorAll('.gallery-item').forEach(item => {
            const photoId = item.dataset.photoId;
            const albumId = item.dataset.albumId;
            const isSelected = photoId ? selectedPhotos.has(photoId) : selectedAlbums.has(albumId);
            item.classList.toggle('selected', isSelected);
        });

        if (selectionCount) selectionCount.textContent = total;
        if (selectionMenu) {
            selectionMenu.classList.toggle('hidden', total === 0);
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-selection.js] Loaded');
})();
