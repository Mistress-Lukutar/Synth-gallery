/**
 * Folder Actions module
 * Folder management: create, edit, share, delete
 */

(function() {
    // === Folder Modal State ===
    let editingFolderId = null;
    let userDefaultFolderId = null;

    // === Share Modal State ===
    let shareModalFolderId = null;
    let selectedUserId = null;
    let searchTimeout = null;

    // === DOM Element References ===
    let folderModal = null;
    let folderModalTitle = null;
    let folderNameInput = null;
    let folderSubmitBtn = null;
    let folderCancelBtn = null;
    let folderModalClose = null;
    let defaultFolderSection = null;
    let setDefaultFolderBtn = null;
    let deleteFolderSection = null;
    let deleteFolderBtn = null;
    let folderMoveSection = null;
    let folderCurrentLocation = null;
    let folderMoveBtn = null;

    function init() {
        // Folder modal elements
        folderModal = document.getElementById('folder-modal');
        folderModalTitle = document.getElementById('folder-modal-title');
        folderNameInput = document.getElementById('folder-name-input');
        folderSubmitBtn = document.getElementById('folder-submit-btn');
        folderCancelBtn = document.getElementById('folder-cancel-btn');
        folderModalClose = folderModal?.querySelector('.close');
        defaultFolderSection = document.getElementById('default-folder-section');
        setDefaultFolderBtn = document.getElementById('set-default-folder-btn');
        deleteFolderSection = document.getElementById('delete-folder-section');
        deleteFolderBtn = document.getElementById('delete-folder-btn');
        folderMoveSection = document.getElementById('folder-move-section');
        folderCurrentLocation = document.getElementById('folder-current-location');
        folderMoveBtn = document.getElementById('folder-move-btn');

        setupFolderModalHandlers();
        setupShareModalHandlers();
    }

    function setupFolderModalHandlers() {
        if (!folderModal) return;

        // Cancel and close buttons
        if (folderCancelBtn) {
            folderCancelBtn.addEventListener('click', closeFolderModal);
        }
        if (folderModalClose) {
            folderModalClose.addEventListener('click', closeFolderModal);
        }

        // Click outside to close
        folderModal.addEventListener('click', (e) => {
            if (e.target === folderModal) closeFolderModal();
        });

        // Submit button
        if (folderSubmitBtn) {
            folderSubmitBtn.addEventListener('click', submitFolderForm);
        }

        // Set as default folder
        if (setDefaultFolderBtn) {
            setDefaultFolderBtn.addEventListener('click', setAsDefaultFolder);
        }

        // Delete folder
        if (deleteFolderBtn) {
            deleteFolderBtn.addEventListener('click', () => {
                if (editingFolderId) {
                    deleteFolder(editingFolderId);
                }
            });
        }

        // Move folder button
        if (folderMoveBtn) {
            folderMoveBtn.addEventListener('click', openFolderMovePicker);
        }
    }

    function setupShareModalHandlers() {
        // Share modal - user search
        const userSearchInput = document.getElementById('user-search-input');
        if (userSearchInput) {
            userSearchInput.addEventListener('input', function(e) {
                const query = e.target.value.trim();
                clearTimeout(searchTimeout);
                if (query.length < 2) {
                    const results = document.getElementById('user-search-results');
                    if (results) results.classList.add('hidden');
                    return;
                }
                searchTimeout = setTimeout(() => searchUsers(query), 300);
            });

            userSearchInput.addEventListener('blur', function() {
                setTimeout(() => {
                    const results = document.getElementById('user-search-results');
                    if (results) results.classList.add('hidden');
                }, 200);
            });

            userSearchInput.addEventListener('focus', function(e) {
                const query = e.target.value.trim();
                if (query.length >= 2) {
                    searchUsers(query);
                }
            });
        }

        // Cancel add permission
        const cancelAddBtn = document.getElementById('cancel-add-btn');
        if (cancelAddBtn) {
            cancelAddBtn.addEventListener('click', function() {
                const form = document.getElementById('add-permission-form');
                if (form) form.classList.add('hidden');
                selectedUserId = null;
            });
        }

        // Add permission button
        const addPermissionBtn = document.getElementById('add-permission-btn');
        if (addPermissionBtn) {
            addPermissionBtn.addEventListener('click', addPermission);
        }
    }

    // === Folder Modal Functions ===

    window.openCreateFolder = function(parentId = null) {
        editingFolderId = null;
        if (folderModalTitle) folderModalTitle.textContent = 'Create Folder';
        if (folderSubmitBtn) folderSubmitBtn.textContent = 'Create';
        if (folderNameInput) folderNameInput.value = '';
        
        // Hide move section for create
        if (folderMoveSection) folderMoveSection.classList.add('hidden');
        if (folderMoveBtn) folderMoveBtn.style.display = 'none';

        if (defaultFolderSection) defaultFolderSection.classList.add('hidden');
        if (deleteFolderSection) deleteFolderSection.classList.add('hidden');
        if (folderModal) {
            folderModal.classList.remove('hidden');
            folderNameInput?.focus();
        }
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('folder-modal', closeFolderModal);
        }
    };

    window.openEditFolder = function(folderId) {
        const folderTree = window.folderTree || [];
        console.log('[folder-actions] openEditFolder:', folderId, 'folderTree:', folderTree.length, 'items');
        const folder = folderTree.find(f => f.id === folderId);
        if (!folder) {
            console.log('[folder-actions] Folder not found:', folderId);
            return;
        }

        editingFolderId = folderId;
        if (folderModalTitle) folderModalTitle.textContent = 'Edit Folder';
        if (folderSubmitBtn) folderSubmitBtn.textContent = 'Save';
        if (folderNameInput) folderNameInput.value = folder.name;
        
        // Show move section with current location
        if (folderMoveSection) folderMoveSection.classList.remove('hidden');
        if (folderMoveBtn) folderMoveBtn.style.display = 'block';
        updateFolderLocation(folder.parent_id);

        // Show default folder section
        if (defaultFolderSection) {
            defaultFolderSection.classList.remove('hidden');
            const isCurrentDefault = userDefaultFolderId === folderId;
            if (setDefaultFolderBtn) {
                if (isCurrentDefault) {
                    setDefaultFolderBtn.textContent = 'This is your default folder';
                    setDefaultFolderBtn.disabled = true;
                } else {
                    setDefaultFolderBtn.textContent = 'Set as default folder';
                    setDefaultFolderBtn.disabled = false;
                }
            }
        }

        // Show delete folder section
        if (deleteFolderSection) deleteFolderSection.classList.remove('hidden');

        if (folderModal) {
            folderModal.classList.remove('hidden');
            folderNameInput?.focus();
        }
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('folder-modal', closeFolderModal);
        }
    };

    function closeFolderModal() {
        // Unregister from BackButtonManager first (skipHistoryBack = true for button clicks)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('folder-modal', true);
        }
        
        if (folderModal) folderModal.classList.add('hidden');
        editingFolderId = null;
    }

    async function submitFolderForm() {
        const name = folderNameInput?.value.trim();
        if (!name) {
            alert('Please enter a folder name');
            return;
        }

        try {
            if (editingFolderId) {
                // Update existing folder (name only)
                await csrfFetch(`${getBaseUrl()}/api/folders/${editingFolderId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
            } else {
                // Create new folder
                await csrfFetch(`${getBaseUrl()}/api/folders`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
            }
            closeFolderModal();
            if (typeof loadFolderTree === 'function') loadFolderTree();
        } catch (err) {
            console.error('Folder operation failed:', err);
            alert('Operation failed');
        }
    }

    async function setAsDefaultFolder() {
        if (!editingFolderId) return;

        if (setDefaultFolderBtn) {
            setDefaultFolderBtn.disabled = true;
            setDefaultFolderBtn.textContent = 'Setting...';
        }

        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${editingFolderId}/set-default`, {
                method: 'PUT'
            });
            userDefaultFolderId = editingFolderId;
            if (setDefaultFolderBtn) {
                setDefaultFolderBtn.textContent = 'This is your default folder';
            }
        } catch (err) {
            console.error('Failed to set default folder:', err);
            if (setDefaultFolderBtn) {
                setDefaultFolderBtn.textContent = 'Set as default folder';
                setDefaultFolderBtn.disabled = false;
            }
            alert('Failed to set default folder');
        }
    }

    async function deleteFolder(folderId) {
        const folderTree = window.folderTree || [];
        const folder = folderTree.find(f => f.id === folderId);
        if (!folder) return;

        if (!confirm(`Delete folder "${folder.name}" and all its contents?`)) return;

        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${folderId}`, {
                method: 'DELETE'
            });
            closeFolderModal();
            if (typeof loadFolderTree === 'function') loadFolderTree();
            if (typeof navigateToDefaultFolder === 'function') navigateToDefaultFolder();
        } catch (err) {
            console.error('Failed to delete folder:', err);
            alert('Failed to delete folder');
        }
    }

    function updateFolderLocation(parentId) {
        if (!folderCurrentLocation) return;
        
        if (!parentId) {
            folderCurrentLocation.textContent = 'Root level';
            return;
        }
        
        const folderTree = window.folderTree || [];
        const parent = folderTree.find(f => f.id === parentId);
        folderCurrentLocation.textContent = parent ? parent.name : 'Unknown folder';
    }

    async function openFolderMovePicker() {
        if (!editingFolderId) return;
        
        // Use existing folder picker from gallery-selection.js if available
        if (typeof window.openFolderPicker === 'function') {
            // Pass editingFolderId to exclude it from picker (can't move into itself)
            const result = await window.openFolderPicker('Select destination folder', editingFolderId);
            if (result && result.folder_id !== undefined) {
                // folder_id can be empty string for root level
                await moveFolderTo(result.folder_id);
            }
        } else {
            // Fallback: simple prompt (should not happen)
            alert('Folder picker not available');
        }
    }

    async function moveFolderTo(targetFolderId) {
        if (!editingFolderId) return;
        
        // targetFolderId can be empty string for root level
        const isRootMove = targetFolderId === '';
        
        // Prevent moving to itself
        if (editingFolderId === targetFolderId) {
            alert('Cannot move folder into itself');
            return;
        }
        
        // Save folder ID before closing modal (closeFolderModal clears editingFolderId)
        const movedFolderId = editingFolderId;
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${movedFolderId}/move`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ parent_id: isRootMove ? null : targetFolderId })
            });
            
            // Update UI
            updateFolderLocation(isRootMove ? null : targetFolderId);
            
            // Refresh folder tree
            if (typeof loadFolderTree === 'function') loadFolderTree();
            
            // Close modal
            closeFolderModal();
            
            // Navigate to the moved folder to show it in new location
            if (typeof navigateToFolder === 'function') {
                navigateToFolder(movedFolderId, false);
            }
        } catch (err) {
            console.error('Failed to move folder:', err);
            alert('Failed to move folder: ' + (err.message || 'Unknown error'));
        }
    }

    // === Share Modal Functions ===

    window.openShareModal = function(folderId) {
        shareModalFolderId = folderId;
        const shareModal = document.getElementById('share-modal');
        const userSearchInput = document.getElementById('user-search-input');
        const userSearchResults = document.getElementById('user-search-results');
        const addPermissionForm = document.getElementById('add-permission-form');

        if (shareModal) shareModal.classList.remove('hidden');
        if (userSearchInput) userSearchInput.value = '';
        if (userSearchResults) userSearchResults.classList.add('hidden');
        if (addPermissionForm) addPermissionForm.classList.add('hidden');

        loadPermissions();
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('share-modal', closeShareModal);
        }
    };

    window.closeShareModal = function() {
        // Unregister from BackButtonManager first (skipHistoryBack = true for button clicks)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('share-modal', true);
        }
        
        const shareModal = document.getElementById('share-modal');
        if (shareModal) shareModal.classList.add('hidden');
        shareModalFolderId = null;
        selectedUserId = null;
    };

    async function loadPermissions() {
        if (!shareModalFolderId) return;

        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders/${shareModalFolderId}/permissions`);
            const data = await resp.json();
            renderPermissions(data.permissions || []);
        } catch (err) {
            console.error('Failed to load permissions:', err);
        }
    }

    function renderPermissions(permissions) {
        const container = document.getElementById('permissions-list');
        if (!container) return;

        if (permissions.length === 0) {
            container.innerHTML = '<p class="no-permissions">No one else has access</p>';
            return;
        }

        container.innerHTML = permissions.map(p => `
            <div class="permission-item" data-user-id="${p.user_id}">
                <span class="permission-user">${escapeHtml(p.display_name || p.username)}</span>
                <select class="permission-select" onchange="updatePermission(${p.user_id}, this.value)">
                    <option value="viewer" ${p.permission === 'viewer' ? 'selected' : ''}>Viewer</option>
                    <option value="editor" ${p.permission === 'editor' ? 'selected' : ''}>Editor</option>
                </select>
                <button class="btn-remove-permission" onclick="removePermission(${p.user_id})" title="Remove">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                    </svg>
                </button>
            </div>
        `).join('');
    }

    async function searchUsers(query) {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/users/search?q=${encodeURIComponent(query)}`);
            const data = await resp.json();
            renderSearchResults(data.users || []);
        } catch (err) {
            console.error('Search failed:', err);
        }
    }

    function renderSearchResults(users) {
        const container = document.getElementById('user-search-results');
        if (!container) return;

        if (users.length === 0) {
            container.innerHTML = '<div class="no-results">No users found</div>';
        } else {
            container.innerHTML = users.map(u => `
                <div class="search-result-item" onclick="selectUserForShare(${u.id}, '${escapeHtml(u.display_name || u.username)}')">
                    ${escapeHtml(u.display_name || u.username)}
                    <span class="search-result-username">@${escapeHtml(u.username)}</span>
                </div>
            `).join('');
        }
        container.classList.remove('hidden');
    }

    window.selectUserForShare = function(userId, displayName) {
        selectedUserId = userId;
        const userSearchResults = document.getElementById('user-search-results');
        const userSearchInput = document.getElementById('user-search-input');
        const selectedUserName = document.getElementById('selected-user-name');
        const addPermissionForm = document.getElementById('add-permission-form');

        if (userSearchResults) userSearchResults.classList.add('hidden');
        if (userSearchInput) userSearchInput.value = '';
        if (selectedUserName) selectedUserName.textContent = displayName;
        if (addPermissionForm) addPermissionForm.classList.remove('hidden');
    };

    async function addPermission() {
        if (!selectedUserId || !shareModalFolderId) return;

        const permissionSelect = document.getElementById('permission-level-select');
        const permission = permissionSelect?.value || 'viewer';

        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${shareModalFolderId}/permissions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: selectedUserId, permission })
            });
            const addPermissionForm = document.getElementById('add-permission-form');
            if (addPermissionForm) addPermissionForm.classList.add('hidden');
            selectedUserId = null;
            loadPermissions();
        } catch (err) {
            console.error('Add permission failed:', err);
            alert('Failed to add permission');
        }
    }

    window.updatePermission = async function(userId, permission) {
        if (!shareModalFolderId) return;

        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${shareModalFolderId}/permissions/${userId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ permission })
            });
        } catch (err) {
            console.error('Update permission failed:', err);
            alert('Failed to update permission');
            loadPermissions();
        }
    };

    window.removePermission = async function(userId) {
        if (!shareModalFolderId) return;
        if (!confirm('Remove access for this user?')) return;

        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${shareModalFolderId}/permissions/${userId}`, {
                method: 'DELETE'
            });
            loadPermissions();
        } catch (err) {
            console.error('Remove permission failed:', err);
            alert('Failed to remove permission');
        }
    };

    // Load default folder ID
    async function loadDefaultFolder() {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/user/default-folder`);
            const data = await resp.json();
            userDefaultFolderId = data.folder_id;
        } catch (err) {
            console.error('Failed to load default folder:', err);
        }
    }

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
        loadDefaultFolder();
    });

    console.log('[folder-actions.js] Loaded');
})();
