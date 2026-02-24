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

    // Check if any selected item is from a safe (encrypted content)
    function checkSafeContent(photos, albums) {
        // Check photos
        for (const photoId of photos) {
            const item = gallery.querySelector(`[data-photo-id="${photoId}"]`);
            if (item && item.dataset.safeId) {
                return true;
            }
        }
        // Check albums
        for (const albumId of albums) {
            const item = gallery.querySelector(`[data-album-id="${albumId}"]`);
            if (item && item.dataset.safeId) {
                return true;
            }
        }
        return false;
    }

    // Check if any selected item is from a LOCKED safe (no client-side key)
    // This is the critical check - E2E safes lock on client, not server!
    function checkLockedSafeContent(photos, albums) {
        const lockedSafeIds = new Set();
        
        // Check photos
        for (const photoId of photos) {
            const item = gallery.querySelector(`[data-photo-id="${photoId}"]`);
            if (item && item.dataset.safeId) {
                const safeId = item.dataset.safeId;
                // Check if we have the key in memory (SafeCrypto.isUnlocked)
                if (typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && !SafeCrypto.isUnlocked(safeId)) {
                    lockedSafeIds.add(safeId);
                }
            }
        }
        // Check albums
        for (const albumId of albums) {
            const item = gallery.querySelector(`[data-album-id="${albumId}"]`);
            if (item && item.dataset.safeId) {
                const safeId = item.dataset.safeId;
                // Check if we have the key in memory
                if (typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && !SafeCrypto.isUnlocked(safeId)) {
                    lockedSafeIds.add(safeId);
                }
            }
        }
        return lockedSafeIds;
    }

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
                const originalHTML = downloadBtn.innerHTML;
                // Show spinner while preparing
                downloadBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18" class="spinner"><circle cx="12" cy="12" r="10" stroke-dasharray="60" stroke-dashoffset="20"/></svg>';

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
                    downloadBtn.innerHTML = originalHTML;
                }
            });
        }

        // Move selected
        const moveBtn = document.getElementById('move-selected-btn');
        if (moveBtn) {
            moveBtn.addEventListener('click', async () => {
                const total = selectedPhotos.size + selectedAlbums.size;
                if (total === 0) return;
                
                // Check for safe content - block move for encrypted items
                const hasSafeContent = checkSafeContent(selectedPhotos, selectedAlbums);
                if (hasSafeContent) {
                    alert('Cannot move encrypted content. Moving files from safes is not supported in this version.');
                    return;
                }
                
                const destination = await showFolderPicker('Move to');
                if (!destination) return;

                try {
                    const payload = {
                        photo_ids: Array.from(selectedPhotos),
                        album_ids: Array.from(selectedAlbums),
                        folder_id: destination.folder_id
                    };
                    console.log('[Move] Sending:', payload);
                    
                    const resp = await csrfFetch(`${getBaseUrl()}/api/items/move`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });

                    if (!resp.ok) {
                        const errText = await resp.text();
                        throw new Error('Move failed: ' + resp.status + ' - ' + errText);
                    }
                    
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
                
                // Check for safe content - block copy for encrypted items
                const hasSafeContent = checkSafeContent(selectedPhotos, selectedAlbums);
                if (hasSafeContent) {
                    alert('Cannot copy encrypted content. Copying files from safes is not supported in this version.');
                    return;
                }
                
                const destination = await showFolderPicker('Copy to');
                if (!destination) return;

                try {
                    const payload = {
                        photo_ids: Array.from(selectedPhotos),
                        album_ids: Array.from(selectedAlbums),
                        folder_id: destination.folder_id
                    };
                    console.log('[Copy] Sending:', payload);
                    
                    const resp = await csrfFetch(`${getBaseUrl()}/api/items/copy`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });

                    if (!resp.ok) throw new Error('Copy failed: ' + await resp.text());
                    
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
                
                // CRITICAL: Check for locked safes (E2E - client-side only!)
                const lockedSafeIds = checkLockedSafeContent(selectedPhotos, selectedAlbums);
                if (lockedSafeIds.size > 0) {
                    alert(`Cannot delete: selected items are in locked safe(s). Please unlock the safe(s) first.`);
                    return;
                }
                
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

    // Clear all selections and hide selection menu
    window.clearSelection = function() {
        selectedPhotos.clear();
        selectedAlbums.clear();
        document.querySelectorAll('.gallery-item.selected').forEach(item => {
            item.classList.remove('selected');
        });
        const selectionMenu = document.getElementById('selection-menu');
        if (selectionMenu) {
            selectionMenu.classList.add('hidden');
        }
    };

    // Toggle collapse in picker (syncs with sidebar collapsed state)
    window.togglePickerFolderCollapse = function(folderId, event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        
        // Use same collapsed state as sidebar
        const collapsed = window.collapsedFolders;
        if (collapsed.has(folderId)) {
            collapsed.delete(folderId);
        } else {
            collapsed.add(folderId);
        }
        
        // Also update sidebar if visible
        if (typeof window.loadFolderTree === 'function') {
            window.loadFolderTree();
        }
        
        // Re-render picker
        const listEl = document.getElementById('folder-picker-list');
        if (listEl && window._pickerFolders) {
            const html = '<div class="folder-section">' + 
                        buildPickerTreeHTML(null, 0, window._pickerFolders) + '</div>';
            listEl.innerHTML = html;
        }
    };

    // Build folder tree HTML for picker (similar to sidebar)
    // excludeFolderId - folder to exclude (for move folder: prevent moving into itself or its descendants)
    function buildPickerTreeHTML(parentId, level, folders, excludeFolderId = null) {
        // Filter: owner only, no safe folders (safe_id is null/undefined), exclude specified folder
        const children = folders.filter(f => 
            f.parent_id === parentId && 
            f.permission === 'owner' && 
            !f.safe_id &&
            f.id !== excludeFolderId
        );
        if (children.length === 0) return '';

        // Use sidebar collapsed state
        const collapsed = window.collapsedFolders || new Set();

        return children.map(folder => {
            const hasChildren = folders.some(f => f.parent_id === folder.id);
            const isCollapsed = collapsed.has(folder.id);
            const photoCount = folder.photo_count || 0;
            
            // Same color logic as sidebar
            let folderClass = '';
            if (folder.permission === 'owner') {
                if (folder.share_status === 'has_editors') {
                    folderClass = 'shared-editors';
                } else if (folder.share_status === 'has_viewers') {
                    folderClass = 'shared-viewers';
                } else {
                    folderClass = 'private';
                }
            } else if (folder.permission === 'editor') {
                folderClass = 'incoming-editor';
            } else {
                folderClass = 'incoming-viewer';
            }
            
            const expandArrow = hasChildren ? `
                <button class="folder-expand-btn ${isCollapsed ? 'collapsed' : ''}"
                        onclick="togglePickerFolderCollapse('${folder.id}', event)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                </button>
            ` : '<span class="folder-expand-placeholder"></span>';
            
            const childrenHtml = hasChildren && !isCollapsed 
                ? buildPickerTreeHTML(folder.id, level + 1, folders, excludeFolderId) 
                : '';
            
            return `
                <div class="folder-item-wrapper picker-folder-item" style="padding-left: ${level * 16}px">
                    ${expandArrow}
                    <div class="folder-item ${folderClass}"
                         data-folder-id="${folder.id}"
                         onclick="selectFolderForPicker('${folder.id}')">
                        <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        </svg>
                        <span class="folder-name">${escapeHtml(folder.name)}</span>
                        <span class="folder-count">${photoCount}</span>
                    </div>
                </div>
            ` + childrenHtml;
        }).join('');
    }

    // Show folder picker modal with both folders and safes
    // excludeFolderId - folder to exclude from picker (for move folder operation)
    async function showFolderPicker(title, operation = 'move', excludeFolderId = null) {
        // Create modal if not exists
        let modal = document.getElementById('folder-picker-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'folder-picker-modal';
            modal.className = 'modal hidden';
            modal.innerHTML = `
                <div class="modal-content folder-picker-content">
                    <span class="close" onclick="closeFolderPicker()">&times;</span>
                    <h3 id="folder-picker-title">Select Destination</h3>
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
        listEl.innerHTML = '<p>Loading...</p>';
        modal.classList.remove('hidden');

        try {
            // Load folders and safes in parallel
            const [foldersResp, safesResp] = await Promise.all([
                fetch(`${getBaseUrl()}/api/folders`),
                fetch(`${getBaseUrl()}/api/safes`)
            ]);
            
            if (!foldersResp.ok || !safesResp.ok) throw new Error('Failed to load destinations');
            
            const folders = await foldersResp.json();
            const safesData = await safesResp.json();
            const safes = safesData.safes || [];
            
            console.log('[folder picker] Folders:', folders?.length, 'Safes:', safes?.length);

            // Store for later use
            window._pickerFolders = folders;
            window._pickerSafes = safes;
            
            // Build HTML - for now only regular folders (safes not supported for move/copy yet)
            let html = '';
            
            // Root level option (for move folder operation)
            html += `
                <div class="folder-item-wrapper picker-folder-item" onclick="selectFolderForPicker('')">
                    <span class="folder-expand-placeholder"></span>
                    <div class="folder-item">
                        <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                            <polyline points="9 22 9 12 15 12 15 22"/>
                        </svg>
                        <span class="folder-name">Root level</span>
                    </div>
                </div>
            `;
            
            // Regular folders section
            html += '<div class="folder-section">';
            html += '<div class="folder-section-header">My Folders</div>';
            const regularFolders = buildPickerTreeHTML(null, 0, folders, excludeFolderId);
            if (regularFolders) {
                html += regularFolders;
            } else {
                html += '<p class="no-folders">No other folders</p>';
            }
            html += '</div>';
            
            // Note: Safes excluded from picker - cross-storage operations need encryption handling
            
            listEl.innerHTML = html;
        } catch (err) {
            console.error('Failed to load destinations:', err);
            listEl.innerHTML = '<p>Error: ' + err.message + '</p>';
        }

        // Return promise that resolves when destination is selected
        return new Promise((resolve) => {
            window._folderPickerResolve = resolve;
        });
    }

    // Build safe picker HTML (only unlocked safes)
    function buildSafePickerHTML(safes) {
        const unlockedSafes = safes.filter(s => {
            const serverUnlocked = s.is_unlocked;
            const clientHasKey = typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && SafeCrypto.isUnlocked(s.id);
            return serverUnlocked && clientHasKey;
        });
        
        if (unlockedSafes.length === 0) return '';
        
        return unlockedSafes.map(safe => `
            <div class="folder-item-wrapper picker-folder-item" onclick="selectSafeForPicker('${safe.id}')">
                <span class="folder-expand-placeholder"></span>
                <div class="folder-item safe-item unlocked">
                    <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="5" y="11" width="14" height="10" rx="2"/>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                    </svg>
                    <span class="folder-name">${escapeHtml(safe.name)}</span>
                    <span class="folder-count">${safe.photo_count || 0}</span>
                </div>
            </div>
        `).join('');
    }

    // Select safe as destination (not used yet - needs server support for cross-storage encryption)
    window.selectSafeForPicker = function(safeId) {
        alert('Moving/Copying to safes is not yet supported. Please use regular folders.');
        if (window._folderPickerResolve) {
            window._folderPickerResolve(null);
            window._folderPickerResolve = null;
        }
        const modal = document.getElementById('folder-picker-modal');
        if (modal) modal.classList.add('hidden');
    };

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
            window._folderPickerResolve({ folder_id: folderId });
            window._folderPickerResolve = null;
        }
        const modal = document.getElementById('folder-picker-modal');
        if (modal) modal.classList.add('hidden');
    };

    // Expose folder picker for use by other modules (e.g., folder-actions.js)
    // excludeFolderId - folder to exclude (useful for move folder: can't move into itself)
    window.openFolderPicker = function(title, excludeFolderId = null) {
        return showFolderPicker(title, 'move', excludeFolderId);
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-selection.js] Loaded');
})();
