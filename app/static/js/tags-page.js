/**
 * Tag Management Page - per-category CRUD + implications
 */

const BASE_URL = (() => {
    const link = document.querySelector('link[rel="stylesheet"]');
    if (link) {
        const href = link.getAttribute('href') || '';
        return href.replace('/static/style.css', '');
    }
    return '';
})();

function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
}

async function csrfFetch(url, options = {}) {
    const headers = options.headers || {};
    const method = (options.method || 'GET').toUpperCase();
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
        headers['X-CSRF-Token'] = getCsrfToken();
        if (!headers['Content-Type'] && options.body && typeof options.body === 'string') {
            headers['Content-Type'] = 'application/json';
        }
    }
    return fetch(BASE_URL + url, { ...options, headers });
}

function showStatus(message, isError = false) {
    showToast(message, isError);
}

// =============================================================================
// State
// =============================================================================

let selectedTagId = null;
let tagDetail = null;
let searchQuery = '';
let catState = {}; // { [catId]: { tags: [], offset: 0, hasMore: true, isLoading: false } }
let tagDescriptionEditor = null;
let tagDescMode = 'preview';

// =============================================================================
// Load categories & init
// =============================================================================

function initCategories() {
    if (!window.__categories || !window.__categories.length) {
        const opts = document.querySelectorAll('#new-tag-category option');
        window.__categories = Array.from(opts).map(o => ({
            id: parseInt(o.value, 10),
            name: o.textContent,
            color: o.getAttribute('data-color') || '#888'
        }));
    }

    for (const cat of window.__categories) {
        catState[cat.id] = { tags: [], offset: 0, hasMore: true, isLoading: false };
        loadCategoryTags(cat.id);
    }
}

async function loadCategoryTags(catId) {
    const state = catState[catId];
    if (!state || state.isLoading || !state.hasMore) return;

    state.isLoading = true;
    const q = encodeURIComponent(searchQuery);
    try {
        const resp = await csrfFetch(`/api/tags?category_id=${catId}&q=${q}&limit=50&offset=${state.offset}`);
        if (!resp.ok) throw new Error('Failed to load tags');
        const data = await resp.json();

        if (state.offset === 0) {
            state.tags = data.items;
        } else {
            state.tags = state.tags.concat(data.items);
        }
        state.offset += data.items.length;
        state.hasMore = data.items.length === 50 && state.offset < data.total;
        renderTagList();
    } catch (err) {
        showStatus(err.message, true);
    } finally {
        state.isLoading = false;
    }
}

// =============================================================================
// Render
// =============================================================================

function renderTagList() {
    const container = document.getElementById('tag-list');
    const cats = window.__categories || [];
    const hasAnyTag = cats.some(c => catState[c.id]?.tags.length);

    if (!hasAnyTag) {
        container.innerHTML = `<div class="empty-state">No tags found</div>`;
        return;
    }

    const html = cats.map(cat => {
        const state = catState[cat.id] || { tags: [] };
        if (!state.tags.length && !state.hasMore) return '';

        const itemsHtml = state.tags.map(tag => {
            const activeClass = tag.id === selectedTagId ? 'active' : '';
            return `
            <div class="tag-list-item ${activeClass}" data-id="${tag.id}" onclick="selectTag(${tag.id})" style="--tag-color:${cat.color||'#888'}">
                <span class="tag-name">${escapeHtml(tag.display_name || tag.name)}</span>
                <span class="tag-meta">${tag.usage_count || 0}</span>
            </div>`;
        }).join('');

        const loadMore = state.hasMore
            ? `<div class="load-more"><button class="btn btn-small btn-secondary" onclick="loadCategoryTags(${cat.id})">Load more</button></div>`
            : '';

        return `
        <div class="tag-category-block">
            <div class="tag-category-header" style="color:${cat.color||'#888'}">${escapeHtml(cat.name)}</div>
            <div class="tag-category-items">
                ${itemsHtml || '<span style="font-size:0.75rem;color:var(--text-muted);">No tags</span>'}
            </div>
            ${loadMore}
        </div>`;
    }).join('');

    container.innerHTML = html;
}

// =============================================================================
// Tag Detail
// =============================================================================

