/**
 * Gallery Tags module
 * Phase 4: Tag editor functionality
 */

(function() {
    // Tag editor elements
    let tagEditorPanel = null;
    let tagSearch = null;
    let presetsContainer = null;
    let addNewTagSection = null;
    let currentTagsContainer = null;

    // State
    let categories = [];
    let editingTags = [];
    let originalTags = [];
    let selectedCategoryId = null;
    let currentPhotoId = null;

    function init() {
        tagEditorPanel = document.getElementById('tag-editor-panel');
        if (!tagEditorPanel) {
            console.log('[gallery-tags] No tag editor panel');
            return;
        }

        tagSearch = document.getElementById('tag-search');
        presetsContainer = document.getElementById('tag-presets-container');
        addNewTagSection = document.getElementById('add-new-tag-section');
        currentTagsContainer = document.getElementById('current-tags-container');

        setupEventListeners();
        console.log('[gallery-tags] Initialized');
    }

    function setupEventListeners() {
        // Close button
        const closeBtn = document.getElementById('tag-editor-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', window.closeTagEditor);
        }

        // Cancel button
        const cancelBtn = document.querySelector('#tag-editor-panel .btn-secondary');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', window.closeTagEditor);
        }

        // Save button
        const saveBtn = document.getElementById('save-tags-btn');
        if (saveBtn) {
            saveBtn.addEventListener('click', window.saveTagChanges);
        }

        // Tag search
        if (tagSearch) {
            let searchTimeout;
            tagSearch.addEventListener('input', () => {
                clearTimeout(searchTimeout);
                const query = tagSearch.value.trim();

                searchTimeout = setTimeout(async () => {
                    const presets = await window.loadTagPresets?.(query);
                    if (!presets) return;

                    if (query.length > 0 && addNewTagSection) {
                        const hasExactMatch = presets.some(cat =>
                            cat.tags.some(t => t.name.toLowerCase() === query.toLowerCase())
                        );

                        const addBtn = document.getElementById('add-new-tag-btn');
                        if (!hasExactMatch) {
                            const newTagName = document.getElementById('new-tag-name');
                            if (newTagName) newTagName.textContent = query;
                            addNewTagSection.classList.remove('hidden');
                            selectedCategoryId = null;
                            document.querySelectorAll('.category-btn').forEach(btn => btn.classList.remove('selected'));
                            if (addBtn) addBtn.disabled = true;
                        } else {
                            addNewTagSection.classList.add('hidden');
                        }
                    } else if (addNewTagSection) {
                        addNewTagSection.classList.add('hidden');
                    }
                }, 300);
            });
        }
    }

    window.openTagEditor = async function(photoId) {
        if (!tagEditorPanel) return;
        
        currentPhotoId = photoId;
        editingTags = [];
        originalTags = [];
        selectedCategoryId = null;

        // Load current tags
        try {
            const resp = await fetch(`${getBaseUrl()}/api/photos/${photoId}/tags`);
            if (resp.ok) {
                const data = await resp.json();
                editingTags = data.tags || [];
                originalTags = [...editingTags];
            }
        } catch (e) {
            console.error('Failed to load tags:', e);
        }

        window.updateCurrentTagsDisplay();
        await window.loadCategories?.();
        await window.loadTagPresets?.();

        tagEditorPanel.classList.add('open');
    };

    window.closeTagEditor = function() {
        if (tagEditorPanel) {
            tagEditorPanel.classList.remove('open');
        }
        currentPhotoId = null;
    };

    window.saveTagChanges = async function() {
        if (!currentPhotoId) return;

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/photos/${currentPhotoId}/tags`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ tags: editingTags.map(t => t.name) })
            });

            if (!resp.ok) throw new Error('Failed to save tags');

            window.closeTagEditor();
            // Refresh to show new tags
            if (window.currentFolderId && typeof navigateToFolder === 'function') {
                navigateToFolder(window.currentFolderId, false);
            }
        } catch (err) {
            console.error('Failed to save tags:', err);
            alert('Failed to save tags: ' + err.message);
        }
    };

    window.toggleTag = function(tagName, categoryId, color) {
        const existingIndex = editingTags.findIndex(t => t.name.toLowerCase() === tagName.toLowerCase());
        
        if (existingIndex >= 0) {
            editingTags.splice(existingIndex, 1);
        } else {
            editingTags.push({ name: tagName, category_id: categoryId, color: color });
        }

        window.updateCurrentTagsDisplay();
        window.updatePresetButtons?.();
    };

    window.updateCurrentTagsDisplay = function() {
        if (!currentTagsContainer) return;

        if (editingTags.length === 0) {
            currentTagsContainer.innerHTML = '<span class="no-tags-hint">No tags selected</span>';
            return;
        }

        currentTagsContainer.innerHTML = editingTags.map(tag => `
            <span class="tag selected" style="--tag-color: ${tag.color || '#6b7280'}">
                ${escapeHtml(tag.name)}
                <button onclick="window.toggleTag('${escapeHtml(tag.name)}', ${tag.category_id || 'null'}, '${tag.color || ''}')">×</button>
            </span>
        `).join('');
    };

    window.loadCategories = async function() {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/categories`);
            if (!resp.ok) return;
            categories = await resp.json();
            
            const container = document.getElementById('category-buttons');
            if (!container) return;
            
            container.innerHTML = categories.map(cat => `
                <button class="category-btn" data-category-id="${cat.id}" onclick="selectCategory(${cat.id})">
                    ${escapeHtml(cat.name)}
                </button>
            `).join('');
        } catch (err) {
            console.error('Failed to load categories:', err);
        }
    };

    window.loadTagPresets = async function(query = '') {
        try {
            const url = query 
                ? `${getBaseUrl()}/api/tags/presets?q=${encodeURIComponent(query)}`
                : `${getBaseUrl()}/api/tags/presets`;
            const resp = await fetch(url);
            if (!resp.ok) return;
            
            const data = await resp.json();
            renderTagPresets(data.categories || []);
            return data.categories;
        } catch (err) {
            console.error('Failed to load tag presets:', err);
        }
    };

    function renderTagPresets(cats) {
        if (!presetsContainer) return;
        
        if (cats.length === 0) {
            presetsContainer.innerHTML = '';
            return;
        }
        
        presetsContainer.innerHTML = cats.map(cat => `
            <div class="tag-category">
                <h4>${escapeHtml(cat.name)}</h4>
                <div class="tag-buttons">
                    ${(cat.tags || []).map(tag => {
                        const isSelected = editingTags.find(t => t.name.toLowerCase() === tag.name.toLowerCase());
                        return `
                            <button class="tag-btn ${isSelected ? 'selected' : ''}" 
                                    onclick="window.toggleTag('${escapeHtml(tag.name)}', ${cat.id}, '${tag.color || '#6b7280'}')"
                                    style="--tag-color: ${tag.color || '#6b7280'}">
                                ${escapeHtml(tag.name)}
                            </button>
                        `;
                    }).join('')}
                </div>
            </div>
        `).join('');
    }

    window.updatePresetButtons = function() {
        // Re-render to update selection state
        window.loadTagPresets?.(tagSearch?.value || '');
    };

    window.selectCategory = function(categoryId) {
        selectedCategoryId = categoryId;
        document.querySelectorAll('.category-btn').forEach(btn => {
            btn.classList.toggle('selected', parseInt(btn.dataset.categoryId) === categoryId);
        });
        const addBtn = document.getElementById('add-new-tag-btn');
        if (addBtn) addBtn.disabled = !selectedCategoryId;
    };

    window.requestAIAnalysis = async function() {
        if (!currentPhotoId) return;
        
        const btn = document.getElementById('request-ai-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Analyzing...';
        }
        
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/photos/${currentPhotoId}/analyze`, {
                method: 'POST'
            });
            
            if (!resp.ok) throw new Error('Analysis failed');
            
            const data = await resp.json();
            if (data.tags) {
                // Add suggested tags to editing tags
                data.tags.forEach(tag => {
                    if (!editingTags.find(t => t.name.toLowerCase() === tag.toLowerCase())) {
                        editingTags.push({ name: tag, category_id: null, color: '#6b7280' });
                    }
                });
                window.updateCurrentTagsDisplay();
            }
        } catch (err) {
            console.error('AI analysis failed:', err);
            alert('AI analysis failed: ' + err.message);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                        <path d="M12 2a4 4 0 0 1 4 4v2a4 4 0 0 1-8 0V6a4 4 0 0 1 4-4z"/>
                        <path d="M16 14v1a4 4 0 0 1-8 0v-1"/>
                        <circle cx="12" cy="20" r="2"/>
                        <path d="M12 18v-2"/>
                    </svg>
                    Request AI Analysis
                `;
            }
        }
    };

    window.addNewTag = async function() {
        const tagName = document.getElementById('new-tag-name')?.textContent;
        if (!tagName || !selectedCategoryId) return;
        
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/tags`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: tagName, category_id: selectedCategoryId })
            });
            
            if (!resp.ok) throw new Error('Failed to add tag');
            
            const data = await resp.json();
            editingTags.push({ name: tagName, category_id: selectedCategoryId, color: data.color || '#6b7280' });
            window.updateCurrentTagsDisplay();
            
            // Clear search
            if (tagSearch) tagSearch.value = '';
            if (addNewTagSection) addNewTagSection.classList.add('hidden');
            
            // Reload presets
            await window.loadTagPresets?.();
        } catch (err) {
            console.error('Failed to add tag:', err);
            alert('Failed to add tag: ' + err.message);
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-tags.js] Loaded');
})();
