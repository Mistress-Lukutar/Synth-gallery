/**
 * Gallery Albums module
 * Phase 6: Album management
 */

(function() {
    let albumModal = null;
    let editingAlbumId = null;

    function init() {
        albumModal = document.getElementById('album-modal');
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

    window.closeAlbumModal = function() {
        if (albumModal) albumModal.classList.add('hidden');
        editingAlbumId = null;
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