async function selectTag(tagId) {
    selectedTagId = tagId;
    renderTagList();
    const panel = document.getElementById('detail-panel');
    panel.className = 'detail-panel';
    panel.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:2rem 0;">Loading...</p>';

    try {
        const [tagResp, implResp, relatedResp] = await Promise.all([
            csrfFetch(`/api/tags/${tagId}`),
            csrfFetch(`/api/tags/${tagId}/implications`),
            csrfFetch(`/api/tags/${tagId}/related`),
        ]);
        if (!tagResp.ok) throw new Error('Failed to load tag details');
        const tag = await tagResp.json();
        const implications = implResp.ok ? await implResp.json() : {implies: [], implied_by: []};
        const related = relatedResp.ok ? await relatedResp.json() : {tags: []};
        tagDetail = {
            tag: tag,
            implications: implications,
            related_tags: related.tags || []
        };
        renderDetail();
    } catch (err) {
        panel.innerHTML = `<p style="color:#ef4444;text-align:center;padding:2rem 0;">${escapeHtml(err.message)}</p>`;
    }
}

function renderDetail() {
    const panel = document.getElementById('detail-panel');
    if (!tagDetail) return;
    const tag = tagDetail.tag;
    const color = tag.category_color || '#888';
    const isAdmin = window.__isAdmin === true;

    const implies = tagDetail.implications.implies || [];
    const impliedBy = tagDetail.implications.implied_by || [];
    const related = tagDetail.related_tags || [];

    const propertiesSection = isAdmin ? `
        <div class="detail-section">
            <h3>Properties</h3>
            <div class="inline-form">
                <input type="text" id="edit-name" value="${escapeHtml(tag.name)}" placeholder="Name">
                <input type="text" id="edit-display" value="${escapeHtml(tag.display_name || '')}" placeholder="Display name">
                <select id="edit-category">
                    ${window.__categories?.map(c => `<option value="${c.id}" ${c.id === tag.category_id ? 'selected' : ''}>${escapeHtml(c.name)}</option>`).join('') || ''}
                </select>
                <div class="form-actions">
                    <button class="btn btn-small btn-secondary" onclick="cancelEdit()">Cancel</button>
                    <button class="btn btn-small" onclick="saveEdit(${tag.id})">Save</button>
                </div>
            </div>
        </div>` : '';

    const descValue = tag.description || '';
    const descriptionSection = isAdmin ? `
        <div class="detail-section">
            <h3>Description</h3>
            <div class="description-editor-wrapper preview-mode" id="tag-desc-wrapper">
                <textarea id="tag-description-editor" style="display:none;"></textarea>
                <div id="tag-description-preview" class="markdown-preview">${renderMarkdown(descValue) || '<em class="no-description">No description</em>'}</div>
            </div>
            <div class="desc-toolbar" style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.5rem;">
                <button class="btn btn-small btn-secondary" id="tag-desc-toggle-btn" onclick="toggleTagDescMode()">Edit</button>
                <button class="btn btn-small" onclick="saveTagDescription(${tag.id})">Save</button>
            </div>
        </div>` : `
        <div class="detail-section">
            <h3>Description</h3>
            <div class="markdown-preview" id="tag-description-preview">
                ${renderMarkdown(descValue) || '<em class="no-description" style="color:var(--text-muted);">No description</em>'}
            </div>
        </div>`;

    const impliesItems = implies.map(t => `
        <div class="tag-list-item" style="--tag-color:${t.category_color||'#888'}" onclick="selectTag(${t.id})">
            <span class="tag-name">${escapeHtml(t.display_name || t.name)}</span>
            ${isAdmin ? `<span class="tag-actions" onclick="event.stopPropagation()">
                <button title="Remove implication" onclick="removeImplication(${tag.id}, ${t.id})">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                </button>
            </span>` : ''}
        </div>
    `).join('');

    const addImplSection = isAdmin ? `
        <div class="add-impl">
            <input type="text" id="implies-search" placeholder="Add implication..." autocomplete="off">
            <button class="btn btn-small" onclick="addImplication(${tag.id})">Add</button>
        </div>
        <div id="implies-search-results" style="margin-top:0.25rem;"></div>` : '';

    const deleteSection = isAdmin ? `
        <div class="detail-section" style="margin-top:1.5rem;padding-top:1rem;border-top:1px solid var(--border);">
            <button class="btn btn-small btn-danger" onclick="deleteTag(${tag.id})">Delete tag</button>
        </div>` : '';

    panel.innerHTML = `
        <div class="detail-header">
            <h2>${escapeHtml(tag.display_name || tag.name)}</h2>
            <span class="tag-chip" style="--tag-color:${color}">${escapeHtml(tag.category_name || 'General')}</span>
        </div>

        ${propertiesSection}
        ${descriptionSection}

        <div class="detail-section">
            <h3>Implies (${implies.length})</h3>
            <div class="impl-list" id="implies-list">
                ${impliesItems || '<p style="font-size:0.8125rem;color:var(--text-muted);margin:0;">No outgoing implications</p>'}
            </div>
            ${addImplSection}
        </div>

        <div class="detail-section">
            <h3>Implied By (${impliedBy.length})</h3>
            <div class="impl-list">
                ${impliedBy.map(t => `
                    <div class="tag-list-item" style="--tag-color:${t.category_color||'#888'}" onclick="selectTag(${t.id})">
                        <span class="tag-name">${escapeHtml(t.display_name || t.name)}</span>
                    </div>
                `).join('') || '<p style="font-size:0.8125rem;color:var(--text-muted);margin:0;">No incoming implications</p>'}
            </div>
        </div>

        <div class="detail-section">
            <h3>Related Tags</h3>
            <div class="related-list">
                ${related.map(t => `
                    <span class="related-chip" style="cursor:pointer;" onclick="selectTag(${t.id})">${escapeHtml(t.display_name || t.name)}</span>
                `).join('') || '<span style="font-size:0.8125rem;color:var(--text-muted);">No data yet</span>'}
            </div>
        </div>

        ${deleteSection}
    `;
    addCopyButtonsToCodeBlocks(panel);

    if (isAdmin) {
        initTagDescriptionEditor(descValue);
    }

    const searchInput = document.getElementById('implies-search');
    if (searchInput) {
        let debounce;
        searchInput.addEventListener('input', (e) => {
            clearTimeout(debounce);
            debounce = setTimeout(() => searchForImplication(e.target.value), 200);
        });
    }
}

