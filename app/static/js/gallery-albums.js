/**
 * Gallery Albums module
 * Phase 6: Album management
 */

(function() {
    let albumEditorPanel = null;
    let addPhotosModal = null;
    let editingAlbumId = null;
    let availablePhotos = [];
    let selectedPhotosForAlbum = new Set();
    
    // Album viewing state
    let currentAlbumPhotos = [];
    let currentAlbumIndex = 0;

    function init() {
        albumEditorPanel = document.getElementById('album-editor-panel');
        addPhotosModal = document.getElementById('add-photos-modal');
        console.log('[gallery-albums] Initialized, albumEditorPanel:', albumEditorPanel);
    }

    // Handle album click with access check
    window.handleAlbumClick = function(albumId, event) {
        // Find album element
        const gallery = document.getElementById('gallery');
        const albumItem = gallery?.querySelector(`.gallery-item[data-album-id="${albumId}"]`);
        
        if (albumItem) {
            const access = albumItem.dataset.access;
            const safeId = albumItem.dataset.safeId;
            
            if (access === 'denied') {
                // Shared content without access - do nothing
                console.log('[handleAlbumClick] Access denied for album:', albumId);
                return;
            }
            
            if (access === 'locked' && safeId) {
                // Safe is locked - show unlock modal
                console.log('[handleAlbumClick] Safe locked, showing unlock modal for:', safeId);
                
                let safeName = 'Safe';
                let unlockType = 'password';
                if (window.userSafes) {
                    const safe = window.userSafes.find(s => s.id === safeId);
                    if (safe) {
                        safeName = safe.name;
                        unlockType = safe.unlock_type;
                    }
                }
                
                if (typeof openSafeUnlock === 'function') {
                    openSafeUnlock(safeId, safeName, unlockType);
                } else {
                    console.error('[handleAlbumClick] openSafeUnlock not available');
                }
                return;
            }
        }
        
        // Access granted - open album
        openAlbum(albumId);
    };

    // Open album in lightbox (view mode)
    window.openAlbum = async function(albumId, startFromEnd = false) {
        console.log('[gallery-albums] Opening album:', albumId);
        
        try {
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}`);
            if (!resp.ok) throw new Error('Failed to load album');
            
            const album = await resp.json();
            console.log('[gallery-albums] Album loaded:', album.name, 'photos:', album.photos?.length);
            
            if (!album.photos || album.photos.length === 0) {
                console.log('[gallery-albums] Album is empty');
                return;
            }
            
            // Store album photos for navigation
            currentAlbumPhotos = album.photos.map(p => ({
                id: p.id,
                safeId: p.safe_id,
                original_name: p.original_name,
                filename: p.filename,
                albumId: albumId
            }));
            currentAlbumIndex = startFromEnd ? currentAlbumPhotos.length - 1 : 0;
            
            const firstPhoto = currentAlbumPhotos[currentAlbumIndex];
            if (!firstPhoto) return;
            
            // Set up lightbox for album viewing
            window.setAlbumContext(currentAlbumPhotos, currentAlbumIndex);
            
            // Expand album in gallery navigation order so we can navigate beyond the album
            if (typeof window.expandAlbumInLightboxNav === 'function') {
                window.expandAlbumInLightboxNav(albumId, currentAlbumPhotos, currentAlbumIndex);
            }
            
            // Update URL with photo_id
            const url = new URL(window.location.href);
            url.searchParams.set('photo_id', firstPhoto.id);
            window.history.pushState({ photoId: firstPhoto.id }, '', url.toString());
            
            // Load first photo
            await window.loadPhoto(firstPhoto.id);
            window.showLightbox();
            
        } catch (err) {
            console.error('[gallery-albums] Failed to open album:', err);
        }
    };

    // Navigate within album
    window.navigateAlbum = function(direction) {
        if (currentAlbumPhotos.length <= 1) return;
        
        currentAlbumIndex += direction;
        if (currentAlbumIndex < 0) currentAlbumIndex = currentAlbumPhotos.length - 1;
        if (currentAlbumIndex >= currentAlbumPhotos.length) currentAlbumIndex = 0;
        
        const photo = currentAlbumPhotos[currentAlbumIndex];
        if (photo) {
            window.loadPhoto(photo.id);
            window.updateAlbumIndicator?.(currentAlbumIndex, currentAlbumPhotos.length);
        }
    };

    window.openCreateAlbum = function() {
        editingAlbumId = null;
        const title = document.getElementById('album-modal-title');
        const nameInput = document.getElementById('album-name-input');
        
        if (title) title.textContent = 'Create Album';
        if (nameInput) nameInput.value = '';
        if (albumModal) albumModal.classList.remove('hidden');
    };

    window.openEditAlbum = function(albumId) {
        editingAlbumId = albumId;
        const title = document.getElementById('album-modal-title');
        const nameInput = document.getElementById('album-name-input');

        if (title) title.textContent = 'Edit Album';
        // Load album data
        fetch(`${getBaseUrl()}/api/albums/${albumId}`)
            .then(r => r.json())
            .then(data => {
                if (nameInput) nameInput.value = data.name || '';
            });

        if (albumModal) albumModal.classList.remove('hidden');
    };

    // Alias for lightbox compatibility
    window.openAlbumEditor = async function(albumId) {
        console.log('[gallery-albums] openAlbumEditor called for album:', albumId);
        
        // Always try to get fresh reference to panel
        const panel = document.getElementById('album-editor-panel');
        if (!panel) {
            console.error('[gallery-albums] album-editor-panel element not found in DOM');
            return;
        }
        
        // Update module reference
        albumEditorPanel = panel;
        
        editingAlbumId = albumId;
        selectedPhotosForAlbum.clear();
        
        try {
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}`);
            if (!resp.ok) throw new Error('Failed to load album');
            
            const album = await resp.json();
            
            // Update header
            const header = albumEditorPanel.querySelector('h3');
            if (header) header.textContent = `Edit Album: ${album.name || 'Untitled'}`;
            
            // Load photos in album (includes cover selection logic)
            await loadAlbumPhotos(albumId, album.cover_photo_id);
            
            albumEditorPanel.classList.add('open');
            
            // Add panel-open class to lightbox for layout adjustments
            const lightbox = document.getElementById('lightbox');
            if (lightbox) lightbox.classList.add('panel-open');
        } catch (err) {
            console.error('Failed to open album editor:', err);
        }
    };

    window.closeAlbumEditor = function(skipRefresh = false) {
        const panel = document.getElementById('album-editor-panel') || albumEditorPanel;
        if (panel) panel.classList.remove('open');
        // Remove panel-open class from lightbox
        const lightbox = document.getElementById('lightbox');
        if (lightbox) lightbox.classList.remove('panel-open');
        editingAlbumId = null;
        selectedPhotosForAlbum.clear();
        
        // Refresh current folder to show updated album covers and photo counts
        if (!skipRefresh && window.currentFolderId && typeof navigateToFolder === 'function') {
            navigateToFolder(window.currentFolderId, false);
        }
    };

    window.closeAlbumModal = function() {
        if (albumModal) albumModal.classList.add('hidden');
        editingAlbumId = null;
    };

    async function loadAlbumPhotos(albumId, currentCoverId) {
        const container = document.getElementById('album-photos-list');
        const countEl = document.getElementById('album-photo-count');
        if (!container) return;
        
        try {
            // Use main album endpoint which includes photos
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}`);
            if (!resp.ok) throw new Error('Failed to load photos');
            
            const data = await resp.json();
            const photos = data.photos || [];
            
            if (countEl) countEl.textContent = `(${photos.length})`;
            
            // Use first photo as default cover if none specified
            const effectiveCoverId = currentCoverId || (photos[0] && photos[0].id);
            
            // Render photos in grid layout with cover selection
            let html = photos.map((photo, index) => {
                const isCover = photo.id === effectiveCoverId;
                return `
                    <div class="photo-grid-item${isCover ? ' is-cover' : ''}" data-photo-id="${photo.id}" draggable="true" data-index="${index}" onclick="handlePhotoClick(event, '${photo.id}')" title="${isCover ? 'Current cover' : 'Click to set as cover'}">
                        <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" alt="${escapeHtml(photo.filename || '')}">
                        <button class="remove-btn" onclick="removePhotoFromAlbum(event, '${photo.id}')" title="Remove from album">&times;</button>
                    </div>
                `;
            }).join('');
            
            // Add "+" button for adding photos
            html += `
                <div class="photo-grid-item add-item" onclick="openAddPhotosModal()" title="Add Photos">
                    <span class="add-icon">+</span>
                </div>
            `;
            
            container.innerHTML = html;
            
            // Setup drag and drop
            setupDragAndDrop(container);
        } catch (err) {
            console.error('Failed to load album photos:', err);
        }
    }

    window.handlePhotoClick = async function(event, photoId) {
        // Don't trigger if clicking remove button
        if (event.target.closest('.remove-btn')) return;
        
        // Set as cover
        if (editingAlbumId) {
            await setAlbumCover(editingAlbumId, photoId);
        }
    };

    function setupDragAndDrop(container) {
        let draggedItem = null;
        
        // Only make photo items draggable (not the add button)
        container.querySelectorAll('.photo-grid-item:not(.add-item)').forEach(item => {
            item.addEventListener('dragstart', (e) => {
                draggedItem = item;
                item.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
            });
            
            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
                draggedItem = null;
                // Save new order
                saveAlbumOrder();
            });
            
            item.addEventListener('dragover', (e) => {
                e.preventDefault();
                if (!draggedItem || draggedItem === item) return;
                
                const rect = item.getBoundingClientRect();
                const midpoint = rect.left + rect.width / 2;
                
                if (e.clientX < midpoint) {
                    item.before(draggedItem);
                } else {
                    item.after(draggedItem);
                }
            });
        });
    }

    async function saveAlbumOrder() {
        if (!editingAlbumId) return;
        
        const container = document.getElementById('album-photos-list');
        if (!container) return;
        
        // Get all photo items (exclude the add button)
        const photoIds = Array.from(container.querySelectorAll('.photo-grid-item:not(.add-item)'))
            .map(item => item.dataset.photoId);
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/reorder`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ photo_ids: photoIds })
            });
        } catch (err) {
            console.error('Failed to save album order:', err);
        }
    }

    window.removePhotoFromAlbum = async function(event, photoId) {
        event.stopPropagation();
        if (!editingAlbumId) return;
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/photos`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ photo_ids: [photoId] })
            });
            // Refresh - need to get current cover from album
            const resp = await fetch(`${getBaseUrl()}/api/albums/${editingAlbumId}`);
            const album = resp.ok ? await resp.json() : { cover_photo_id: null };
            loadAlbumPhotos(editingAlbumId, album.cover_photo_id);
        } catch (err) {
            console.error('Failed to remove photo:', err);
        }
    };

    window.openAddPhotosModal = async function() {
        if (!editingAlbumId || !addPhotosModal) return;
        
        selectedPhotosForAlbum.clear();
        updateSelectedCount();
        
        // Load available photos from current folder
        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders/${window.currentFolderId}/content`);
            if (!resp.ok) throw new Error('Failed to load photos');
            
            const data = await resp.json();
            availablePhotos = (data.items || [])
                .filter(item => item.type === 'photo')
                .map(item => ({ id: item.id, original_name: item.original_name }));
            
            renderAvailablePhotos();
            addPhotosModal.classList.remove('hidden');
        } catch (err) {
            console.error('Failed to load available photos:', err);
        }
    };

    window.closeAddPhotosModal = function() {
        if (addPhotosModal) addPhotosModal.classList.add('hidden');
        selectedPhotosForAlbum.clear();
    };

    function renderAvailablePhotos() {
        const container = document.getElementById('available-photos-grid');
        if (!container) return;
        
        if (availablePhotos.length === 0) {
            container.innerHTML = '<p class="no-photos">No available photos</p>';
            return;
        }
        
        container.innerHTML = availablePhotos.map(photo => `
            <div class="available-photo-item ${selectedPhotosForAlbum.has(photo.id) ? 'selected' : ''}" 
                 data-photo-id="${photo.id}"
                 onclick="togglePhotoForAlbum('${photo.id}')">
                <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" alt="${escapeHtml(photo.original_name || '')}">
            </div>
        `).join('');
    }

    window.togglePhotoForAlbum = function(photoId) {
        if (selectedPhotosForAlbum.has(photoId)) {
            selectedPhotosForAlbum.delete(photoId);
        } else {
            selectedPhotosForAlbum.add(photoId);
        }
        renderAvailablePhotos();
        updateSelectedCount();
    };

    function updateSelectedCount() {
        const countEl = document.getElementById('selected-photos-count');
        const confirmBtn = document.getElementById('confirm-add-btn');
        
        if (countEl) countEl.textContent = `${selectedPhotosForAlbum.size} selected`;
        if (confirmBtn) confirmBtn.disabled = selectedPhotosForAlbum.size === 0;
    }

    window.confirmAddPhotos = async function() {
        if (!editingAlbumId || selectedPhotosForAlbum.size === 0) return;
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/photos`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ photo_ids: Array.from(selectedPhotosForAlbum) })
            });
            
            closeAddPhotosModal();
            loadAlbumPhotos(editingAlbumId);
        } catch (err) {
            console.error('Failed to add photos:', err);
            alert('Failed to add photos');
        }
    };

    window.saveAlbum = async function() {
        const nameInput = document.getElementById('album-name-input');
        const name = nameInput?.value.trim();

        if (!name) {
            alert('Please enter an album name');
            return;
        }

        try {
            const url = editingAlbumId 
                ? `${getBaseUrl()}/api/albums/${editingAlbumId}`
                : `${getBaseUrl()}/api/albums`;
            
            const resp = await csrfFetch(url, {
                method: editingAlbumId ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    name: name,
                    folder_id: window.currentFolderId
                })
            });

            if (!resp.ok) throw new Error('Failed to save album');

            window.closeAlbumModal();
            
            // Refresh
            if (window.currentFolderId && typeof navigateToFolder === 'function') {
                navigateToFolder(window.currentFolderId, false);
            }
        } catch (err) {
            console.error('Failed to save album:', err);
            alert('Failed to save album: ' + err.message);
        }
    };

    window.deleteAlbum = async function(albumId) {
        // Check if album is in a locked safe (E2E - client-side only!)
        const gallery = document.getElementById('gallery');
        const albumItem = gallery?.querySelector(`.gallery-item[data-album-id="${albumId}"]`);
        if (albumItem) {
            const safeId = albumItem.dataset.safeId;
            if (safeId && typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && !SafeCrypto.isUnlocked(safeId)) {
                alert('Cannot delete: this album is in a locked safe. Please unlock the safe first.');
                return;
            }
        }
        
        if (!confirm('Delete this album? Photos will not be deleted.')) return;

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/albums/${albumId}`, {
                method: 'DELETE'
            });

            if (!resp.ok) throw new Error('Failed to delete album');

            // Refresh
            if (window.currentFolderId && typeof navigateToFolder === 'function') {
                navigateToFolder(window.currentFolderId, false);
            }
        } catch (err) {
            console.error('Failed to delete album:', err);
            alert('Failed to delete album');
        }
    };

    window.setAlbumCover = async function(albumId, photoId) {
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/albums/${albumId}/cover`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ photo_id: photoId })
            });

            if (!resp.ok) throw new Error('Failed to set cover');
            
            // Refresh photos grid to show new cover
            loadAlbumPhotos(albumId, photoId);
        } catch (err) {
            console.error('Failed to set album cover:', err);
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-albums.js] Loaded');
})();
