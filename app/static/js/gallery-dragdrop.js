/**
 * Gallery Drag & Drop module
 * Phase 5: Drag and drop functionality
 */

(function() {
    let gallery = null;
    let draggedItem = null;

    function init() {
        gallery = document.getElementById('gallery');
        if (!gallery) {
            console.log('[gallery-dragdrop] No gallery');
            return;
        }

        setupEventListeners();
        console.log('[gallery-dragdrop] Initialized');
    }

    function setupEventListeners() {
        // Make gallery items draggable
        gallery.addEventListener('dragstart', (e) => {
            const item = e.target.closest('.gallery-item');
            if (!item) return;

            draggedItem = item;
            item.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
        });

        gallery.addEventListener('dragend', (e) => {
            const item = e.target.closest('.gallery-item');
            if (item) {
                item.classList.remove('dragging');
            }
            draggedItem = null;
        });

        gallery.addEventListener('dragover', (e) => {
            e.preventDefault();
            if (!draggedItem) return;

            const albumItem = e.target.closest('.gallery-item[data-item-type="album"]');
            if (albumItem && albumItem !== draggedItem) {
                albumItem.classList.add('drag-over');
            }
        });

        gallery.addEventListener('dragleave', (e) => {
            const albumItem = e.target.closest('.gallery-item[data-item-type="album"]');
            if (albumItem) {
                albumItem.classList.remove('drag-over');
            }
        });

        gallery.addEventListener('drop', async (e) => {
            e.preventDefault();
            if (!draggedItem) return;

            const albumItem = e.target.closest('.gallery-item[data-item-type="album"]');
            if (!albumItem || albumItem === draggedItem) return;

            albumItem.classList.remove('drag-over');

            const photoId = draggedItem.dataset.photoId;
            const albumId = albumItem.dataset.albumId;

            if (photoId && albumId) {
                await window.addPhotoToAlbum(photoId, albumId);
            }
        });
    }

    window.addPhotoToAlbum = async function(photoId, albumId) {
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/albums/${albumId}/photos`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ photo_ids: [photoId] })
            });

            if (!resp.ok) throw new Error('Failed to add photo to album');

            // Visual feedback
            const photoEl = document.querySelector(`.gallery-item[data-photo-id="${photoId}"]`);
            if (photoEl) {
                photoEl.style.opacity = '0.5';
                setTimeout(() => {
                    photoEl.style.opacity = '';
                }, 500);
            }
        } catch (err) {
            console.error('Failed to add photo to album:', err);
            alert('Failed to add photo to album');
        }
    };

    window.movePhotosToFolder = async function(photoIds, targetFolderId) {
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/photos/move`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    photo_ids: photoIds,
                    target_folder_id: targetFolderId
                })
            });

            if (!resp.ok) throw new Error('Failed to move photos');

            // Refresh gallery
            if (window.currentFolderId && typeof navigateToFolder === 'function') {
                navigateToFolder(window.currentFolderId, false);
            }
        } catch (err) {
            console.error('Failed to move photos:', err);
            alert('Failed to move photos: ' + err.message);
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-dragdrop.js] Loaded');
})();
