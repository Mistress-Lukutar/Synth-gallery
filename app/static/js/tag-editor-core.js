/**
 * Tag Editor Core - Reusable tag search and rendering utilities
 * Used by both gallery-tags.js (single item) and gallery-bulk-tags.js (bulk)
 */

(function() {
    'use strict';

    window.tagEditorSearch = async function(query) {
        if (!query || query.length < 1) return [];
        try {
            const resp = await fetch(
                `${getBaseUrl()}/api/tags/search?q=${encodeURIComponent(query)}&limit=50`
            );
            if (resp.ok) {
                const data = await resp.json();
                return data.tags || [];
            }
        } catch (e) {
            console.error('Tag search failed:', e);
        }
        return [];
    };

    window.tagEditorRenderSearchResults = function(container, results, onSelect) {
        if (!container) return;
        if (!results || results.length === 0) {
            container.innerHTML = '';
            return;
        }

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        const html = results.map(tag => `
            <div class="search-result" data-id="${tag.id}"
                 onclick="window._tagEditorSelect && window._tagEditorSelect(${tag.id})">
                <div class="search-result-main">
                    <span class="search-result-name"
                          style="--tag-color: ${tag.category_color || '#6b7280'}">
                        ${escapeHtml(tag.display_name || tag.name)}
                    </span>
                    <span class="search-result-count">${tag.count || 0}</span>
                </div>
            </div>
        `).join('');

        container.innerHTML = `
            <div class="search-results-header">
                ${results.length} result${results.length !== 1 ? 's' : ''}
            </div>
            ${html}
        `;

        window._tagEditorSelect = function(tagId) {
            const tag = results.find(t => t.id === tagId);
            if (tag && onSelect) onSelect(tag);
        };
    };

    window.tagEditorRenderChips = function(container, tags, options) {
        if (!container) return;
        options = options || {};
        const removable = options.removable || false;
        const onRemove = options.onRemove || null;
        const emptyHint = options.emptyHint || 'No tags';

        function escapeHtml(text) {
            if (!text) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        if (!tags || tags.length === 0) {
            container.innerHTML = `<span class="no-tags-hint">${escapeHtml(emptyHint)}</span>`;
            return;
        }

        // Group by category
        const grouped = {};
        for (const tag of tags) {
            const catName = tag.category_name || 'Other';
            if (!grouped[catName]) {
                grouped[catName] = { tags: [], color: tag.category_color || '#6b7280' };
            }
            grouped[catName].tags.push(tag);
        }

        let html = '<div class="tags-section">';
        for (const [catName, group] of Object.entries(grouped)) {
            html += `
                <div class="tag-group">
                    <div class="tag-group-header" style="--tag-color: ${group.color}">
                        ${escapeHtml(catName)}
                    </div>
                    <div class="tags-list">
                        ${group.tags.map(tag => `
                            <span class="tag-chip tag-chip-editable"
                                  style="--tag-color: ${tag.category_color || '#6b7280'}">
                                ${escapeHtml(tag.display_name || tag.name)}
                                ${removable ? `<button class="tag-remove"
                                        onclick="window._tagEditorRemove && window._tagEditorRemove(${tag.id})"
                                        title="Remove">×</button>` : ''}
                            </span>
                        `).join('')}
                    </div>
                </div>
            `;
        }
        html += '</div>';

        container.innerHTML = html;

        window._tagEditorRemove = function(tagId) {
            if (onRemove) onRemove(tagId);
        };
    };
})();
