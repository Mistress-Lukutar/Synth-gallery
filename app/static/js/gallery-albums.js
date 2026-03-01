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
            // Support both new items array and legacy photos array
            const albumItems = album.items || album.photos || [];
            console.log('[gallery-albums] Album loaded:', album.name, 'items:', albumItems.length);
            
            if (albumItems.length === 0) {
                console.log('[gallery-albums] Album is empty');
                return;
            }
            
            // Store album items for navigation
            currentAlbumPhotos = albumItems.map(item => ({
                id: item.id,
                safeId: item.safe_id,
                original_name: item.original_name || item.title || '',
                filename: item.filename || item.title || '',
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
            
            // Register with BackButtonManager for mobile back button support
            if (window.BackButtonManager) {
                window.BackButtonManager.register('album-editor', () => window.closeAlbumEditor());
            }
        } catch (err) {
            console.error('Failed to open album editor:', err);
        }
    };

    window.closeAlbumEditor = function(skipRefresh = false) {
        // Check if lightbox is open - if so, never refresh folder (stay in lightbox context)
        const lightbox = document.getElementById('lightbox');
        const isLightboxOpen = lightbox && !lightbox.classList.contains('hidden');
        
        // Unregister from BackButtonManager first
        // skipHistoryBack = true because closing via button should NOT trigger history.back()
        // (the popstate will be handled by the manager if user presses back button)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('album-editor', true);
        }
        
        const panel = document.getElementById('album-editor-panel') || albumEditorPanel;
        if (panel) panel.classList.remove('open');
        // Remove panel-open class from lightbox
        if (lightbox) lightbox.classList.remove('panel-open');
        editingAlbumId = null;
        selectedPhotosForAlbum.clear();
        
        // Refresh current folder only if:
        // 1. Not explicitly skipped
        // 2. Lightbox is NOT open (we're in gallery view)
        // 3. We have a current folder
        if (!skipRefresh && !isLightboxOpen && window.currentFolderId && typeof navigateToFolder === 'function') {
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
            // Use main album endpoint which includes items
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}`);
            if (!resp.ok) throw new Error('Failed to load album items');
            
            const data = await resp.json();
            // Support both new items array and legacy photos array
            const items = data.items || data.photos || [];
            
            if (countEl) countEl.textContent = `(${items.length})`;
            
            // Use first item as default cover if none specified
            const effectiveCoverId = currentCoverId || (items[0] && items[0].id);
            
            // Render items in grid layout with cover selection
            let html = items.map((item, index) => {
                const isCover = item.id === effectiveCoverId;
                const displayName = item.original_name || item.filename || item.title || '';
                return `
                    <div class="photo-grid-item${isCover ? ' is-cover' : ''}" data-item-id="${item.id}" draggable="true" data-index="${index}" onclick="handleItemClick(event, '${item.id}')" title="${isCover ? 'Current cover' : 'Click to set as cover'}">
                        <img src="${getBaseUrl()}/files/${item.id}/thumbnail" alt="${escapeHtml(displayName)}">
                        <button class="remove-btn" onclick="removeItemFromAlbum(event, '${item.id}')" title="Remove from album">&times;</button>
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

    window.handleItemClick = async function(event, itemId) {
        // Don't trigger if clicking remove button
        if (event.target.closest('.remove-btn')) return;
        
        // Set as cover
        if (editingAlbumId) {
            await setAlbumCover(editingAlbumId, itemId);
        }
    };

    // Phase 5: Legacy alias
    window.handlePhotoClick = window.handleItemClick;

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
        
        // Get all item ids (exclude the add button)
        const itemIds = Array.from(container.querySelectorAll('.photo-grid-item:not(.add-item)'))
            .map(item => item.dataset.photoId);
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/reorder`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_ids: itemIds })
            });
        } catch (err) {
            console.error('Failed to save album order:', err);
        }
    }

    window.removeItemFromAlbum = async function(event, itemId) {
        event.stopPropagation();
        if (!editingAlbumId) return;
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/items`, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_ids: [itemId] })
            });
            // Refresh - need to get current cover from album
            const resp = await fetch(`${getBaseUrl()}/api/albums/${editingAlbumId}`);
            const album = resp.ok ? await resp.json() : { cover_item_id: null };
            loadAlbumPhotos(editingAlbumId, album.cover_item_id || album.cover_photo_id);
        } catch (err) {
            console.error('Failed to remove item:', err);
        }
    };

    // Phase 5: Legacy alias
    window.removePhotoFromAlbum = window.removeItemFromAlbum;

    window.openAddPhotosModal = async function() {
        if (!editingAlbumId || !addPhotosModal) return;
        
        selectedPhotosForAlbum.clear();
        updateSelectedCount();
        
        // Load available items from current folder
        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders/${window.currentFolderId}/content`);
            if (!resp.ok) throw new Error('Failed to load items');
            
            const data = await resp.json();
            // Support both new polymorphic items (type: 'item' + item_type: 'media') and legacy (type: 'photo')
            availablePhotos = (data.items || [])
                .filter(item => item.type === 'photo' || (item.type === 'item' && item.item_type === 'media'))
                .map(item => ({ 
                    id: item.id, 
                    original_name: item.original_name || item.filename || item.title || '' 
                }));
            
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
                <img src="${getBaseUrl()}/files/${photo.id}/thumbnail" alt="${escapeHtml(photo.original_name)}">
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
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_ids: Array.from(selectedPhotosForAlbum) })
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

    window.setAlbumCover = async function(albumId, itemId) {
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/albums/${albumId}/cover`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_id: itemId })
            });

            if (!resp.ok) throw new Error('Failed to set cover');
            
            // Refresh items grid to show new cover
            loadAlbumPhotos(albumId, itemId);
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
