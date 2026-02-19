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

    function init() {
        albumEditorPanel = document.getElementById('album-editor-panel');
        addPhotosModal = document.getElementById('add-photos-modal');
        console.log('[gallery-albums] Initialized');
    }

    window.openAlbum = function(albumId) {
        // Navigate to album view
        window.location.href = `${getBaseUrl()}/album/${albumId}`;
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
        if (!albumEditorPanel) return;
        
        editingAlbumId = albumId;
        selectedPhotosForAlbum.clear();
        
        try {
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}`);
            if (!resp.ok) throw new Error('Failed to load album');
            
            const album = await resp.json();
            
            // Update header
            const header = albumEditorPanel.querySelector('h3');
            if (header) header.textContent = `Edit Album: ${album.name}`;
            
            // Load photos in album
            await loadAlbumPhotos(albumId);
            
            // Load available cover photos
            await loadCoverPhotos(album.photos || []);
            
            albumEditorPanel.classList.add('open');
            
            // Add panel-open class to lightbox for layout adjustments
            const lightbox = document.getElementById('lightbox');
            if (lightbox) lightbox.classList.add('panel-open');
        } catch (err) {
            console.error('Failed to open album editor:', err);
        }
    };

    window.closeAlbumEditor = function() {
        if (albumEditorPanel) albumEditorPanel.classList.remove('open');
        // Remove panel-open class from lightbox
        const lightbox = document.getElementById('lightbox');
        if (lightbox) lightbox.classList.remove('panel-open');
        editingAlbumId = null;
        selectedPhotosForAlbum.clear();
    };

    window.closeAlbumModal = function() {
        if (albumModal) albumModal.classList.add('hidden');
        editingAlbumId = null;
    };

    async function loadAlbumPhotos(albumId) {
        const container = document.getElementById('album-photos-list');
        const countEl = document.getElementById('album-photo-count');
        if (!container) return;
        
        try {
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}/photos`);
            if (!resp.ok) throw new Error('Failed to load photos');
            
            const data = await resp.json();
            const photos = data.photos || [];
            
            if (countEl) countEl.textContent = `(${photos.length})`;
            
            if (photos.length === 0) {
                container.innerHTML = '<p class="no-photos">No photos in album</p>';
                return;
            }
            
            container.innerHTML = photos.map((photo, index) => `
                <div class="album-photo-item" data-photo-id="${photo.id}" draggable="true" data-index="${index}">
                    <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" alt="${escapeHtml(photo.original_name || '')}">
                    <button class="remove-photo-btn" onclick="removePhotoFromAlbum('${photo.id}')" title="Remove">&times;</button>
                </div>
            `).join('');
            
            // Setup drag and drop
            setupDragAndDrop(container);
        } catch (err) {
            console.error('Failed to load album photos:', err);
        }
    }

    async function loadCoverPhotos(photos) {
        const container = document.getElementById('album-cover-grid');
        if (!container) return;
        
        if (photos.length === 0) {
            container.innerHTML = '<p class="no-photos">Add photos first</p>';
            return;
        }
        
        container.innerHTML = photos.map(photo => `
            <div class="cover-photo-option" data-photo-id="${photo.id}" onclick="setAlbumCover('${editingAlbumId}', '${photo.id}')">
                <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" alt="${escapeHtml(photo.original_name || '')}">
            </div>
        `).join('');
    }

    function setupDragAndDrop(container) {
        let draggedItem = null;
        
        container.querySelectorAll('.album-photo-item').forEach(item => {
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
        
        const photoIds = Array.from(container.querySelectorAll('.album-photo-item'))
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

    window.removePhotoFromAlbum = async function(photoId) {
        if (!editingAlbumId) return;
        if (!confirm('Remove this photo from album?')) return;
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/albums/${editingAlbumId}/photos/${photoId}`, {
                method: 'DELETE'
            });
            // Refresh
            loadAlbumPhotos(editingAlbumId);
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
            
            // Visual feedback
            document.querySelectorAll('.cover-photo-option').forEach(el => {
                el.classList.toggle('selected', el.dataset.photoId === photoId);
            });
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
