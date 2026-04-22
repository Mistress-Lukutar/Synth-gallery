/**
 * Gallery Tags v2 - Hierarchical Tag System with Metadata Editing
 * 
 * Features:
 * - Tree view for browsing tags
 * - Autocomplete with usage count
 * - Negative search support
 * - Automatic ancestor inclusion
 * - Item metadata editing (title, description, taken_at)
 */

(function() {
    // Inline fallback for copyToClipboard in case core.js is cached/old
    const _copyToClipboard = window.copyToClipboard || async function(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                console.log('[clipboard] Copied using navigator.clipboard API');
                return true;
            } catch (err) {
                console.warn('[clipboard] navigator.clipboard failed:', err);
            }
        }
        try {
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-9999px';
            textArea.style.top = '0';
            textArea.setAttribute('readonly', '');
            document.body.appendChild(textArea);
            textArea.focus();
            textArea.select();
            const successful = document.execCommand('copy');
            document.body.removeChild(textArea);
            if (successful) {
                console.log('[clipboard] Copied using execCommand fallback');
                return true;
            }
            console.warn('[clipboard] execCommand returned false');
        } catch (err) {
            console.warn('[clipboard] execCommand fallback failed:', err);
        }
        console.warn('[clipboard] All automated clipboard methods failed');
        return false;
    };

    // DOM Elements
    let itemDetailsPanel = null;
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
    
    // Metadata state
    let currentItemMetadata = null;
    let hasMetadataChanges = false;

    function init() {
        itemDetailsPanel = document.getElementById('item-details-panel');
        if (!itemDetailsPanel) return;

        lightbox = document.getElementById('lightbox');
        tagSearch = document.getElementById('tag-search');
        tagTreeContainer = document.getElementById('tag-tree-container');
        currentTagsContainer = document.getElementById('current-tags-container');
        categoryTabs = document.getElementById('category-tabs');

        setupEventListeners();
        loadCategories();
        loadRecentTags();
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
        itemDetailsPanel?.addEventListener('click', (e) => {
            if (e.target === itemDetailsPanel) {
                closeItemDetails();
            }
        });
    }

    // ========================================================================
    // Metadata API Functions
    // ========================================================================

    async function loadMetadata(itemId) {
        const resp = await fetch(`${getBaseUrl()}/api/items/${itemId}/metadata`);
        if (!resp.ok) throw new Error('Failed to load metadata');
        return await resp.json();
    }

    async function saveMetadata(itemId, metadata) {
        return await csrfFetch(`${getBaseUrl()}/api/items/${itemId}/metadata`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(metadata)
        });
    }

    function renderMetadata(metadata) {
        currentItemMetadata = metadata;
        
        // Editable fields
        document.getElementById('item-title').value = metadata.title || '';
        
        // Initialize Markdown editor (but keep it hidden initially)
        initDescriptionEditor(metadata.description || '');
        currentDescriptionMode = 'preview';
        
        // Apply preview mode by default (Edit button to enter edit mode)
        const wrapper = document.querySelector('.description-editor-wrapper');
        const toggleText = document.getElementById('desc-toggle-text');
        const previewEl = document.getElementById('item-description-preview');
        
        wrapper?.classList.add('preview-mode');
        wrapper?.classList.remove('edit-mode');
        if (toggleText) toggleText.textContent = 'Edit';
        updateDescriptionPreview();
        previewEl?.classList.remove('hidden');
        
        // Read-only details (includes Date Taken)
        document.getElementById('detail-filename').textContent = metadata.original_name || '-';
        document.getElementById('detail-type').textContent = metadata.content_type || '-';
        document.getElementById('detail-size').textContent = formatFileSize(metadata.file_size);
        document.getElementById('detail-dimensions').textContent = 
            metadata.width && metadata.height ? `${metadata.width} × ${metadata.height}` : '-';
        document.getElementById('detail-duration').textContent = 
            metadata.duration ? formatDuration(metadata.duration) : '-';
        document.getElementById('detail-uploaded').textContent = formatDate(metadata.uploaded_at);
        document.getElementById('detail-modified').textContent = formatDate(metadata.updated_at);
        document.getElementById('detail-taken-at').textContent = formatDate(metadata.taken_at) || '-';
        
        // Show/hide media-specific rows
        document.getElementById('detail-dimensions-row').classList.toggle('hidden', !metadata.width);
        document.getElementById('detail-duration-row').classList.toggle('hidden', !metadata.duration);
    }
    
    // Initialize EasyMDE editor
    function initDescriptionEditor(initialValue) {
        const textarea = document.getElementById('item-description');
        if (!textarea) return;
        
        // Destroy existing instance if any
        if (descriptionEditor) {
            descriptionEditor.toTextArea();
            descriptionEditor = null;
        }
        
        // Set initial value
        textarea.value = initialValue || '';
        
        // Create EasyMDE instance with auto-expanding
        descriptionEditor = new EasyMDE({
            element: textarea,
            autofocus: false,
            spellChecker: false,
            autoDownloadFontAwesome: false,
            toolbar: [
                'bold', 'italic', 'heading', '|',
                'code', 'quote', 'unordered-list', 'ordered-list', '|',
                'link', 'image', '|',
                'preview', 'side-by-side', 'fullscreen', '|',
                'guide'
            ],
            status: ['lines', 'words'],
            minHeight: '80px',
            maxHeight: '400px',
            placeholder: 'Add a description... (Markdown supported)',
            initialValue: initialValue || '',
            shortcuts: {
                'togglePreview': null,
                'toggleSideBySide': null,
                'toggleFullscreen': null
            },
            forceSync: true
        });
        
        // Hook into blur event for save (not change)
        descriptionEditor.codemirror.on('blur', () => {
            if (hasChanges()) autoSave();
        });
        
        // Initial render of preview
        updateDescriptionPreview();
    }
    
    // Update preview content
    function updateDescriptionPreview() {
        const previewEl = document.getElementById('item-description-preview');
        const value = descriptionEditor ? descriptionEditor.value() : (document.getElementById('item-description')?.value || '');
        
        if (previewEl && typeof marked !== 'undefined') {
            // Parse markdown
            previewEl.innerHTML = marked.parse(value, { breaks: true });
            
            // Apply syntax highlighting to code blocks
            if (typeof hljs !== 'undefined') {
                previewEl.querySelectorAll('pre code').forEach((block) => {
                    hljs.highlightElement(block);
                });
            }
            
            // Render LaTeX math
            if (typeof renderMathInElement !== 'undefined') {
                renderMathInElement(previewEl, {
                    delimiters: [
                        {left: '$$', right: '$$', display: true},
                        {left: '$', right: '$', display: false}
                    ],
                    throwOnError: false
                });
            }
            
            addCopyButtonsToCodeBlocks(previewEl);
        }
    }
    
    // Add copy buttons to code blocks
    function addCopyButtonsToCodeBlocks(container) {
        const codeBlocks = container.querySelectorAll('pre code');
        codeBlocks.forEach((codeBlock, index) => {
            const pre = codeBlock.parentElement;
            if (pre.querySelector('.code-copy-btn')) return; // Already has button
            
            const wrapper = document.createElement('div');
            wrapper.className = 'code-block-wrapper';
            wrapper.style.position = 'relative';
            
            const copyBtn = document.createElement('button');
            copyBtn.className = 'code-copy-btn';
            copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
            copyBtn.title = 'Copy code';
            copyBtn.onclick = () => copyCodeToClipboard(codeBlock, copyBtn);
            
            pre.style.position = 'relative';
            pre.appendChild(copyBtn);
        });
    }
    
    // Copy code to clipboard
    async function copyCodeToClipboard(codeBlock, button) {
        const code = codeBlock.textContent;
        const copied = await _copyToClipboard(code);
        if (copied) {
            button.classList.add('copied');
            button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="20 6 9 17 4 12"></polyline></svg>';
            setTimeout(() => {
                button.classList.remove('copied');
                button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
            }, 2000);
        } else {
            window.prompt('Copy code manually:', code);
        }
    }
    
    // Toggle between edit and preview mode
    window.toggleDescriptionMode = async function() {
        const newMode = currentDescriptionMode === 'edit' ? 'preview' : 'edit';
        
        // Save when switching from edit to preview
        if (currentDescriptionMode === 'edit' && newMode === 'preview') {
            if (hasChanges()) await autoSave();
        }
        
        currentDescriptionMode = newMode;
        
        const wrapper = document.querySelector('.description-editor-wrapper');
        const toggleBtn = document.getElementById('desc-toggle-btn');
        const toggleText = document.getElementById('desc-toggle-text');
        const previewEl = document.getElementById('item-description-preview');
        
        if (newMode === 'edit') {
            wrapper?.classList.remove('preview-mode');
            wrapper?.classList.add('edit-mode');
            toggleText.textContent = 'Save';
            previewEl?.classList.add('hidden');
            descriptionEditor?.codemirror.refresh();
        } else {
            wrapper?.classList.add('preview-mode');
            wrapper?.classList.remove('edit-mode');
            toggleText.textContent = 'Edit';
            updateDescriptionPreview();
            previewEl?.classList.remove('hidden');
        }
    };
    
    // Legacy function for compatibility
    window.setDescriptionMode = function(mode) {
        if ((mode === 'preview' && currentDescriptionMode === 'edit') ||
            (mode === 'edit' && currentDescriptionMode === 'preview')) {
            toggleDescriptionMode();
        }
    };
    
    function formatDateTime(dateStr) {
        if (!dateStr) return null;
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return null;
        return date.toLocaleString();
    }

    // Helper functions
    function formatFileSize(bytes) {
        if (!bytes) return '-';
        const units = ['B', 'KB', 'MB', 'GB'];
        let size = bytes;
        let unitIndex = 0;
        while (size >= 1024 && unitIndex < units.length - 1) {
            size /= 1024;
            unitIndex++;
        }
        return `${size.toFixed(1)} ${units[unitIndex]}`;
    }

    function formatDuration(seconds) {
        if (!seconds) return '-';
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    function formatDate(dateStr) {
        if (!dateStr) return '-';
        return new Date(dateStr).toLocaleDateString();
    }

    function formatForDateTimeLocal(dateStr) {
        if (!dateStr) return '';
        const date = new Date(dateStr);
        return date.toISOString().slice(0, 16); // YYYY-MM-DDTHH:mm
    }

    // Collapsible toggle
    window.toggleDetailsSection = function() {
        const content = document.getElementById('details-content');
        const icon = document.querySelector('.collapsible-toggle .toggle-icon');
        content.classList.toggle('collapsed');
        icon.textContent = content.classList.contains('collapsed') ? '▶' : '▼';
    };

    // ========================================================================
    // Tag API Calls
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
                currentTags = data.tags?.all_tags || [];
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
                currentTags = data.tags?.all_tags || [];
                renderCurrentTags();
            }
        } catch (e) {
            console.error('Failed to remove tag:', e);
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
        
        // Separate explicit and inherited
        const explicit = currentTags.filter(t => !t.is_inherited);
        const inherited = currentTags.filter(t => t.is_inherited);
        
        let html = '';
        
        // Section 1: Your Tags (explicit) - editable
        if (explicit.length > 0) {
            html += `
                <div class="tags-section explicit-tags">
                    <div class="tags-section-header">
                        <span>Your Tags</span>
                        <span class="tags-hint">Click × to remove</span>
                    </div>
                    <div class="tags-list">
                        ${explicit.map(tag => `
                            <span class="tag-chip tag-chip-editable" 
                                  style="--tag-color: ${tag.category_color || '#6b7280'}"
                                  title="${tag.path || tag.name}">
                                ${tag.display_name || tag.name}
                                <button class="tag-remove" 
                                        onclick="window.removeTag(${tag.id})"
                                        title="Remove">×</button>
                            </span>
                        `).join('')}
                    </div>
                </div>
            `;
        }
        
        // Section 2: Inherited Tags - read-only
        if (inherited.length > 0) {
            html += `
                <div class="tags-section inherited-tags">
                    <div class="tags-section-header">
                        <span>Inherited Tags</span>
                        <span class="tags-hint">Auto-added from your tags</span>
                    </div>
                    <div class="tags-list">
                        ${inherited.map(tag => `
                            <span class="tag-chip tag-chip-inherited" 
                                  style="--tag-color: ${tag.category_color || '#6b7280'}"
                                  title="${tag.path || tag.name}">
                                ${tag.display_name || tag.name}
                            </span>
                        `).join('')}
                    </div>
                </div>
            `;
        }
        
        if (explicit.length === 0 && inherited.length === 0) {
            html = '<span class="no-tags-hint">No tags selected. Search or browse to add tags.</span>';
        }
        
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

    // Load and display item details (used when panel is already open)
    window.loadItemDetails = async function(itemId) {
        if (!itemId) return;
        editingItemId = itemId;
        
        // Save any pending changes for previous item
        clearTimeout(saveTimeout);
        if (hasChanges()) {
            await autoSave();
        }
        
        // Load new metadata
        try {
            const metadata = await loadMetadata(itemId);
            renderMetadata(metadata);
            // Store original values for dirty check
            originalValues = {
                title: metadata.title ?? '',
                description: metadata.description ?? ''
            };
        } catch (e) {
            console.error('Failed to load metadata:', e);
        }
        
        // Load tags for new item
        try {
            const resp = await fetch(`${getBaseUrl()}/api/items/${itemId}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                currentTags = data.all_tags || [];
                renderCurrentTags();
            }
        } catch (e) {
            console.error('Failed to load tags:', e);
        }
    };
    
    window.openItemDetails = async function(itemId) {
        editingItemId = itemId || window.currentLightboxPhotoId;
        if (!editingItemId) {
            console.error('[gallery-tags] No item ID');
            return;
        }
        
        // Close album editor if open
        const albumEditorPanel = document.getElementById('album-editor-panel');
        if (albumEditorPanel?.classList.contains('open')) {
            window.closeAlbumEditor?.();
        }
        
        // Load metadata
        try {
            const metadata = await loadMetadata(editingItemId);
            renderMetadata(metadata);
            // Store original values for dirty check (normalize null to empty string)
            originalValues = {
                title: metadata.title ?? '',
                description: metadata.description ?? ''
            };
        } catch (e) {
            console.error('Failed to load metadata:', e);
        }
        
        // Load tags (existing code)
        try {
            const resp = await fetch(`${getBaseUrl()}/api/items/${editingItemId}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                currentTags = data.all_tags || [];
                renderCurrentTags();
            }
        } catch (e) {
            console.error('Failed to load tags:', e);
        }
        
        // Show panel (update to use new panel ID)
        itemDetailsPanel?.classList.add('open');
        lightbox?.classList.add('panel-open');
        
        // Reset state
        currentCategory = null;
        currentTreeParent = null;
        if (tagSearch) tagSearch.value = '';
        showTreeView();
        
        // Setup auto-save listeners
        setupAutoSaveListeners();
        
        // Register back button
        if (window.BackButtonManager) {
            window.BackButtonManager.register('item-details', window.closeItemDetails);
        }
    };

    // EasyMDE instance for description
    let descriptionEditor = null;
    let currentDescriptionMode = 'edit';
    
    // Auto-save with debounce and dirty check
    let saveTimeout = null;
    let originalValues = {};
    const SAVE_DELAY = 800; // ms
    
    function hasChanges() {
        // Get current values (treat null/undefined as empty string for comparison)
        const currentTitle = document.getElementById('item-title')?.value ?? '';
        const currentDesc = descriptionEditor ? descriptionEditor.value() : (document.getElementById('item-description')?.value ?? '');
        
        // Get original values (stored as empty string if originally null/undefined)
        const origTitle = originalValues.title ?? '';
        const origDesc = originalValues.description ?? '';
        
        return currentTitle !== origTitle || currentDesc !== origDesc;
    }
    
    function queueAutoSave() {
        if (!editingItemId) return;
        if (!hasChanges()) return; // Don't save if nothing changed
        
        clearTimeout(saveTimeout);
        saveTimeout = setTimeout(() => {
            autoSave();
        }, SAVE_DELAY);
    }
    
    async function autoSave() {
        if (!editingItemId) return;
        if (!hasChanges()) return; // Skip if nothing changed
        
        // Get values
        const titleValue = document.getElementById('item-title')?.value ?? '';
        const descValue = descriptionEditor ? descriptionEditor.value() : (document.getElementById('item-description')?.value ?? '');
        
        // Send empty string as empty string (not null), so backend can clear the field
        const metadata = {
            title: titleValue.trim() || null,  // Title can be null if empty
            description: descValue  // Description can be empty string to clear it
        };
        
        try {
            const resp = await saveMetadata(editingItemId, metadata);
            if (resp.ok) {
                // Update cached metadata
                const data = await resp.json();
                if (data.item) {
                    currentItemMetadata = { ...currentItemMetadata, ...data.item };
                }
                // Refresh lightbox
                if (window.reloadCurrentPhoto) {
                    window.reloadCurrentPhoto();
                }
                // Update original values after successful save (store as empty string if null)
                originalValues = {
                    title: metadata.title ?? '',
                    description: metadata.description ?? ''
                };
            }
        } catch (e) {
            console.error('Auto-save failed:', e);
        }
    }
    
    // Setup save on blur (no auto-save on input)
    function setupAutoSaveListeners() {
        const titleInput = document.getElementById('item-title');
        
        if (titleInput) {
            titleInput.addEventListener('blur', () => {
                if (hasChanges()) autoSave();
            });
        }
    }
    
    window.closeItemDetails = async function() {
        // Save any pending changes before closing (only if changed)
        clearTimeout(saveTimeout);
        if (hasChanges()) {
            await autoSave();
        }
        
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('item-details', true);
        }
        
        itemDetailsPanel?.classList.remove('open');
        lightbox?.classList.remove('panel-open');
        editingItemId = null;
        currentTags = [];
        currentItemMetadata = null;
        searchResults = [];
        originalValues = {}; // Reset dirty check
        
        // Destroy editor instance
        if (descriptionEditor) {
            descriptionEditor.toTextArea();
            descriptionEditor = null;
        }
    };
    
    // Backward compatibility
    window.saveItemDetails = async function() {
        clearTimeout(saveTimeout);
        await autoSave();
    };

    // Keep old function names as aliases for compatibility
    window.openTagEditor = window.openItemDetails;
    window.closeTagEditor = window.closeItemDetails;

    window.addTag = addTag;
    window.removeTag = removeTag;
    window.saveTagChanges = window.closeItemDetails;
    
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
