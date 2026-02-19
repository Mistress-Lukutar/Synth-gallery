/**
 * Sidebar module - Folder tree management
 * Phase 3: Sidebar Module
 */

(function() {
    let folderTree = [];
    let collapsedFolders = new Set();

    // Get sidebar elements (some may be null if DOM not ready)
    const sidebar = document.getElementById('sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const sidebarOverlay = document.getElementById('sidebar-overlay');
    
    // folderTreeContainer will be looked up dynamically

    // Mobile sidebar toggle
    window.openSidebar = function() {
        if (sidebar) sidebar.classList.add('open');
        if (sidebarOverlay) {
            sidebarOverlay.classList.remove('hidden');
            sidebarOverlay.classList.add('visible');
        }
    };

    window.closeSidebar = function() {
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

        // Use cache
        const cached = sessionStorage.getItem('folderTreeCache');
        if (cached) {
            try {
                folderTree = JSON.parse(cached);
                renderFolderTree();
            } catch (e) {}
        }

        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders`);
            const freshData = await resp.json();
            sessionStorage.setItem('folderTreeCache', JSON.stringify(freshData));

            if (JSON.stringify(freshData) !== JSON.stringify(folderTree)) {
                folderTree = freshData;
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

        const myFolders = folderTree.filter(f => f.permission === 'owner');
        
        let html = '<div class="folder-section">';
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
                    <div class="folder-item ${isActive ? 'active' : ''}"
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

    // Open create folder modal
    window.openCreateFolder = function() {
        const modal = document.getElementById('folder-modal');
        const nameInput = document.getElementById('folder-name-input');
        
        if (nameInput) nameInput.value = '';
        if (modal) modal.classList.remove('hidden');
    };

    // Init sidebar toggle
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

    // Load folder tree on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        loadFolderTree();
    });

    // Debug: force render tree (for console testing)
    window.debugRenderTree = function() {
        console.log('[sidebar.js] Manual tree render triggered');
        folderTree = []; // Reset to force fresh load
        loadFolderTree();
    };
    
    // For manual testing via console
    window.renderFolderTree = renderFolderTree;

    // Export
    window.folderTree = folderTree;
    window.loadFolderTree = loadFolderTree;
    window.toggleFolderCollapse = toggleFolderCollapse;

    console.log('[sidebar.js] Loaded');
})();
