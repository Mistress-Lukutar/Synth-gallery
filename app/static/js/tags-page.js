/**
 * Tag Management Page - CRUD + implications
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
    const el = document.getElementById('status-message');
    el.textContent = message;
    el.className = 'status-message ' + (isError ? 'error' : 'success');
    setTimeout(() => { el.className = 'status-message'; }, 5000);
}

// =============================================================================
// State
// =============================================================================

let tagsData = [];
let selectedTagId = null;
let tagDetail = null;
let currentQuery = '';
let currentOffset = 0;
const LIMIT = 50;
let isLoading = false;
let hasMore = true;

// =============================================================================
// Tag List
// =============================================================================

async function loadTags(reset = false) {
    if (isLoading) return;
    isLoading = true;

    if (reset) {
        currentOffset = 0;
        hasMore = true;
        tagsData = [];
    }
    if (!hasMore) { isLoading = false; return; }

    const q = encodeURIComponent(currentQuery);
    try {
        const resp = await csrfFetch(`/api/tags?q=${q}&limit=${LIMIT}&offset=${currentOffset}`);
        if (!resp.ok) throw new Error('Failed to load tags');
        const data = await resp.json();

        if (reset) {
            tagsData = data.items;
        } else {
            tagsData = tagsData.concat(data.items);
        }
        currentOffset += data.items.length;
        hasMore = data.items.length === LIMIT && currentOffset < data.total;
        renderTagList();
        updatePaginationInfo(data.total);
    } catch (err) {
        showStatus(err.message, true);
    } finally {
        isLoading = false;
    }
}

function updatePaginationInfo(total) {
    const el = document.getElementById('pagination-info');
    if (!total) {
        el.textContent = '';
        return;
    }
    el.textContent = `Showing ${tagsData.length} of ${total} tags`;
}

function renderTagList() {
    const container = document.getElementById('tag-list');
    if (!tagsData.length) {
        container.innerHTML = `<div class="empty-state">No tags found</div>`;
        return;
    }

    // Group by category
    const groups = {};
    for (const tag of tagsData) {
        const catName = tag.category_name || 'General';
        const catColor = tag.category_color || '#888';
        if (!groups[catName]) {
            groups[catName] = { color: catColor, tags: [] };
        }
        groups[catName].tags.push(tag);
    }

    // Preserve category sort order from window.__categories if available
    let categoryOrder = Object.keys(groups);
    if (window.__categories && window.__categories.length) {
        const orderMap = new Map(window.__categories.map((c, i) => [c.name, i]));
        categoryOrder.sort((a, b) => {
            const ia = orderMap.get(a) ?? 999;
            const ib = orderMap.get(b) ?? 999;
            return ia - ib;
        });
    }

    const html = categoryOrder.map(catName => {
        const group = groups[catName];
        const itemsHtml = group.tags.map(tag => {
            const activeClass = tag.id === selectedTagId ? 'active' : '';
            return `
            <div class="tag-list-item ${activeClass}" data-id="${tag.id}" onclick="selectTag(${tag.id})">
                <span class="tag-name">${escapeHtml(tag.display_name || tag.name)}</span>
                <span class="tag-meta">
                    ${tag.usage_count || 0}
                    ${tag.implies_count ? `· →${tag.implies_count}` : ''}
                    ${tag.implied_by_count ? `· ←${tag.implied_by_count}` : ''}
                </span>
                <span class="tag-actions" onclick="event.stopPropagation()">
                    <button title="Edit" onclick="startEditTag(${tag.id})">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                    </button>
                    <button title="Delete" onclick="deleteTag(${tag.id})">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </span>
            </div>`;
        }).join('');

        return `
        <div class="tag-category-block">
            <div class="tag-category-header" style="color:${group.color}">${escapeHtml(catName)}</div>
            <div class="tag-category-items">
                ${itemsHtml}
            </div>
        </div>`;
    }).join('');

    container.innerHTML = html;

    if (hasMore) {
        const loadMore = document.createElement('div');
        loadMore.className = 'load-more';
        loadMore.innerHTML = `<button class="btn btn-small btn-secondary" onclick="loadTags()">Load more</button>`;
        container.appendChild(loadMore);
    }
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

    const implies = tagDetail.implications.implies || [];
    const impliedBy = tagDetail.implications.implied_by || [];
    const related = tagDetail.related_tags || [];

    panel.innerHTML = `
        <div class="detail-header">
            <h2>${escapeHtml(tag.display_name || tag.name)}</h2>
            <span class="tag-chip" style="--tag-color:${color}">${escapeHtml(tag.category_name || 'General')}</span>
        </div>

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
        </div>

        <div class="detail-section">
            <h3>Implies (${implies.length})</h3>
            <div class="impl-list" id="implies-list">
                ${implies.map(t => `
                    <div class="impl-row">
                        <span style="color:${t.category_color||'#888'}">●</span>
                        ${escapeHtml(t.display_name || t.name)}
                        <button onclick="removeImplication(${tag.id}, ${t.id})">×</button>
                    </div>
                `).join('') || '<p style="font-size:0.8125rem;color:var(--text-muted);margin:0;">No outgoing implications</p>'}
            </div>
            <div class="add-impl">
                <input type="text" id="implies-search" placeholder="Add implication..." autocomplete="off">
                <button class="btn btn-small" onclick="addImplication(${tag.id})">Add</button>
            </div>
            <div id="implies-search-results" style="margin-top:0.25rem;"></div>
        </div>

        <div class="detail-section">
            <h3>Implied By (${impliedBy.length})</h3>
            <div class="impl-list">
                ${impliedBy.map(t => `
                    <div class="impl-row">
                        <span style="color:${t.category_color||'#888'}">●</span>
                        ${escapeHtml(t.display_name || t.name)}
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
    `;

    // Wire up implication search
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
        // Refresh list and detail
        await loadTags(true);
        await selectTag(tagId);
    } catch (err) {
        showStatus(err.message, true);
    }
}

function startEditTag(tagId) {
    selectTag(tagId);
    // Focus name field after render
    setTimeout(() => {
        const el = document.getElementById('edit-name');
        if (el) el.focus();
    }, 50);
}

async function deleteTag(tagId) {
    const tag = tagsData.find(t => t.id === tagId);
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
        await loadTags(true);
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
        const resp = await csrfFetch(`/api/tags/search?q=${encodeURIComponent(query)}&limit=8`);
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
        // Try exact match from last search
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
        await loadTags(true);
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
        await loadTags(true);
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
        await loadTags(true);
        if (data.tag) selectTag(data.tag.id);
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
    currentQuery = e.target.value.trim();
    loadTags(true);
});

document.getElementById('new-tag-btn').addEventListener('click', openModal);

document.getElementById('new-tag-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) closeModal();
});

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeModal();
});

// Pass categories to JS for edit form
window.__categories = JSON.parse(document.querySelector('script[data-categories]')?.textContent || '[]');

// Load categories from template into __categories if not already set
if (!window.__categories.length) {
    const opts = document.querySelectorAll('#new-tag-category option');
    window.__categories = Array.from(opts).map(o => ({ id: parseInt(o.value,10), name: o.textContent }));
}

loadTags(true);
