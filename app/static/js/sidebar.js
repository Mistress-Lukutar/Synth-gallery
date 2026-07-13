/**
 * Sidebar module - Folder tree management
 * Phase 3: Sidebar Module
 */

(function() {
    let folderTree = [];
    let collapsedFolders = new Set();

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
        if (!folderTreeContainer) {
            return;
        }

        // Load collapsed state
        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders/user/collapsed`);
            const data = await resp.json();
            collapsedFolders = new Set(data.collapsed_folders || []);
        } catch (err) {
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
            const foldersResp = await fetch(`${getBaseUrl()}/api/folders`);
            const freshData = await foldersResp.json();
            sessionStorage.setItem('folderTreeCache', JSON.stringify(freshData));

            const dataChanged = JSON.stringify(freshData) !== JSON.stringify(folderTree);
            folderTree = freshData;
            
            if (!hasCache || dataChanged) {
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
            return;
        }

        const myFolders = folderTree.filter(f => f.permission === 'owner');
        
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

    // Use shared folder tree utilities (Issue #26)
    function buildTreeHTML(parentId, level, folders) {
        return FolderTreeUtils.buildTreeHTML(parentId, level, folders, {
            mode: 'sidebar',
            currentFolderId: window.currentFolderId,
            collapsed: collapsedFolders
        });
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
        folderTree = []; // Reset to force fresh load
        loadFolderTree();
    };
    
    // For manual testing via console
    window.renderFolderTree = renderFolderTree;

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

})();
