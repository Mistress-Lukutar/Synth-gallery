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

        // Move selected
        const moveBtn = document.getElementById('move-selected-btn');
        if (moveBtn) {
            moveBtn.addEventListener('click', async () => {
                const total = selectedPhotos.size + selectedAlbums.size;
                if (total === 0) return;
                
                const targetFolderId = await showFolderPicker('Move to folder');
                if (!targetFolderId) return;

                try {
                    const resp = await csrfFetch(`${getBaseUrl()}/api/items/move`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            photo_ids: Array.from(selectedPhotos),
                            album_ids: Array.from(selectedAlbums),
                            folder_id: targetFolderId
                        })
                    });

                    if (!resp.ok) throw new Error('Move failed');
                    
                    selectedPhotos.clear();
                    selectedAlbums.clear();
                    window.updateSelectionUI();
                    
                    if (window.currentFolderId && typeof navigateToFolder === 'function') {
                        navigateToFolder(window.currentFolderId, false);
                    }
                } catch (err) {
                    console.error('Move failed:', err);
                    alert('Move failed: ' + err.message);
                }
            });
        }

        // Copy selected
        const copyBtn = document.getElementById('copy-selected-btn');
        if (copyBtn) {
            copyBtn.addEventListener('click', async () => {
                const total = selectedPhotos.size + selectedAlbums.size;
                if (total === 0) return;
                
                const targetFolderId = await showFolderPicker('Copy to folder');
                if (!targetFolderId) return;

                try {
                    const resp = await csrfFetch(`${getBaseUrl()}/api/items/copy`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            photo_ids: Array.from(selectedPhotos),
                            album_ids: Array.from(selectedAlbums),
                            folder_id: targetFolderId
                        })
                    });

                    if (!resp.ok) throw new Error('Copy failed');
                    
                    selectedPhotos.clear();
                    selectedAlbums.clear();
                    window.updateSelectionUI();
                    
                    if (window.currentFolderId && typeof navigateToFolder === 'function') {
                        navigateToFolder(window.currentFolderId, false);
                    }
                } catch (err) {
                    console.error('Copy failed:', err);
                    alert('Copy failed: ' + err.message);
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

                try {
                    const resp = await csrfFetch(`${getBaseUrl()}/api/photos/batch-delete`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            photo_ids: Array.from(selectedPhotos),
                            album_ids: Array.from(selectedAlbums)
                        })
                    });

                    if (!resp.ok) throw new Error('Delete failed');
                    
                    const result = await resp.json();
                    console.log('Delete result:', result);

                    selectedPhotos.clear();
                    selectedAlbums.clear();
                    window.updateSelectionUI();

                    // Refresh gallery
                    if (window.currentFolderId && typeof navigateToFolder === 'function') {
                        navigateToFolder(window.currentFolderId, false);
                    }
                } catch (err) {
                    console.error('Delete failed:', err);
                    alert('Delete failed: ' + err.message);
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

    // Build folder tree HTML for picker (similar to sidebar)
    function buildPickerTreeHTML(parentId, level, folders) {
        const children = folders.filter(f => f.parent_id === parentId && f.permission === 'owner');
        if (children.length === 0) return '';

        return children.map(folder => {
            const hasChildren = folders.some(f => f.parent_id === folder.id);
            const photoCount = folder.photo_count || 0;
            
            const expandArrow = hasChildren ? `
                <span class="folder-expand-placeholder"></span>
            ` : '<span class="folder-expand-placeholder"></span>';
            
            return `
                <div class="folder-item-wrapper picker-folder-item" style="padding-left: ${level * 16}px">
                    ${expandArrow}
                    <div class="folder-item"
                         data-folder-id="${folder.id}"
                         onclick="selectFolderForPicker('${folder.id}')">
                        <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        </svg>
                        <span class="folder-name">${escapeHtml(folder.name)}</span>
                        <span class="folder-count">${photoCount}</span>
                    </div>
                </div>
            ` + buildPickerTreeHTML(folder.id, level + 1, folders);
        }).join('');
    }

    // Show folder picker modal
    async function showFolderPicker(title) {
        // Create modal if not exists
        let modal = document.getElementById('folder-picker-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'folder-picker-modal';
            modal.className = 'modal hidden';
            modal.innerHTML = `
                <div class="modal-content folder-picker-content">
                    <span class="close" onclick="closeFolderPicker()">&times;</span>
                    <h3 id="folder-picker-title">Select Folder</h3>
                    <div id="folder-picker-list" class="folder-picker-list"></div>
                    <div class="modal-actions">
                        <button class="btn btn-secondary" onclick="closeFolderPicker()">Cancel</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
        }

        document.getElementById('folder-picker-title').textContent = title;
        const listEl = document.getElementById('folder-picker-list');
        listEl.innerHTML = '<p>Loading folders...</p>';
        modal.classList.remove('hidden');

        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders`);
            if (!resp.ok) throw new Error('Failed to load folders');
            const folders = await resp.json();
            
            console.log('[folder picker] Loaded folders:', folders?.length);

            if (!folders || folders.length === 0) {
                listEl.innerHTML = '<p>No folders available</p>';
                return null;
            }

            // Build tree like sidebar
            const html = '<div class="folder-section"><div class="folder-section-header">My Folders</div>' + 
                        buildPickerTreeHTML(null, 0, folders) + '</div>';
            listEl.innerHTML = html;
        } catch (err) {
            console.error('Failed to load folders:', err);
            listEl.innerHTML = '<p>Error loading folders: ' + err.message + '</p>';
        }

        // Return promise that resolves when folder is selected
        return new Promise((resolve) => {
            window._folderPickerResolve = resolve;
        });
    }

    window.closeFolderPicker = function() {
        const modal = document.getElementById('folder-picker-modal');
        if (modal) modal.classList.add('hidden');
        if (window._folderPickerResolve) {
            window._folderPickerResolve(null);
            window._folderPickerResolve = null;
        }
    };

    window.selectFolderForPicker = function(folderId) {
        if (window._folderPickerResolve) {
            window._folderPickerResolve(folderId);
            window._folderPickerResolve = null;
        }
        const modal = document.getElementById('folder-picker-modal');
        if (modal) modal.classList.add('hidden');
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-selection.js] Loaded');
})();
