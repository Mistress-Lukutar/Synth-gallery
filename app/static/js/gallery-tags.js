/**
 * Gallery Tags v3 - Flat Tags with Implications and Related Suggestions
 *
 * Features:
 * - Search-first tag selection
 * - Related tag suggestions based on co-occurrence
 * - Implied tag badges (auto-resolved via implication graph)
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
    let tagResultsContainer = null;
    let currentTagsContainer = null;
    let lightbox = null;

    // State
    let editingItemId = null;
    let currentTags = [];
    let searchResults = [];
    let recentTags = [];
    let relatedSuggestions = [];

    // Metadata state
    let currentItemMetadata = null;
    let hasMetadataChanges = false;
    let isEditMode = false;

    function init() {
        itemDetailsPanel = document.getElementById('item-details-panel');
        if (!itemDetailsPanel) return;

        lightbox = document.getElementById('lightbox');
        tagSearch = document.getElementById('tag-search');
        tagResultsContainer = document.getElementById('tag-tree-container');
        currentTagsContainer = document.getElementById('current-tags-container');

        setupEventListeners();
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
                    renderSearchResults();
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
                    searchResults = [];
                    renderSearchResults();
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

        // Apply preview mode by default
        applyEditMode(false);

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

    function formatDateTime(dateStr) {
        if (!dateStr) return null;
        const date = new Date(dateStr);
        if (isNaN(date.getTime())) return null;
        return date.toLocaleString();
    }

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
        return date.toISOString().slice(0, 16);
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
                'link', 'image'
            ],
            status: false,
            minHeight: '80px',
            maxHeight: '300px',
            placeholder: 'Enter description...',
            initialValue: initialValue || ''
        });
    }

    // Description preview rendering
    function updateDescriptionPreview() {
        const previewEl = document.getElementById('item-description-preview');
        if (!previewEl || !descriptionEditor) return;

        const markdown = descriptionEditor.value();
        if (!markdown || !markdown.trim()) {
            previewEl.innerHTML = '<em class="no-description">No description</em>';
            return;
        }
        previewEl.innerHTML = renderMarkdown(markdown);
    }

    // Simple markdown renderer for preview
    function renderMarkdown(text) {
        if (!text) return '';
        // Escape HTML
        let html = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        // Headers
        html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
        html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
        html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
        // Bold and italic
        html = html.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>');
        html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
        // Code
        html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
        // Links
        html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
        // Line breaks
        html = html.replace(/\n/g, '<br>');
        return html;
    }

    // Global edit mode toggle (controls description + tags)
    let currentDescriptionMode = 'preview';

    function applyEditMode(edit) {
        isEditMode = edit;
        const wrapper = document.querySelector('.description-editor-wrapper');
        const previewEl = document.getElementById('item-description-preview');
        const editBtn = document.getElementById('edit-mode-btn');
        const pencilIcon = document.getElementById('edit-icon-pencil');
        const checkIcon = document.getElementById('edit-icon-check');
        const tagSearchContainer = document.querySelector('.tag-search-container');

        if (isEditMode) {
            // Edit mode: show description editor, show tag search, show remove buttons
            wrapper?.classList.add('edit-mode');
            wrapper?.classList.remove('preview-mode');
            previewEl?.classList.add('hidden');
            if (editBtn) editBtn.title = 'Done';
            if (pencilIcon) pencilIcon.style.display = 'none';
            if (checkIcon) checkIcon.style.display = '';
            if (tagSearchContainer) tagSearchContainer.style.display = '';
        } else {
            // Preview mode: show description preview, hide tag search, hide remove buttons
            wrapper?.classList.add('preview-mode');
            wrapper?.classList.remove('edit-mode');
            updateDescriptionPreview();
            previewEl?.classList.remove('hidden');
            if (editBtn) editBtn.title = 'Edit';
            if (pencilIcon) pencilIcon.style.display = '';
            if (checkIcon) checkIcon.style.display = 'none';
            if (tagSearchContainer) tagSearchContainer.style.display = 'none';
        }

        renderCurrentTags();
    }

    window.toggleEditMode = function() {
        applyEditMode(!isEditMode);
    };

    // ========================================================================
    // Tag API Functions
    // ========================================================================

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
                await loadRelatedSuggestions();

                // Add to recent
                const tag = searchResults.find(t => t.id === tagId);
                if (tag) addToRecent(tag);

                // Clear search
                if (tagSearch) {
                    tagSearch.value = '';
                    tagSearch.focus();
                    searchResults = [];
                    renderSearchResults();
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
                await loadRelatedSuggestions();
            }
        } catch (e) {
            console.error('Failed to remove tag:', e);
        }
    }

    async function loadRelatedSuggestions() {
        if (!editingItemId) return;
        const explicit = currentTags.filter(t => t.is_explicit);
        if (explicit.length === 0) {
            relatedSuggestions = [];
            renderRelatedSuggestions();
            return;
        }
        // Aggregate related tags from all explicit tags
        const relatedMap = new Map();
        try {
            for (const tag of explicit) {
                const resp = await fetch(
                    `${getBaseUrl()}/api/tags/${tag.id}/related?limit=5`
                );
                if (resp.ok) {
                    const data = await resp.json();
                    for (const t of (data.tags || [])) {
                        const existing = relatedMap.get(t.id);
                        if (existing) {
                            existing.count += t.count || 1;
                        } else {
                            relatedMap.set(t.id, { ...t });
                        }
                    }
                }
            }
        } catch (e) {
            console.error('Failed to load related tags:', e);
        }
        // Exclude already applied tags
        const currentIds = new Set(currentTags.map(t => t.id));
        relatedSuggestions = Array.from(relatedMap.values())
            .filter(t => !currentIds.has(t.id))
            .sort((a, b) => (b.count || 0) - (a.count || 0))
            .slice(0, 12);
        renderRelatedSuggestions();
    }

    // ========================================================================
    // Rendering
    // ========================================================================

    function renderSearchResults() {
        if (!tagResultsContainer) return;

        if (searchResults.length === 0) {
            tagResultsContainer.innerHTML = '';
            return;
        }

        const html = searchResults.map(tag => {
            return `
                <div class="search-result" data-id="${tag.id}" onclick="window.addTag(${tag.id})">
                    <div class="search-result-main">
                        <span class="search-result-name"
                              style="--tag-color: ${tag.category_color || '#6b7280'}">
                            ${escapeHtml(tag.display_name || tag.name)}
                        </span>
                        <span class="search-result-count">${tag.count || 0}</span>
                    </div>
                </div>
            `;
        }).join('');

        tagResultsContainer.innerHTML = `
            <div class="search-results-header">
                ${searchResults.length} result${searchResults.length !== 1 ? 's' : ''}
            </div>
            ${html}
        `;
    }

    function renderRelatedSuggestions() {
        const container = document.getElementById('related-tags-container');
        if (!container) return;

        if (relatedSuggestions.length === 0) {
            container.innerHTML = '';
            return;
        }

        const html = relatedSuggestions.map(tag => `
            <button class="related-tag-btn"
                    style="--tag-color: ${tag.category_color || '#6b7280'}"
                    onclick="window.addTag(${tag.id})"
                    title="Add ${escapeHtml(tag.display_name || tag.name)}">
                + ${escapeHtml(tag.display_name || tag.name)}
            </button>
        `).join('');

        container.innerHTML = `
            <div class="related-tags-header">Related suggestions</div>
            <div class="related-tags-list">${html}</div>
        `;
    }

    function renderCurrentTags() {
        if (!currentTagsContainer) return;

        // New API format: is_explicit flag
        const explicit = currentTags.filter(t => t.is_explicit);
        const implied = currentTags.filter(t => !t.is_explicit);

        let html = '';

        // Single section: all tags together
        if (explicit.length > 0 || implied.length > 0) {
            html += `
                <div class="tags-section">
                    <div class="tags-list">
                        ${explicit.map(tag => `
                            <span class="tag-chip tag-chip-editable"
                                  style="--tag-color: ${tag.category_color || '#6b7280'}">
                                ${escapeHtml(tag.display_name || tag.name)}
                                ${isEditMode ? `<button class="tag-remove"
                                        onclick="window.removeTag(${tag.id})"
                                        title="Remove">×</button>` : ''}
                            </span>
                        `).join('')}
                        ${implied.map(tag => `
                            <span class="tag-chip tag-chip-implied"
                                  style="--tag-color: ${tag.category_color || '#6b7280'}"
                                  title="Automatically added via implication">
                                ${escapeHtml(tag.display_name || tag.name)}
                            </span>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        if (explicit.length === 0 && implied.length === 0) {
            html = isEditMode
                ? '<span class="no-tags-hint">No tags selected. Search to add tags.</span>'
                : '<span class="no-tags-hint">No tags selected</span>';
        }

        currentTagsContainer.innerHTML = html;
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

    function loadRecentTags() {
        try {
            const stored = localStorage.getItem('recentTags');
            if (stored) {
                recentTags = JSON.parse(stored);
            }
        } catch (e) {
            recentTags = [];
        }
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
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
                await loadRelatedSuggestions();
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
                await loadRelatedSuggestions();
            }
        } catch (e) {
            console.error('Failed to load tags:', e);
        }

        // Show panel (update to use new panel ID)
        itemDetailsPanel?.classList.add('open');
        lightbox?.classList.add('panel-open');

        // Reset search
        if (tagSearch) tagSearch.value = '';
        searchResults = [];
        renderSearchResults();

        // Setup auto-save listeners
        setupAutoSaveListeners();

        // Register back button
        if (window.BackButtonManager) {
            window.BackButtonManager.register('item-details', window.closeItemDetails);
        }
    };

    // EasyMDE instance for description
    let descriptionEditor = null;

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
        relatedSuggestions = [];
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

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
