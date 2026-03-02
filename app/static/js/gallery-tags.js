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
    let lightbox = null;

    // State
    let categories = [];
    let editingTags = [];
    let originalTags = [];
    let selectedCategoryId = null;
    let currentEditingPhotoId = null;

    function init() {
        tagEditorPanel = document.getElementById('tag-editor-panel');
        if (!tagEditorPanel) {
            console.log('[gallery-tags] No tag editor panel');
            return;
        }

        lightbox = document.getElementById('lightbox');
        tagSearch = document.getElementById('tag-search');
        presetsContainer = document.getElementById('tag-presets-container');
        addNewTagSection = document.getElementById('add-new-tag-section');
        currentTagsContainer = document.getElementById('current-tags-container');

        setupEventListeners();
        console.log('[gallery-tags] Initialized');
    }

    function setupEventListeners() {
        // Search functionality with debounce
        let searchTimeout;
        if (tagSearch) {
            tagSearch.addEventListener('input', () => {
                clearTimeout(searchTimeout);
                const query = tagSearch.value.trim();

                searchTimeout = setTimeout(async () => {
                    const presets = await loadTagPresets(query);

                    if (query.length > 0) {
                        const hasExactMatch = presets.some(cat =>
                            cat.tags.some(t => t.name.toLowerCase() === query.toLowerCase())
                        );

                        if (!hasExactMatch) {
                            const newTagName = document.getElementById('new-tag-name');
                            if (newTagName) newTagName.textContent = query;
                            if (addNewTagSection) addNewTagSection.classList.remove('hidden');
                            selectedCategoryId = null;
                            document.querySelectorAll('.category-btn').forEach(btn => btn.classList.remove('selected'));
                            const addBtn = document.getElementById('add-new-tag-btn');
                            if (addBtn) addBtn.disabled = true;
                        } else {
                            if (addNewTagSection) addNewTagSection.classList.add('hidden');
                        }
                    } else if (addNewTagSection) {
                        addNewTagSection.classList.add('hidden');
                    }
                }, 300);
            });
        }
    }

    // Open tag editor - uses window.currentLightboxPhotoId if no photoId provided
    window.openTagEditor = async function(photoId) {
        // Use provided photoId or fall back to global currentLightboxPhotoId
        const targetPhotoId = photoId || window.currentLightboxPhotoId;
        if (!targetPhotoId) {
            console.error('[gallery-tags] No photo ID available for tag editor');
            return;
        }

        currentEditingPhotoId = targetPhotoId;
        editingTags = [];
        originalTags = [];
        selectedCategoryId = null;

        // Get current tags from item API (Phase 5: polymorphic items)
        try {
            const resp = await fetch(`${getBaseUrl()}/api/items/${currentEditingPhotoId}`);
            if (resp.ok) {
                const photo = await resp.json();
                originalTags = (photo.tags || []).map(t => ({
                    id: t.id,
                    name: t.tag || t.name,
                    category_id: t.category_id,
                    color: t.color
                }));
                editingTags = originalTags.map(t => ({ ...t }));
            }
        } catch (e) {
            console.error('Failed to load photo tags:', e);
        }

        updateCurrentTagsDisplay();
        
        if (tagEditorPanel) {
            tagEditorPanel.classList.add('open');
        }
        if (lightbox) {
            lightbox.classList.add('panel-open');
        }
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('tag-editor', window.closeTagEditor);
        }
        
        await loadCategories();
        await loadTagPresets();
        
        if (tagSearch) {
            tagSearch.value = '';
            tagSearch.focus();
        }
        if (addNewTagSection) {
            addNewTagSection.classList.add('hidden');
        }
    };

    // Close tag editor
    window.closeTagEditor = function() {
        // Unregister from BackButtonManager first (skipHistoryBack = true for button clicks)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('tag-editor', true);
        }
        
        if (tagEditorPanel) {
            tagEditorPanel.classList.remove('open');
        }
        if (lightbox) {
            lightbox.classList.remove('panel-open');
        }
        selectedCategoryId = null;
        editingTags = [];
        originalTags = [];
        currentEditingPhotoId = null;
    };

    // Load tag categories
    async function loadCategories() {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/tag-categories`);
            if (!resp.ok) return;
            categories = await resp.json();
            renderCategoryButtons();
        } catch (err) {
            console.error('Failed to load categories:', err);
        }
    }

    // Load tag presets
    async function loadTagPresets(search = "") {
        try {
            const url = search 
                ? `${getBaseUrl()}/api/tag-presets?search=${encodeURIComponent(search)}`
                : `${getBaseUrl()}/api/tag-presets`;
            const resp = await fetch(url);
            if (!resp.ok) return [];
            
            const data = await resp.json();
            renderPresets(data);
            return data;
        } catch (err) {
            console.error('Failed to load tag presets:', err);
            return [];
        }
    }

    // Render preset tags by category
    function renderPresets(presetsData) {
        if (!presetsContainer) return;
        
        if (!presetsData || presetsData.length === 0) {
            presetsContainer.innerHTML = '<p class="no-presets">No matching tags found</p>';
            return;
        }

        presetsContainer.innerHTML = presetsData.map(category => `
            <div class="preset-category">
                <h4 style="--cat-color: ${category.color}">${escapeHtml(category.name)}</h4>
                <div class="preset-tags">
                    ${(category.tags || []).map(tag => {
                        const isSelected = editingTags.some(t => t.name.toLowerCase() === tag.name.toLowerCase());
                        return `
                            <button class="preset-tag ${isSelected ? 'selected' : ''}"
                                    style="--tag-color: ${category.color}"
                                    data-tag-name="${escapeHtml(tag.name)}"
                                    data-category-id="${category.id}"
                                    data-color="${category.color}">
                                ${escapeHtml(tag.name)}
                            </button>
                        `;
                    }).join('')}
                </div>
            </div>
        `).join('');

        // Add click handlers
        presetsContainer.querySelectorAll('.preset-tag').forEach(btn => {
            btn.onclick = () => toggleTag(btn.dataset.tagName, parseInt(btn.dataset.categoryId), btn.dataset.color);
        });
    }

    // Render category buttons for new tags
    function renderCategoryButtons() {
        const container = document.getElementById('category-buttons');
        if (!container) return;

        container.innerHTML = categories.map(cat => `
            <button class="category-btn" style="--cat-color: ${cat.color}" data-category-id="${cat.id}">
                ${escapeHtml(cat.name)}
            </button>
        `).join('');

        container.querySelectorAll('.category-btn').forEach(btn => {
            btn.onclick = () => selectCategory(parseInt(btn.dataset.categoryId));
        });
    }

    // Select category for new tag
    window.selectCategory = function(categoryId) {
        selectedCategoryId = categoryId;
        document.querySelectorAll('.category-btn').forEach(btn => {
            btn.classList.toggle('selected', parseInt(btn.dataset.categoryId) === categoryId);
        });
        const addBtn = document.getElementById('add-new-tag-btn');
        if (addBtn) addBtn.disabled = false;
    };

    // Toggle tag selection
    window.toggleTag = function(tagName, categoryId, color) {
        const index = editingTags.findIndex(t => t.name.toLowerCase() === tagName.toLowerCase());

        if (index === -1) {
            editingTags.push({ name: tagName, category_id: categoryId, color: color });
        } else {
            editingTags.splice(index, 1);
        }

        updateCurrentTagsDisplay();
        updatePresetButtons();
    };

    // Remove tag from editing list
    window.removeTagFromEditing = function(tagName) {
        const index = editingTags.findIndex(t => t.name.toLowerCase() === tagName.toLowerCase());
        if (index !== -1) {
            editingTags.splice(index, 1);
            updateCurrentTagsDisplay();
            updatePresetButtons();
        }
    };

    // Update current tags display
    function updateCurrentTagsDisplay() {
        if (!currentTagsContainer) return;

        if (editingTags.length === 0) {
            currentTagsContainer.innerHTML = '<span class="no-tags-hint">No tags selected</span>';
        } else {
            currentTagsContainer.innerHTML = editingTags.map(tag => `
                <span class="selected-tag" style="--tag-color: ${tag.color || '#6b7280'}">
                    ${escapeHtml(tag.name)}
                    <button class="tag-remove" onclick="window.removeTagFromEditing('${escapeHtml(tag.name)}')">&times;</button>
                </span>
            `).join('');
        }
    }

    // Update preset button states
    function updatePresetButtons() {
        if (!presetsContainer) return;
        presetsContainer.querySelectorAll('.preset-tag').forEach(btn => {
            const tagName = btn.dataset.tagName;
            const isSelected = editingTags.some(t => t.name.toLowerCase() === tagName.toLowerCase());
            btn.classList.toggle('selected', isSelected);
        });
    }

    // Save tag changes - add/remove individual tags like in v0.8.5
    window.saveTagChanges = async function() {
        if (!currentEditingPhotoId) return;

        const saveBtn = document.getElementById('save-tags-btn');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        try {
            // Calculate differences
            const toAdd = editingTags.filter(et =>
                !originalTags.some(ot => ot.name.toLowerCase() === et.name.toLowerCase())
            );

            const toRemove = originalTags.filter(ot =>
                !editingTags.some(et => et.name.toLowerCase() === ot.name.toLowerCase())
            );

            // Remove tags
            for (const tag of toRemove) {
                if (tag.id) {
                    await csrfFetch(`${getBaseUrl()}/api/items/${currentEditingPhotoId}/tag/${tag.id}`, { 
                        method: 'DELETE' 
                    });
                }
            }

            // Add new tags
            for (const tag of toAdd) {
                await csrfFetch(`${getBaseUrl()}/api/items/${currentEditingPhotoId}/tag`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ tag: tag.name, category_id: tag.category_id })
                });
            }

            window.closeTagEditor();

            // Refresh lightbox to show updated tags
            if (typeof window.reloadCurrentPhoto === 'function') {
                window.reloadCurrentPhoto();
            }
        } catch (err) {
            console.error('Failed to save tags:', err);
            alert('Failed to save tags: ' + err.message);
        } finally {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Changes';
            }
        }
    };

    // Add new tag to library
    window.addNewTag = async function() {
        if (!tagSearch || !selectedCategoryId) return;
        
        const tagName = tagSearch.value.trim();
        if (!tagName) return;

        if (editingTags.some(t => t.name.toLowerCase() === tagName.toLowerCase())) {
            alert('This tag is already selected');
            return;
        }

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/tag-presets`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: tagName, category_id: selectedCategoryId })
            });

            if (!resp.ok) throw new Error('Failed to add tag');

            const category = categories.find(c => c.id === selectedCategoryId);
            editingTags.push({
                name: tagName.toLowerCase(),
                category_id: selectedCategoryId,
                color: category?.color || '#6b7280'
            });

            updateCurrentTagsDisplay();
            tagSearch.value = '';
            if (addNewTagSection) addNewTagSection.classList.add('hidden');
            await loadTagPresets();
        } catch (err) {
            console.error('Failed to add tag:', err);
            alert('Failed to add tag: ' + err.message);
        }
    };

    // Request AI analysis
    window.requestAIAnalysis = async function() {
        if (!currentEditingPhotoId) return;

        const btn = document.getElementById('request-ai-btn');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = 'Analyzing...';
        }

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/items/${currentEditingPhotoId}/ai-tags`, {
                method: 'POST'
            });

            if (!resp.ok) throw new Error('Analysis failed');

            const data = await resp.json();
            if (data.status === 'ok') {
                window.closeTagEditor();
                // Refresh lightbox to show new tags
                if (typeof window.loadPhoto === 'function') {
                    await window.loadPhoto(currentEditingPhotoId);
                }
            } else {
                throw new Error(data.message || 'Analysis failed');
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

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-tags.js] Loaded');
})();
