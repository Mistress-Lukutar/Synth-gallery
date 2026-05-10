/**
 * Gallery Bulk Tags - Mass tag editing for selected items
 */

(function() {
    'use strict';

    let modal = null;
    let searchInput = null;
    let resultsContainer = null;
    let commonTagsContainer = null;
    let countLabel = null;
    let aiTagBtn = null;
    let bulkTagsBtn = null;

    let currentItemIds = [];
    let currentCommonTags = [];
    let searchDebounce = null;

    function init() {
        modal = document.getElementById('bulk-tags-modal');
        searchInput = document.getElementById('bulk-tag-search');
        resultsContainer = document.getElementById('bulk-tag-results');
        commonTagsContainer = document.getElementById('bulk-common-tags');
        countLabel = document.getElementById('bulk-tag-count');
        aiTagBtn = document.getElementById('bulk-ai-tag-btn');
        bulkTagsBtn = document.getElementById('bulk-tags-btn');

        if (!modal || !bulkTagsBtn) return;

        bulkTagsBtn.addEventListener('click', openModal);
        aiTagBtn?.addEventListener('click', () => {
            closeModal();
            if (window.startAITagging) window.startAITagging();
        });

        searchInput?.addEventListener('input', (e) => {
            clearTimeout(searchDebounce);
            const query = e.target.value.trim();
            if (!query) {
                resultsContainer.innerHTML = '';
                return;
            }
            searchDebounce = setTimeout(() => doSearch(query), 200);
        });

        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeBulkTagsModal();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                closeBulkTagsModal();
            }
        });
    }

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
        searchInput.value = '';
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
                    ${escapeHtml(tag.display_name||tag.name)}
                    <button class="tag-remove" onclick="window._bulkRemoveTag(${tag.id})">×</button>
                 </span>`
            ).join('');
            window._bulkRemoveTag = removeTag;
        }
    }

    async function doSearch(query) {
        if (typeof window.tagEditorSearch === 'function') {
            const results = await window.tagEditorSearch(query);
            if (typeof window.tagEditorRenderSearchResults === 'function') {
                window.tagEditorRenderSearchResults(resultsContainer, results, (tag) => {
                    addTag(tag.id);
                });
            } else {
                fallbackRenderSearch(results);
            }
        } else {
            // Direct fallback
            try {
                const resp = await fetch(`${getBaseUrl()}/api/tags/search?q=${encodeURIComponent(query)}&limit=50`);
                if (resp.ok) {
                    const data = await resp.json();
                    fallbackRenderSearch(data.tags || []);
                }
            } catch (e) {
                console.error('Search failed:', e);
            }
        }
    }

    function fallbackRenderSearch(results) {
        if (!resultsContainer) return;
        if (!results.length) {
            resultsContainer.innerHTML = '';
            return;
        }
        resultsContainer.innerHTML = results.map(tag =>
            `<div class="search-result" onclick="window._bulkAddTag(${tag.id})">
                <div class="search-result-main">
                    <span class="search-result-name" style="--tag-color:${tag.category_color||'#6b7280'}">
                        ${escapeHtml(tag.display_name||tag.name)}
                    </span>
                    <span class="search-result-count">${tag.count||0}</span>
                </div>
            </div>`
        ).join('');
        window._bulkAddTag = addTag;
    }

    async function addTag(tagId) {
        if (!currentItemIds.length) return;
        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/items/tags/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    item_ids: currentItemIds,
                    add_tag_ids: [tagId],
                    remove_tag_ids: []
                })
            });
            if (resp.ok) {
                const data = await resp.json();
                if (data.skipped > 0) {
                    showToast(`${data.skipped} item(s) skipped (no permission)`, true);
                }
                searchInput.value = '';
                resultsContainer.innerHTML = '';
                await loadCommonTags();
            } else {
                const err = await resp.json();
                showToast(err.detail || 'Failed to add tag', true);
            }
        } catch (e) {
            showToast('Failed to add tag', true);
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
