/**
 * Gallery module - Masonry layout, selection, download
 * Phase 5: Gallery Module
 */

(function() {
    const gallery = document.getElementById('gallery');
    if (!gallery) return;

    const MIN_COLUMN_WIDTH = 280;
    let allItems = [];

    // Selection state
    const selectedPhotos = new Set();
    const selectedAlbums = new Set();

    // Masonry rebuild
    window.rebuildMasonry = function(force = false) {
        allItems = Array.from(gallery.querySelectorAll('.gallery-item'));
        
        if (allItems.length === 0) {
            gallery.innerHTML = `
                <div class="empty-state">
                    <p>No photos yet</p>
                    <p>Click "Upload" to add your first photos</p>
                </div>
            `;
            return;
        }

        // Simple masonry: CSS columns
        gallery.style.columnCount = Math.max(2, Math.floor(gallery.clientWidth / MIN_COLUMN_WIDTH));
        gallery.style.opacity = '1';
    };

    // Update selection UI
    function updateSelectionUI() {
        const selectionMenu = document.getElementById('selection-menu');
        const selectionCount = document.getElementById('selection-count');
        const total = selectedPhotos.size + selectedAlbums.size;

        document.querySelectorAll('.gallery-item').forEach(item => {
            const id = item.dataset.photoId || item.dataset.albumId;
            const isPhoto = !!item.dataset.photoId;
            const isSelected = isPhoto ? selectedPhotos.has(id) : selectedAlbums.has(id);
            item.classList.toggle('selected', isSelected);
        });

        if (selectionCount) selectionCount.textContent = total;
        if (selectionMenu) {
            selectionMenu.classList.toggle('hidden', total === 0);
        }
    }

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

        updateSelectionUI();
    });

    // Select all
    const selectAllBtn = document.getElementById('select-all-btn');
    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
            allItems.forEach(item => {
                if (item.dataset.itemType === 'album') {
                    selectedAlbums.add(item.dataset.albumId);
                } else {
                    selectedPhotos.add(item.dataset.photoId);
                }
            });
            updateSelectionUI();
        });
    }

    // Deselect all
    const deselectAllBtn = document.getElementById('deselect-all-btn');
    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
            selectedPhotos.clear();
            selectedAlbums.clear();
            updateSelectionUI();
        });
    }

    // Download selected
    const downloadBtn = document.getElementById('download-selected-btn');
    if (downloadBtn) {
        downloadBtn.addEventListener('click', async () => {
            if (selectedPhotos.size === 0 && selectedAlbums.size === 0) return;

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
                a.click();
                window.URL.revokeObjectURL(url);
            } catch (err) {
                console.error('Download error:', err);
                alert('Download failed: ' + err.message);
            }
        });
    }

    // Delete selected
    const deleteBtn = document.getElementById('delete-selected-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', async () => {
            if (!confirm(`Delete ${selectedPhotos.size + selectedAlbums.size} items?`)) return;

            for (const photoId of selectedPhotos) {
                try {
                    await csrfFetch(`${getBaseUrl()}/api/photos/${photoId}`, { method: 'DELETE' });
                } catch (err) {
                    console.error('Failed to delete:', photoId);
                }
            }

            selectedPhotos.clear();
            selectedAlbums.clear();
            updateSelectionUI();

            if (window.currentFolderId) {
                navigateToFolder(window.currentFolderId, false);
            }
        });
    }

    // Export for other modules
    window.selectedPhotos = selectedPhotos;
    window.selectedAlbums = selectedAlbums;
    window.allItems = allItems;

    // Init masonry after images load
    window.addEventListener('load', () => rebuildMasonry());

    console.log('[gallery.js] Loaded');
})();
