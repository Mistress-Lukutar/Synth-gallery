/**
 * Sidebar module - Folder tree management
 * Phase 3: Sidebar Module
 */

(function() {
    let folderTree = [];
    let collapsedFolders = new Set();
    let userSafes = [];

    // folderTreeContainer will be looked up dynamically

    // Mobile sidebar toggle
    window.openSidebar = function() {
        const sidebar = document.getElementById('sidebar');
        const sidebarOverlay = document.getElementById('sidebar-overlay');
        if (sidebar) sidebar.classList.add('open');
        if (sidebarOverlay) {
            sidebarOverlay.classList.remove('hidden');
            sidebarOverlay.classList.add('visible');
        }
    };

    window.closeSidebar = function() {
        const sidebar = document.getElementById('sidebar');
        const sidebarOverlay = document.getElementById('sidebar-overlay');
        if (sidebar) sidebar.classList.remove('open');
        if (sidebarOverlay) {
            sidebarOverlay.classList.remove('visible');
            sidebarOverlay.classList.add('hidden');
        }
    };

    // Update active state in sidebar
    window.updateSidebarActiveState = function(folderId) {
        document.querySelectorAll('.folder-item').forEach(item => {
            item.classList.remove('active');
            if (item.dataset.folderId === folderId) {
                item.classList.add('active');
            }
        });
    };

    // Load folder tree
    window.loadFolderTree = async function() {
        const folderTreeContainer = document.getElementById('folder-tree');
        console.log('[sidebar.js] loadFolderTree called, container:', folderTreeContainer);
        if (!folderTreeContainer) {
            console.log('[sidebar.js] No folder-tree container found');
            return;
        }

        // Load collapsed state
        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders/user/collapsed`);
            const data = await resp.json();
            collapsedFolders = new Set(data.collapsed_folders || []);
        } catch (err) {
            console.log('[sidebar.js] Could not load collapsed state');
        }

        // Use cache for immediate display
        const cached = sessionStorage.getItem('folderTreeCache');
        let hasCache = false;
        if (cached) {
            try {
                folderTree = JSON.parse(cached);
                hasCache = true;
                renderFolderTree();
            } catch (e) {}
        }

        try {
            // Load folders and safes in parallel
            const [foldersResp, safesResp] = await Promise.all([
                fetch(`${getBaseUrl()}/api/folders`),
                fetch(`${getBaseUrl()}/api/safes`).catch(() => null)
            ]);
            
            const freshData = await foldersResp.json();
            sessionStorage.setItem('folderTreeCache', JSON.stringify(freshData));

            // Parse safes response
            if (safesResp && safesResp.ok) {
                const safesData = await safesResp.json();
                userSafes = safesData.safes || [];
            } else {
                userSafes = [];
            }

            const dataChanged = JSON.stringify(freshData) !== JSON.stringify(folderTree);
            folderTree = freshData;
            
            // Render if: no cache, or data changed, or no safes rendered yet
            if (!hasCache || dataChanged || userSafes.length > 0) {
                renderFolderTree();
            }
        } catch (err) {
            console.error('Failed to load folders:', err);
        }
    };

    // Toggle folder collapse
    window.toggleFolderCollapse = async function(folderId, event) {
        if (event) event.stopPropagation();
        
        const wasCollapsed = collapsedFolders.has(folderId);
        if (wasCollapsed) {
            collapsedFolders.delete(folderId);
        } else {
            collapsedFolders.add(folderId);
        }
        renderFolderTree();
        
        try {
            await csrfFetch(`${getBaseUrl()}/api/folders/${folderId}/toggle-collapse`, {
                method: 'POST'
            });
        } catch (err) {
            console.error('Failed to toggle collapse:', err);
        }
    };

    // Render folder tree
    function renderFolderTree() {
        const folderTreeContainer = document.getElementById('folder-tree');
        if (!folderTreeContainer) {
            console.log('[sidebar.js] No folder-tree container found');
            return;
        }

        // Filter folders: exclude those in safes (safe_id != null)
        const myFolders = folderTree.filter(f => f.permission === 'owner' && !f.safe_id);
        
        let html = '';
        
        // My Folders section
        html += '<div class="folder-section">';
        html += '<div class="folder-section-header">My Folders</div>';
        
        if (myFolders.length > 0) {
            html += buildTreeHTML(null, 0, myFolders);
        }
        
        // Add folder button
        html += `
            <div class="folder-item-wrapper">
                <span class="folder-expand-placeholder"></span>
                <div class="folder-item add-folder-item" onclick="openCreateFolder()">
                    <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        <line x1="12" y1="11" x2="12" y2="17"/>
                        <line x1="9" y1="14" x2="15" y2="14"/>
                    </svg>
                    <span class="folder-name">add folder</span>
                </div>
            </div>
        `;
        html += '</div>';
        
        // Safes section (always show, even if empty)
        html += '<div class="folder-section">';
        html += '<div class="folder-section-header">Safes</div>';
        
        if (userSafes.length > 0) {
            userSafes.forEach(safe => {
                // Check if really unlocked - server says unlocked AND client has the key
                const serverUnlocked = safe.is_unlocked;
                const clientHasKey = typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && SafeCrypto.isUnlocked(safe.id);
                const isUnlocked = serverUnlocked && clientHasKey;
                
                html += `
                    <div class="folder-item-wrapper">
                        <span class="folder-expand-placeholder"></span>
                        <div class="folder-item safe-item ${isUnlocked ? 'unlocked' : 'locked'}"
                             data-safe-id="${safe.id}"
                             onclick="${isUnlocked ? `openSafeEditModal('${safe.id}')` : `openSafeUnlock('${safe.id}', '${escapeHtml(safe.name)}', '${safe.unlock_type}', '${escapeHtml(safe.credential_name || '')}')`}">
                            <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <rect x="5" y="11" width="14" height="10" rx="2"/>
                                <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                            </svg>
                            <span class="folder-name">${escapeHtml(safe.name)}</span>
                            <span class="folder-count">${safe.photo_count || 0}</span>
                        </div>
                    </div>
                `;
                
                // Show folders inside safe when unlocked
                if (isUnlocked) {

                    const safeFolders = folderTree.filter(f => f.safe_id === safe.id && !f.parent_id);
                    if (safeFolders.length > 0) {
                        html += buildTreeHTML(null, 1, safeFolders);
                    }
                    // Add "Create Folder" button inside unlocked safe
                    html += `
                        <div class="folder-item-wrapper" style="padding-left: 20px;">
                            <span class="folder-expand-placeholder"></span>
                            <div class="folder-item add-folder-item" onclick="openCreateFolder(null, '${safe.id}')">
                                <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                                    <line x1="12" y1="11" x2="12" y2="17"/>
                                    <line x1="9" y1="14" x2="15" y2="14"/>
                                </svg>
                                <span class="folder-name">add folder</span>
                            </div>
                        </div>
                    `;
                }
            });
        }
        
        // Create safe button (always show)
        html += `
            <div class="folder-item-wrapper">
                <span class="folder-expand-placeholder"></span>
                <div class="folder-item add-folder-item" onclick="openCreateSafe()">
                    <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="5" y="11" width="14" height="10" rx="2"/>
                        <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                        <line x1="12" y1="14" x2="12" y2="17"/>
                        <line x1="9" y1="15.5" x2="15" y2="15.5"/>
                    </svg>
                    <span class="folder-name">create safe</span>
                </div>
            </div>
        `;
        html += '</div>';
        
        // Shared with me section
        const sharedFolders = folderTree.filter(f => f.permission !== 'owner');
        if (sharedFolders.length > 0) {
            html += '<div class="folder-section">';
            html += '<div class="folder-section-header">Shared with me</div>';
            html += buildTreeHTML(null, 0, sharedFolders);
            html += '</div>';
        }

        folderTreeContainer.innerHTML = html;
    }

    function buildTreeHTML(parentId, level, folders) {
        if (parentId && collapsedFolders.has(parentId)) return '';

        const children = folders.filter(f => f.parent_id === parentId);
        if (children.length === 0) return '';

        return children.map(folder => {
            const isActive = folder.id === window.currentFolderId;
            const hasChildren = folders.some(f => f.parent_id === folder.id);
            const isCollapsed = collapsedFolders.has(folder.id);
            const photoCount = folder.photo_count || 0;
            
            // Determine folder class based on permission/share status
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
                        onclick="toggleFolderCollapse('${folder.id}', event)">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="6 9 12 15 18 9"/>
                    </svg>
                </button>
            ` : '<span class="folder-expand-placeholder"></span>';
            
            return `
                <div class="folder-item-wrapper" style="padding-left: ${level * 16}px">
                    ${expandArrow}
                    <div class="folder-item ${folderClass} ${isActive ? 'active' : ''}"
                         data-folder-id="${folder.id}"
                         onclick="navigateToFolder('${folder.id}', true, event)">
                        <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        </svg>
                        <span class="folder-name">${escapeHtml(folder.name)}</span>
                        <span class="folder-count">${photoCount}</span>
                    </div>
                </div>
            ` + buildTreeHTML(folder.id, level + 1, folders);
        }).join('');
    }

    // Init sidebar toggle - get elements dynamically to handle DOM ready timing
    document.addEventListener('DOMContentLoaded', () => {
        const sidebarToggle = document.getElementById('sidebar-toggle');
        const sidebarOverlay = document.getElementById('sidebar-overlay');
        const sidebar = document.getElementById('sidebar');
        
        if (sidebarToggle) {
            sidebarToggle.onclick = () => {
                if (sidebar && sidebar.classList.contains('open')) {
                    closeSidebar();
                } else {
                    openSidebar();
                }
            };
        }

        // Close on overlay click
        if (sidebarOverlay) {
            sidebarOverlay.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                closeSidebar();
            };
        }
        
        // Setup swipe on overlay to close sidebar
        setupOverlaySwipe();
    });

    // Load folder tree on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        loadFolderTree();
        setupGallerySwipe();
    });
    
    // Setup swipe from left edge to open sidebar
    function setupGallerySwipe() {
        const gallery = document.getElementById('gallery');
        if (!gallery) return;
        
        // Ensure gallery takes full available height for swipe to work everywhere
        gallery.style.minHeight = 'calc(100vh - 120px)';
        
        let touchStartX = 0;
        let touchStartY = 0;
        let isSwiping = false;
        
        gallery.addEventListener('touchstart', (e) => {
            // Only handle swipes from left 75% of screen
            const touchX = e.touches[0].clientX;
            const screenWidth = window.innerWidth;
            
            if (touchX > screenWidth * 0.75) return; // Not in left 75%
            
            touchStartX = touchX;
            touchStartY = e.touches[0].clientY;
            isSwiping = true;
        }, { passive: true });
        
        gallery.addEventListener('touchmove', (e) => {
            if (!isSwiping) return;
        }, { passive: true });
        
        gallery.addEventListener('touchend', (e) => {
            if (!isSwiping) return;
            isSwiping = false;
            
            const touchEndX = e.changedTouches[0].clientX;
            const touchEndY = e.changedTouches[0].clientY;
            
            const diffX = touchEndX - touchStartX; // Positive = right swipe
            const diffY = touchEndY - touchStartY;
            
            const swipeThreshold = 50;
            
            // Only handle horizontal swipes to the right
            if (diffX > swipeThreshold && Math.abs(diffX) > Math.abs(diffY)) {
                openSidebar();
            }
        }, { passive: true });
    }
    
    // Setup swipe left on sidebar overlay to close sidebar
    function setupOverlaySwipe() {
        const sidebarOverlay = document.getElementById('sidebar-overlay');
        if (!sidebarOverlay) return;
        
        let touchStartX = 0;
        let touchStartY = 0;
        let isSwiping = false;
        
        sidebarOverlay.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
            touchStartY = e.touches[0].clientY;
            isSwiping = true;
        }, { passive: true });
        
        sidebarOverlay.addEventListener('touchmove', (e) => {
            if (!isSwiping) return;
        }, { passive: true });
        
        sidebarOverlay.addEventListener('touchend', (e) => {
            if (!isSwiping) return;
            isSwiping = false;
            
            const touchEndX = e.changedTouches[0].clientX;
            const touchEndY = e.changedTouches[0].clientY;
            
            const diffX = touchEndX - touchStartX; // Negative = left swipe
            const diffY = touchEndY - touchStartY;
            
            const swipeThreshold = 50;
            
            // Only handle horizontal swipes to the left (negative diffX)
            if (diffX < -swipeThreshold && Math.abs(diffX) > Math.abs(diffY)) {
                closeSidebar();
            }
        }, { passive: true });
    }

    // Debug: force render tree (for console testing)
    window.debugRenderTree = function() {
        console.log('[sidebar.js] Manual tree render triggered');
        folderTree = []; // Reset to force fresh load
        loadFolderTree();
    };
    
    // For manual testing via console
    window.renderFolderTree = renderFolderTree;

    // Getter for userSafes (returns current value, not snapshot)
    Object.defineProperty(window, 'userSafes', {
        get: function() { return userSafes; }
    });

    // Export - use getter for folderTree to always return current value
    Object.defineProperty(window, 'folderTree', {
        get: function() { return folderTree; }
    });
    // Export collapsed folders for picker sync
    Object.defineProperty(window, 'collapsedFolders', {
        get: function() { return collapsedFolders; }
    });
    window.loadFolderTree = loadFolderTree;
    window.toggleFolderCollapse = toggleFolderCollapse;

    console.log('[sidebar.js] Loaded');
})();
