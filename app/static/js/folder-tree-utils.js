/**
 * Folder tree utilities - shared module for rendering folder trees
 * 
 * Used by:
 * - sidebar.js (sidebar folder tree)
 * - gallery-selection.js (folder picker)
 * 
 * Issue #26: Deduplicates folder tree rendering logic
 */

// Make available globally
window.FolderTreeUtils = {
    /**
     * Get CSS class for folder based on permission and share status
     * @param {Object} folder - folder object with permission and share_status
     * @returns {string} CSS class name
     */
    getFolderClass(folder) {
        if (folder.permission === 'owner') {
            if (folder.share_status === 'has_editors') {
                return 'shared-editors';
            } else if (folder.share_status === 'has_viewers') {
                return 'shared-viewers';
            } else {
                return 'private';
            }
        } else if (folder.permission === 'editor') {
            return 'incoming-editor';
        } else {
            return 'incoming-viewer';
        }
    },

    /**
     * Render folder item HTML
     * @param {Object} folder - folder data
     * @param {Object} options - rendering options
     * @param {string} options.mode - 'sidebar' or 'picker'
     * @param {number} options.level - indentation level
     * @param {boolean} options.hasChildren - whether folder has children
     * @param {boolean} options.isCollapsed - whether folder is collapsed
     * @param {boolean} options.isActive - whether folder is active (sidebar mode)
     * @param {string} options.excludeFolderId - folder to exclude (picker mode)
     * @returns {string} HTML string
     */
    renderFolderItem(folder, options = {}) {
        const {
            mode = 'sidebar',
            level = 0,
            hasChildren = false,
            isCollapsed = false,
            isActive = false,
            excludeFolderId = null
        } = options;

        const folderClass = this.getFolderClass(folder);
        const photoCount = folder.photo_count || 0;
        const paddingLeft = level * 16;

        // Expand/collapse button
        const expandArrow = hasChildren ? `
            <button class="folder-expand-btn ${isCollapsed ? 'collapsed' : ''}"
                    onclick="${mode === 'sidebar' 
                        ? `toggleFolderCollapse('${folder.id}', event)` 
                        : `togglePickerFolderCollapse('${folder.id}', event)`}">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="6 9 12 15 18 9"/>
                </svg>
            </button>
        ` : '<span class="folder-expand-placeholder"></span>';

        // Click handler based on mode
        const clickHandler = mode === 'sidebar'
            ? `navigateToFolder('${folder.id}', true, event)`
            : `selectFolderForPicker('${folder.id}')`;

        // Extra class for picker items
        const extraClass = mode === 'picker' ? 'picker-folder-item' : '';
        const activeClass = isActive ? 'active' : '';

        return `
            <div class="folder-item-wrapper ${extraClass}" style="padding-left: ${paddingLeft}px">
                ${expandArrow}
                <div class="folder-item ${folderClass} ${activeClass}"
                     data-folder-id="${folder.id}"
                     onclick="${clickHandler}">
                    <svg class="folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                    </svg>
                    <span class="folder-name">${escapeHtml(folder.name)}</span>
                    <span class="folder-count">${photoCount}</span>
                </div>
            </div>
        `;
    },

    /**
     * Filter folders for picker (owner only, no safes, exclude specific folder)
     * @param {Array} folders - all folders
     * @param {string} excludeFolderId - folder to exclude
     * @returns {Array} filtered folders
     */
    filterFoldersForPicker(folders, excludeFolderId = null) {
        return folders.filter(f => 
            f.permission === 'owner' && 
            !f.safe_id &&
            f.id !== excludeFolderId
        );
    },

    /**
     * Build folder tree HTML recursively
     * @param {string|null} parentId - parent folder ID
     * @param {number} level - indentation level
     * @param {Array} folders - all folders
     * @param {Object} options - rendering options
     * @param {string} options.mode - 'sidebar' or 'picker'
     * @param {string} options.currentFolderId - active folder ID (sidebar mode)
     * @param {string} options.excludeFolderId - folder to exclude (picker mode)
     * @param {Set} options.collapsed - set of collapsed folder IDs
     * @returns {string} HTML string
     */
    buildTreeHTML(parentId, level, folders, options = {}) {
        const {
            mode = 'sidebar',
            currentFolderId = null,
            excludeFolderId = null,
            collapsed = new Set()
        } = options;

        // Skip collapsed branches (only for sidebar, picker always shows)
        if (mode === 'sidebar' && parentId && collapsed.has(parentId)) {
            return '';
        }

        // Get children
        let children;
        if (mode === 'picker') {
            // Picker has special filtering
            children = folders.filter(f => 
                f.parent_id === parentId && 
                f.permission === 'owner' && 
                !f.safe_id &&
                f.id !== excludeFolderId
            );
        } else {
            children = folders.filter(f => f.parent_id === parentId);
        }

        if (children.length === 0) return '';

        return children.map(folder => {
            const hasChildren = folders.some(f => f.parent_id === folder.id);
            const isCollapsed = collapsed.has(folder.id);
            const isActive = mode === 'sidebar' && folder.id === currentFolderId;

            // Render this folder item
            const itemHtml = this.renderFolderItem(folder, {
                mode,
                level,
                hasChildren,
                isCollapsed,
                isActive,
                excludeFolderId
            });

            // Recursively render children (if not collapsed)
            let childrenHtml = '';
            if (hasChildren && !isCollapsed) {
                childrenHtml = this.buildTreeHTML(folder.id, level + 1, folders, options);
            }

            return itemHtml + childrenHtml;
        }).join('');
    },

    /**
     * Render a folder section (for sidebar)
     * @param {string} title - section title
     * @param {Array} folders - folders in this section
     * @param {Object} options - rendering options
     * @returns {string} HTML string
     */
    renderSection(title, folders, options = {}) {
        const { currentFolderId = null, collapsed = new Set() } = options;

        const treeHtml = this.buildTreeHTML(null, 0, folders, {
            mode: 'sidebar',
            currentFolderId,
            collapsed
        });

        if (!treeHtml) return '';

        return `
            <div class="folder-section">
                ${title ? `<div class="folder-section-header">${escapeHtml(title)}</div>` : ''}
                ${treeHtml}
            </div>
        `;
    }
};

