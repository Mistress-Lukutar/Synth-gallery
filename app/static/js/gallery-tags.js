/**
 * Gallery Tags v2 - Hierarchical Tag System
 * 
 * Features:
 * - Tree view for browsing tags
 * - Autocomplete with usage count
 * - Negative search support
 * - Automatic ancestor inclusion
 */

(function() {
    // DOM Elements
    let tagEditorPanel = null;
    let tagSearch = null;
    let tagTreeContainer = null;
    let currentTagsContainer = null;
    let categoryTabs = null;
    let lightbox = null;

    // State
    let categories = [];
    let currentCategory = null;
    let currentTreeParent = null;
    let editingItemId = null;
    let currentTags = [];
    let searchResults = [];
    let recentTags = [];

    function init() {
        tagEditorPanel = document.getElementById('tag-editor-panel');
        if (!tagEditorPanel) return;

        lightbox = document.getElementById('lightbox');
        tagSearch = document.getElementById('tag-search');
        tagTreeContainer = document.getElementById('tag-tree-container');
        currentTagsContainer = document.getElementById('current-tags-container');
        categoryTabs = document.getElementById('category-tabs');

        setupEventListeners();
        loadCategories();
        loadRecentTags();
        console.log('[gallery-tags-v2] Initialized');
    }

    function setupEventListeners() {
        // Search with debounce
        let searchTimeout;
        if (tagSearch) {
            tagSearch.addEventListener('input', (e) => {
                clearTimeout(searchTimeout);
                const query = e.target.value.trim();
                
                if (query.length === 0) {
                    showTreeView();
                    return;
                }
                
                searchTimeout = setTimeout(() => searchTags(query), 200);
            });

            tagSearch.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && searchResults.length > 0) {
                    addTag(searchResults[0].id);
                }
                if (e.key === 'Escape') {
                    tagSearch.value = '';
                    showTreeView();
                }
            });
        }

        // Close on backdrop click
        tagEditorPanel?.addEventListener('click', (e) => {
            if (e.target === tagEditorPanel) {
                closeTagEditor();
            }
        });
    }

    // ========================================================================
    // API Calls
    // ========================================================================

    async function loadCategories() {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/tag-categories`);
            if (resp.ok) {
                const data = await resp.json();
                categories = data.categories || [];
                renderCategoryTabs();
            }
        } catch (e) {
            console.error('Failed to load categories:', e);
        }
    }

    async function loadRecentTags() {
        // Load from localStorage or fetch recent from API
        const saved = localStorage.getItem('recentTags');
        if (saved) {
            recentTags = JSON.parse(saved);
        }
    }

    async function searchTags(query) {
        if (!query || query.length < 1) return;
        
        try {
            const resp = await fetch(
                `${getBaseUrl()}/api/tags/search?q=${encodeURIComponent(query)}&limit=10`
            );
            if (resp.ok) {
                const data = await resp.json();
                searchResults = data.tags || [];
                renderSearchResults();
            }
        } catch (e) {
            console.error('Search failed:', e);
        }
    }

    async function loadTagTree(categorySlug = null, parentId = null) {
        try {
            let url = `${getBaseUrl()}/api/tags/tree`;
            if (categorySlug) url += `?category=${categorySlug}`;
            if (parentId) url += `${categorySlug ? '&' : '?'}parent_id=${parentId}`;
            
            const resp = await fetch(url);
            if (resp.ok) {
                const data = await resp.json();
                renderTagTree(data.tags, parentId);
            }
        } catch (e) {
            console.error('Failed to load tree:', e);
        }
    }

    async function addTag(tagId) {
        if (!editingItemId) return;
        
        try {
            const resp = await csrfFetch(
                `${getBaseUrl()}/api/items/${editingItemId}/tags`,
                {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tag_id: tagId })
                }
            );
            
            if (resp.ok) {
                const data = await resp.json();
                currentTags = data.tags?.tags || [];
                renderCurrentTags();
                
                // Add to recent
                const tag = searchResults.find(t => t.id === tagId);
                if (tag) addToRecent(tag);
                
                // Clear search
                if (tagSearch) {
                    tagSearch.value = '';
                    tagSearch.focus();
                    showTreeView();
                }
            }
        } catch (e) {
            console.error('Failed to add tag:', e);
        }
    }

    async function removeTag(tagId) {
        if (!editingItemId) return;
        
        try {
            const resp = await csrfFetch(
                `${getBaseUrl()}/api/items/${editingItemId}/tags/${tagId}`,
                { method: 'DELETE' }
            );
            
            if (resp.ok) {
                const data = await resp.json();
                currentTags = data.tags?.tags || [];
                renderCurrentTags();
            }
        } catch (e) {
            console.error('Failed to remove tag:', e);
        }
    }

    async function saveChanges() {
        closeTagEditor();
        // Refresh gallery to show tag changes
        if (window.refreshGallery) {
            window.refreshGallery();
        }
    }

    // ========================================================================
    // Rendering
    // ========================================================================

    function renderCategoryTabs() {
        if (!categoryTabs) return;
        
        const html = categories.map(cat => `
            <button class="category-tab ${currentCategory === cat.slug ? 'active' : ''}" 
                    data-slug="${cat.slug}"
                    style="--category-color: ${cat.color}">
                ${cat.name}
            </button>
        `).join('');
        
        categoryTabs.innerHTML = html;
        
        categoryTabs.querySelectorAll('.category-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                currentCategory = tab.dataset.slug;
                currentTreeParent = null;
                renderCategoryTabs();
                loadTagTree(currentCategory);
            });
        });
    }

    function renderTagTree(tags, parentId) {
        if (!tagTreeContainer) return;
        
        // Build breadcrumbs if inside hierarchy
        let breadcrumbsHtml = '';
        if (parentId) {
            breadcrumbsHtml = `
                <div class="tree-breadcrumbs">
                    <button class="breadcrumb-back" onclick="window.tagTreeBack()">
                        ← Back
                    </button>
                </div>
            `;
        }
        
        // Group by parent for tree view
        const html = tags.map(tag => {
            const hasChildren = !tag.is_leaf;
            const count = tag.count || tag.usage_count || 0;
            
            return `
                <div class="tree-node ${hasChildren ? 'has-children' : ''}" 
                     data-id="${tag.id}" data-has-children="${hasChildren}">
                    <div class="tree-node-content">
                        ${hasChildren ? `
                            <button class="tree-expand" onclick="window.tagTreeDrillDown(${tag.id})">
                                ▶
                            </button>
                        ` : '<span class="tree-leaf-icon">•</span>'}
                        <span class="tree-node-name">${tag.display_name || tag.name}</span>
                        ${count > 0 ? `<span class="tree-count">${count}</span>` : ''}
                    </div>
                    <button class="tree-add-btn" onclick="window.addTag(${tag.id})" title="Add tag">
                        +
                    </button>
                </div>
            `;
        }).join('');
        
        tagTreeContainer.innerHTML = breadcrumbsHtml + html;
    }

    function renderSearchResults() {
        if (!tagTreeContainer) return;
        
        if (searchResults.length === 0) {
            tagTreeContainer.innerHTML = `
                <div class="search-empty">
                    No tags found. Try different search terms.
                </div>
            `;
            return;
        }
        
        const html = searchResults.map(tag => {
            const pathParts = tag.path?.split('.') || [tag.name];
            const pathDisplay = pathParts.slice(0, -1).join(' > ') || 'Root';
            
            return `
                <div class="search-result" data-id="${tag.id}" onclick="window.addTag(${tag.id})">
                    <div class="search-result-main">
                        <span class="search-result-name" 
                              style="--tag-color: ${tag.category_color || '#6b7280'}">
                            ${tag.display_name || tag.name}
                        </span>
                        <span class="search-result-count">${tag.count || 0}</span>
                    </div>
                    <div class="search-result-path">${pathDisplay}</div>
                </div>
            `;
        }).join('');
        
        tagTreeContainer.innerHTML = `
            <div class="search-results-header">
                ${searchResults.length} result${searchResults.length !== 1 ? 's' : ''}
            </div>
            ${html}
        `;
    }

    function renderCurrentTags() {
        if (!currentTagsContainer) return;
        
        if (currentTags.length === 0) {
            currentTagsContainer.innerHTML = '<span class="no-tags-hint">No tags selected</span>';
            return;
        }
        
        // Group by category
        const byCategory = {};
        currentTags.forEach(tag => {
            const catSlug = tag.category_slug || 'other';
            if (!byCategory[catSlug]) {
                byCategory[catSlug] = {
                    name: tag.category_name || 'Other',
                    color: tag.category_color || '#6b7280',
                    tags: []
                };
            }
            byCategory[catSlug].tags.push(tag);
        });
        
        // Render grouped
        const html = Object.entries(byCategory).map(([slug, cat]) => `
            <div class="tag-group" data-category="${slug}">
                <div class="tag-group-header" style="color: ${cat.color}">
                    ${cat.name}
                </div>
                <div class="tag-group-tags">
                    ${cat.tags.map(tag => {
                        const isExplicit = tag.is_explicit === 1 || tag.is_explicit === true;
                        const removeBtn = isExplicit ? `
                            <button class="tag-remove" 
                                    onclick="window.removeTag(${tag.id})"
                                    title="Remove">×</button>
                        ` : '';
                        return `
                        <span class="tag-chip ${isExplicit ? '' : 'tag-chip-inherited'}" 
                              style="--tag-color: ${cat.color}"
                              title="${tag.path || tag.name}${isExplicit ? '' : ' (auto-added)'}">
                            ${tag.display_name || tag.name}
                            ${removeBtn}
                        </span>
                        `;
                    }).join('')}
                </div>
            </div>
        `).join('');
        
        currentTagsContainer.innerHTML = html;
    }

    function showTreeView() {
        searchResults = [];
        loadTagTree(currentCategory, currentTreeParent);
    }

    // ========================================================================
    // Helpers
    // ========================================================================

    function addToRecent(tag) {
        // Remove if exists
        recentTags = recentTags.filter(t => t.id !== tag.id);
        // Add to front
        recentTags.unshift(tag);
        // Keep only 10
        recentTags = recentTags.slice(0, 10);
        // Save
        localStorage.setItem('recentTags', JSON.stringify(recentTags));
    }

    // ========================================================================
    // Public API
    // ========================================================================

    window.openTagEditor = async function(itemId) {
        editingItemId = itemId || window.currentLightboxPhotoId;
        if (!editingItemId) {
            console.error('[gallery-tags] No item ID');
            return;
        }
        
        // Load current tags
        try {
            const resp = await fetch(`${getBaseUrl()}/api/items/${editingItemId}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                currentTags = data.tags || [];
                renderCurrentTags();
            }
        } catch (e) {
            console.error('Failed to load tags:', e);
        }
        
        // Show panel
        tagEditorPanel?.classList.add('open');
        lightbox?.classList.add('panel-open');
        
        // Reset state
        currentCategory = null;
        currentTreeParent = null;
        if (tagSearch) tagSearch.value = '';
        
        // Load initial tree
        showTreeView();
        
        // Register back button handler
        if (window.BackButtonManager) {
            window.BackButtonManager.register('tag-editor', window.closeTagEditor);
        }
    };

    window.closeTagEditor = function() {
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('tag-editor', true);
        }
        
        tagEditorPanel?.classList.remove('open');
        lightbox?.classList.remove('panel-open');
        editingItemId = null;
        currentTags = [];
        searchResults = [];
    };

    window.addTag = addTag;
    window.removeTag = removeTag;
    window.saveTagChanges = saveChanges;
    
    window.tagTreeDrillDown = function(tagId) {
        currentTreeParent = tagId;
        loadTagTree(currentCategory, tagId);
    };
    
    window.tagTreeBack = function() {
        if (currentTreeParent) {
            // Need to find parent - for simplicity reload root
            currentTreeParent = null;
            loadTagTree(currentCategory);
        }
    };

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