function cancelEdit() {
    if (tagDetail) renderDetail();
}

async function saveEdit(tagId) {
    const name = document.getElementById('edit-name').value.trim();
    const displayName = document.getElementById('edit-display').value.trim();
    const categoryId = parseInt(document.getElementById('edit-category').value, 10);

    try {
        const resp = await csrfFetch(`/api/tags/${tagId}`, {
            method: 'PUT',
            body: JSON.stringify({ name, display_name: displayName || null, category_id: categoryId })
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to update tag');
        }
        showStatus('Tag updated');
        await refreshAllCategories();
        await selectTag(tagId);
    } catch (err) {
        showStatus(err.message, true);
    }
}

async function refreshAllCategories() {
    for (const cat of window.__categories || []) {
        catState[cat.id] = { tags: [], offset: 0, hasMore: true, isLoading: false };
        await loadCategoryTags(cat.id);
    }
}

async function deleteTag(tagId) {
    const tag = tagDetail?.tag || (() => {
        for (const cat of window.__categories || []) {
            const t = catState[cat.id]?.tags.find(x => x.id === tagId);
            if (t) return t;
        }
        return null;
    })();
    const name = tag ? (tag.display_name || tag.name) : 'this tag';
    if (!confirm(`Delete "${name}"?\n\nThis will remove the tag from all items.`)) return;

    try {
        const resp = await csrfFetch(`/api/tags/${tagId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error('Failed to delete tag');
        showStatus('Tag deleted');
        selectedTagId = null;
        tagDetail = null;
        document.getElementById('detail-panel').className = 'detail-panel empty';
        document.getElementById('detail-panel').innerHTML = '<p>Select a tag to view details</p>';
        await refreshAllCategories();
    } catch (err) {
        showStatus(err.message, true);
    }
}

// =============================================================================
// Description Editor
// =============================================================================

function renderMarkdown(text) {
    if (!text) return '';
    if (window.marked && window.marked.parse) {
        return window.marked.parse(text);
    }
    // Fallback to simple renderer
    let html = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
    html = html.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
    html = html.replace(/\n/g, '<br>');
    return html;
}

function initTagDescriptionEditor(initialValue) {
    const textarea = document.getElementById('tag-description-editor');
    if (!textarea) return;

    if (tagDescriptionEditor) {
        tagDescriptionEditor.toTextArea();
        tagDescriptionEditor = null;
    }

    textarea.value = initialValue || '';
    tagDescMode = 'preview';

    const wrapper = document.getElementById('tag-desc-wrapper');
    const previewEl = document.getElementById('tag-description-preview');
    const toggleBtn = document.getElementById('tag-desc-toggle-btn');

    wrapper?.classList.add('preview-mode');
    wrapper?.classList.remove('edit-mode');
    if (toggleBtn) toggleBtn.textContent = 'Edit';
    updateTagDescriptionPreview();
    previewEl?.classList.remove('hidden');

    tagDescriptionEditor = new EasyMDE({
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
        maxHeight: '200px',
        placeholder: 'Enter tag description...',
        initialValue: initialValue || ''
    });
}

function updateTagDescriptionPreview() {
    const previewEl = document.getElementById('tag-description-preview');
    if (!previewEl || !tagDescriptionEditor) return;

    const markdown = tagDescriptionEditor.value();
    if (!markdown || !markdown.trim()) {
        previewEl.innerHTML = '<em class="no-description">No description</em>';
        return;
    }
    previewEl.innerHTML = renderMarkdown(markdown);
    addCopyButtonsToCodeBlocks(previewEl);
}

// Add copy buttons to code blocks in preview
function addCopyButtonsToCodeBlocks(container) {
    const codeBlocks = container.querySelectorAll('pre code');
    codeBlocks.forEach((codeBlock) => {
        const pre = codeBlock.parentElement;
        if (pre.querySelector('.code-copy-btn')) return;

        const copyBtn = document.createElement('button');
        copyBtn.className = 'code-copy-btn';
        copyBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
        copyBtn.title = 'Copy code';
        copyBtn.onclick = () => copyCodeToClipboard(codeBlock, copyBtn);

        pre.style.position = 'relative';
        pre.appendChild(copyBtn);
    });
}

async function copyCodeToClipboard(codeBlock, button) {
    const code = codeBlock.textContent;
    const copyFn = window.copyToClipboard || async function(text) {
        if (navigator.clipboard && window.isSecureContext) {
            try {
                await navigator.clipboard.writeText(text);
                return true;
            } catch (err) {
                console.warn('Clipboard API failed:', err);
            }
        }
        return false;
    };
    const copied = await copyFn(code);
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

function toggleTagDescMode() {
    const wrapper = document.getElementById('tag-desc-wrapper');
    const toggleBtn = document.getElementById('tag-desc-toggle-btn');
    const previewEl = document.getElementById('tag-description-preview');

    if (tagDescMode === 'edit') {
        wrapper?.classList.add('preview-mode');
        wrapper?.classList.remove('edit-mode');
        if (toggleBtn) toggleBtn.textContent = 'Edit';
        updateTagDescriptionPreview();
        previewEl?.classList.remove('hidden');
        tagDescMode = 'preview';
    } else {
        wrapper?.classList.add('edit-mode');
        wrapper?.classList.remove('preview-mode');
        if (toggleBtn) toggleBtn.textContent = 'Preview';
        previewEl?.classList.add('hidden');
        tagDescMode = 'edit';
    }
}

async function saveTagDescription(tagId) {
    const desc = tagDescriptionEditor ? tagDescriptionEditor.value() : '';

    try {
        const resp = await csrfFetch(`/api/tags/${tagId}`, {
            method: 'PUT',
            body: JSON.stringify({ description: desc })
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to save description');
        }
        showStatus('Description saved');
        // Refresh tag detail to show updated description
        await selectTag(tagId);
    } catch (err) {
        showStatus(err.message, true);
    }
}

// =============================================================================
// Implications
// =============================================================================

let implicationSearchResults = [];

async function searchForImplication(query) {
    const container = document.getElementById('implies-search-results');
    if (!query || query.length < 2) {
        if (container) container.innerHTML = '';
        return;
    }
    try {
        const resp = await csrfFetch(`/api/tags/search?q=${encodeURIComponent(query)}&limit=50`);
        if (!resp.ok) return;
        const payload = await resp.json();
        const data = payload.tags || [];
        implicationSearchResults = data;
        if (!container) return;
        if (!data.length) {
            container.innerHTML = '<span style="font-size:0.75rem;color:var(--text-muted);">No matches</span>';
            return;
        }
        container.innerHTML = data.map(t => `
            <button class="btn btn-small btn-secondary" style="margin:0.125rem;"
                onclick="pickImplication(${t.id})">
                <span style="color:${t.category_color||'#888'}">●</span>
                ${escapeHtml(t.display_name || t.name)}
            </button>
        `).join('');
    } catch {
        if (container) container.innerHTML = '';
    }
}

function pickImplication(tagId) {
    const input = document.getElementById('implies-search');
    if (input) input.value = '';
    const container = document.getElementById('implies-search-results');
    if (container) container.innerHTML = '';
    addImplication(selectedTagId, tagId);
}

async function addImplication(tagId, impliesTagId) {
    if (!impliesTagId) {
        const input = document.getElementById('implies-search');
        const name = input?.value.trim();
        if (!name) return;
        const match = implicationSearchResults.find(t =>
            t.name.toLowerCase() === name.toLowerCase() ||
            (t.display_name && t.display_name.toLowerCase() === name.toLowerCase())
        );
        if (match) {
            impliesTagId = match.id;
        } else {
            showStatus('Select a tag from suggestions', true);
            return;
        }
    }

    try {
        const resp = await csrfFetch(`/api/tags/${tagId}/implications`, {
            method: 'POST',
            body: JSON.stringify({ implies_tag_id: impliesTagId })
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to add implication');
        }
        showStatus('Implication added');
        await selectTag(tagId);
        await refreshAllCategories();
    } catch (err) {
        showStatus(err.message, true);
    }
}

async function removeImplication(tagId, impliesTagId) {
    try {
        const resp = await csrfFetch(`/api/tags/${tagId}/implications/${impliesTagId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error('Failed to remove implication');
        showStatus('Implication removed');
        await selectTag(tagId);
        await refreshAllCategories();
    } catch (err) {
        showStatus(err.message, true);
    }
}

// =============================================================================
// Create Tag
// =============================================================================

function openModal() {
    document.getElementById('new-tag-modal').classList.add('open');
    document.getElementById('new-tag-name').focus();
}

function closeModal() {
    document.getElementById('new-tag-modal').classList.remove('open');
    document.getElementById('new-tag-name').value = '';
    document.getElementById('new-tag-display').value = '';
}

async function createTag() {
    const name = document.getElementById('new-tag-name').value.trim();
    const displayName = document.getElementById('new-tag-display').value.trim();
    const categoryId = parseInt(document.getElementById('new-tag-category').value, 10);

    if (!name) {
        showStatus('Name is required', true);
        return;
    }

    try {
        const resp = await csrfFetch('/api/tags', {
            method: 'POST',
            body: JSON.stringify({ name, display_name: displayName || null, category_id: categoryId })
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to create tag');
        }
        const data = await resp.json();
        showStatus('Tag created');
        closeModal();
        await refreshAllCategories();
        if (data.tag) selectTag(data.tag.id);
    } catch (err) {
        showStatus(err.message, true);
    }
}

// =============================================================================
// Categories
// =============================================================================

function openCategoriesModal() {
    document.getElementById('categories-modal').classList.add('open');
    loadCategories();
}

function closeCategoriesModal() {
    document.getElementById('categories-modal').classList.remove('open');
}

async function loadCategories() {
    try {
        const resp = await csrfFetch('/api/tag-categories');
        if (!resp.ok) throw new Error('Failed to load categories');
        const data = await resp.json();
        renderCategoriesList(data.categories);
        window.__categories = (data.categories || []).map(c => ({ id: c.id, name: c.name, color: c.color }));
        updateCategorySelectOptions(data.categories);
    } catch (err) {
        showStatus(err.message, true);
    }
}

function updateCategorySelectOptions(categories) {
    const select = document.getElementById('new-tag-category');
    const editSelect = document.getElementById('edit-category');
    const opts = (categories || []).map(c => `<option value="${c.id}" data-color="${escapeHtml(c.color)}">${escapeHtml(c.name)}</option>`).join('');
    if (select) select.innerHTML = opts;
    if (editSelect) editSelect.innerHTML = opts;
}

function renderCategoriesList(categories) {
    const container = document.getElementById('categories-list');
    if (!categories || !categories.length) {
        container.innerHTML = '<p style="color:var(--text-muted);font-size:0.875rem;">No categories</p>';
        return;
    }
    container.innerHTML = categories.map(cat => `
        <div class="cat-row" data-cat-id="${cat.id}">
            <input type="color" value="${escapeHtml(cat.color)}" onchange="updateCategory(${cat.id})">
            <input type="text" class="cat-name-input" value="${escapeHtml(cat.name)}" onchange="updateCategory(${cat.id})">
            <button class="btn btn-small btn-danger" onclick="deleteCategory(${cat.id})">Delete</button>
        </div>
    `).join('');
}

async function createCategory() {
    const name = document.getElementById('new-cat-name').value.trim();
    const color = document.getElementById('new-cat-color').value;
    if (!name) {
        showStatus('Category name is required', true);
        return;
    }
    try {
        const resp = await csrfFetch('/api/tag-categories', {
            method: 'POST',
            body: JSON.stringify({ name, color })
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to create category');
        }
        showStatus('Category created');
        document.getElementById('new-cat-name').value = '';
        await loadCategories();
        await refreshAllCategories();
    } catch (err) {
        showStatus(err.message, true);
    }
}

async function updateCategory(catId) {
    const row = document.querySelector(`.cat-row[data-cat-id="${catId}"]`);
    if (!row) return;
    const name = row.querySelector('.cat-name-input').value.trim();
    const color = row.querySelector('input[type="color"]').value;
    try {
        const resp = await csrfFetch(`/api/tag-categories/${catId}`, {
            method: 'PUT',
            body: JSON.stringify({ name, color })
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to update category');
        }
        showStatus('Category updated');
        await refreshAllCategories();
    } catch (err) {
        showStatus(err.message, true);
    }
}

async function deleteCategory(catId) {
    const row = document.querySelector(`.cat-row[data-cat-id="${catId}"]`);
    const name = row ? row.querySelector('.cat-name-input').value : 'this category';
    if (!confirm(`Delete "${name}"?\n\nYou can only delete empty categories.`)) return;

    try {
        const resp = await csrfFetch(`/api/tag-categories/${catId}`, { method: 'DELETE' });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'Failed to delete category');
        }
        showStatus('Category deleted');
        await loadCategories();
        await refreshAllCategories();
    } catch (err) {
        showStatus(err.message, true);
    }
}

// =============================================================================
// Utils
// =============================================================================

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// =============================================================================
// Init
// =============================================================================

document.getElementById('tag-search').addEventListener('input', (e) => {
    searchQuery = e.target.value.trim();
    for (const cat of window.__categories || []) {
        catState[cat.id] = { tags: [], offset: 0, hasMore: true, isLoading: false };
        loadCategoryTags(cat.id);
    }
});

const newTagBtn = document.getElementById('new-tag-btn');
if (newTagBtn) newTagBtn.addEventListener('click', openModal);
const manageCatsBtn = document.getElementById('manage-cats-btn');
if (manageCatsBtn) manageCatsBtn.addEventListener('click', openCategoriesModal);

const sanitizeTagsBtn = document.getElementById('sanitize-tags-btn');
if (sanitizeTagsBtn) {
    sanitizeTagsBtn.addEventListener('click', async () => {
        if (!confirm('This will recalculate all implied tags across all items based on current tag implications. Continue?')) return;
        sanitizeTagsBtn.disabled = true;
        sanitizeTagsBtn.textContent = 'Sanitizing...';
        try {
            const response = await csrfFetch('/api/admin/tags/sanitize', {
                method: 'POST'
            });
            const data = await response.json();
            if (response.ok) {
                alert(`Sanitize complete! Processed ${data.items_processed} items. Added ${data.tags_added}, removed ${data.tags_removed}.`);
            } else {
                alert(data.detail || 'Sanitize failed.');
            }
        } catch (e) {
            alert('Network error during sanitize.');
        } finally {
            sanitizeTagsBtn.disabled = false;
            sanitizeTagsBtn.innerHTML = `
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
                    <polyline points="22,6 12,13 2,6"/>
                </svg>
                Sanitize
            `;
        }
    });
}

const newTagModal = document.getElementById('new-tag-modal');
if (newTagModal) newTagModal.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
});

const categoriesModal = document.getElementById('categories-modal');
if (categoriesModal) categoriesModal.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeCategoriesModal();
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeModal();
        closeCategoriesModal();
    }
});

initCategories();
