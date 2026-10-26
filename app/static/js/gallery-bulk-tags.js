/**
 * Gallery Bulk Tags - Mass tag editing for selected items
 *
 * Tag entry uses the same token input widget as the item details panel
 * (tag-token-input.js): typed whitespace commits chips, suggestions are
 * picked from the dropdown, Enter on an empty field applies the staged
 * chips to all selected items.
 */

(function() {
    'use strict';

    let modal = null;
    let tagInput = null;
    let resultsContainer = null;
    let commonTagsContainer = null;
    let countLabel = null;
    let bulkTagsBtn = null;

    let currentItemIds = [];
    let currentCommonTags = [];

    function init() {
        modal = document.getElementById('bulk-tags-modal');
        resultsContainer = document.getElementById('bulk-tag-results');
        commonTagsContainer = document.getElementById('bulk-common-tags');
        countLabel = document.getElementById('bulk-tag-count');
        bulkTagsBtn = document.getElementById('bulk-tags-btn');

        if (!modal || !bulkTagsBtn) return;

        const tagInputEl = document.getElementById('bulk-tag-input');
        if (tagInputEl && window.createTagTokenInput) {
            tagInput = window.createTagTokenInput(tagInputEl, {
                fetchSuggestions: fetchTagSuggestions,
                renderSuggestions: renderSuggestionsList,
                resolveNames: resolveTagNames,
                onConfirm: confirmTagInput,
            });
        }

        bulkTagsBtn.addEventListener('click', openModal);

        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeBulkTagsModal();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                closeBulkTagsModal();
            }
        });
    }

    // ========================================================================
    // Tag API (same flow as the item details tag input)
    // ========================================================================

    async function fetchTagSuggestions(query) {
        if (!query) return [];
        try {
            const resp = await fetch(
                `${getBaseUrl()}/api/tags/search?q=${encodeURIComponent(query)}&limit=50`
            );
            if (!resp.ok) return [];
            const data = await resp.json();
            return data.tags || [];
        } catch (e) {
            console.error('Tag search failed:', e);
            return [];
        }
    }

    async function resolveTagNames(names, createMissing = false) {
        const resp = await csrfFetch(`${getBaseUrl()}/api/tags/resolve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ names, create_missing: createMissing }),
        });
        if (!resp.ok) throw new Error(`Resolve failed: HTTP ${resp.status}`);
        const data = await resp.json();
        return data.results || [];
    }

    // Enter on an empty field: apply the staged chips to every selected item.
    // Unknown chips are created in the "general" category first (admin only).
    async function confirmTagInput() {
        if (!currentItemIds.length || !tagInput) return;
        let chips = tagInput.getChips();
        if (chips.length === 0) return;

        const invalid = chips.find(c => c.state === 'invalid');
        if (invalid) {
            showToast(`Invalid tag name: "${invalid.name}"`, true);
            return;
        }

        // Create unknown tags (admins); on 403 keep them staged in the field
        const unknown = chips.filter(c => c.state === 'unknown');
        if (unknown.length > 0) {
            try {
                const results = await resolveTagNames(unknown.map(c => c.name), true);
                tagInput.applyResolveResults(results);
            } catch (e) {
                const denied = e.message && e.message.includes('403');
                showToast(denied
                    ? 'Only admins can create new tags'
                    : 'Failed to create tags', true);
            }
        }

        chips = tagInput.getChips();
        const existingIds = new Set(currentCommonTags.map(t => t.id));
        const toAdd = [];
        for (const chip of chips) {
            if (chip.state === 'known' && chip.id != null
                    && !existingIds.has(chip.id) && !toAdd.includes(chip.id)) {
                toAdd.push(chip.id);
            }
        }

        if (toAdd.length === 0) {
            // Nothing new to apply: drop known chips, keep unknown ones staged
            tagInput.removeChipsWhere(c => c.state === 'known');
            return;
        }

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/items/tags/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    item_ids: currentItemIds,
                    add_tag_ids: toAdd,
                    remove_tag_ids: [],
                }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (data.skipped > 0) {
                showToast(`${data.skipped} item(s) skipped (no permission)`, true);
            }
        } catch (e) {
            console.error('Failed to apply tags:', e);
            showToast('Failed to apply tags', true);
            return;
        }
        tagInput.removeChipsWhere(c => c.state === 'known');
        await loadCommonTags();
        tagInput.focus();
    }

    // ========================================================================
    // Modal
    // ========================================================================

    async function openModal() {
        const photos = Array.from(window.selectedPhotos || []);
        const albums = Array.from(window.selectedAlbums || []);

        if (photos.length === 0 && albums.length === 0) return;

        // Collect all item IDs from selected photos and albums
        const itemIdSet = new Set(photos);

        // Fetch items from selected albums
        if (albums.length > 0) {
            const albumPromises = albums.map(albumId =>
                fetch(`${getBaseUrl()}/api/albums/${albumId}`)
                    .then(r => r.ok ? r.json() : null)
                    .catch(() => null)
            );
            const albumData = await Promise.all(albumPromises);
            for (let i = 0; i < albums.length; i++) {
                const albumId = albums[i];
                const album = albumData[i];
                // Check if album is from search results with filtered items
                const albumEl = document.querySelector(`.gallery-item[data-album-id="${albumId}"]`);
                const matchingItems = albumEl?.dataset.matchingItems;
                if (matchingItems) {
                    // Use only matching items from search results
                    matchingItems.split(',').forEach(id => itemIdSet.add(id));
                } else if (album && album.items) {
                    // Use all album items
                    for (const item of album.items) {
                        itemIdSet.add(item.id);
                    }
                }
            }
        }

        currentItemIds = Array.from(itemIdSet);
        if (currentItemIds.length === 0) {
            showToast('No items to edit tags for', true);
            return;
        }

        countLabel.textContent = currentItemIds.length;
        tagInput?.clear();
        resultsContainer.innerHTML = '';
        modal.classList.remove('hidden');

        await loadCommonTags();
    }

    window.closeBulkTagsModal = function() {
        modal.classList.add('hidden');
        currentItemIds = [];
        currentCommonTags = [];
    };

    async function loadCommonTags() {
        try {
            const resp = await fetch(
                `${getBaseUrl()}/api/items/tags/common?item_ids=${encodeURIComponent(currentItemIds.join(','))}`
            );
            if (resp.ok) {
                const data = await resp.json();
                currentCommonTags = data.tags || [];
                renderCommonTags();
            }
        } catch (e) {
            console.error('Failed to load common tags:', e);
        }
    }

    function renderCommonTags() {
        if (!commonTagsContainer) return;
        if (typeof window.tagEditorRenderChips === 'function') {
            window.tagEditorRenderChips(commonTagsContainer, currentCommonTags, {
                removable: true,
                onRemove: removeTag,
                emptyHint: 'No common tags'
            });
        } else {
            // Fallback inline render
            if (!currentCommonTags.length) {
                commonTagsContainer.innerHTML = '<span class="no-tags-hint">No common tags</span>';
                return;
            }
            commonTagsContainer.innerHTML = currentCommonTags.map(tag =>
                `<span class="tag-chip tag-chip-editable" style="--tag-color:${tag.category_color||'#6b7280'}">
                    ${escapeHtml(tag.name)}
                    <button class="tag-remove" onclick="window._bulkRemoveTag(${tag.id})">×</button>
                 </span>`
            ).join('');
            window._bulkRemoveTag = removeTag;
        }
    }

    async function removeTag(tagId) {
        if (!currentItemIds.length) return;
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/items/tags/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    item_ids: currentItemIds,
                    add_tag_ids: [],
                    remove_tag_ids: [tagId]
                })
            });
            if (resp.ok) {
                const data = await resp.json();
                if (data.skipped > 0) {
                    showToast(`${data.skipped} item(s) skipped (no permission)`, true);
                }
                await loadCommonTags();
            } else {
                const err = await resp.json();
                showToast(err.detail || 'Failed to remove tag', true);
            }
        } catch (e) {
            showToast('Failed to remove tag', true);
        }
    }

    // ========================================================================
    // Rendering
    // ========================================================================

    // Suggestion list for the token input (rendered into #bulk-tag-results).
    function renderSuggestionsList(results, selectedIndex, onPick) {
        if (!resultsContainer) return;

        if (!results || results.length === 0) {
            resultsContainer.innerHTML = '';
            return;
        }

        const html = results.map((tag, idx) => {
            const selectedClass = idx === selectedIndex ? 'selected' : '';
            return `
                <div class="search-result ${selectedClass}" data-id="${tag.id}" data-index="${idx}">
                    <div class="search-result-main">
                        <span class="search-result-name"
                              style="--tag-color: ${tag.category_color || '#6b7280'}">
                            ${escapeHtml(tag.name)}
                        </span>
                        <span class="search-result-count">${tag.count || 0}</span>
                    </div>
                </div>
            `;
        }).join('');

        resultsContainer.innerHTML = `
            <div class="search-results-header">
                ${results.length} result${results.length !== 1 ? 's' : ''}
            </div>
            ${html}
        `;

        resultsContainer.querySelectorAll('.search-result').forEach(el => {
            el.addEventListener('click', () => onPick(Number(el.dataset.index)));
        });
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
