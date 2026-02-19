/**
 * Gallery Search module
 * Phase 5: Search functionality
 */

(function() {
    let searchInput = null;
    let suggestions = null;

    function init() {
        searchInput = document.getElementById('tag-search-input');
        suggestions = document.getElementById('tag-suggestions');

        if (!searchInput) {
            console.log('[gallery-search] No search input');
            return;
        }

        setupEventListeners();
        console.log('[gallery-search] Initialized');
    }

    function setupEventListeners() {
        let searchTimeout;

        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            const query = e.target.value.trim();

            if (query.length < 2) {
                if (suggestions) suggestions.classList.add('hidden');
                return;
            }

            searchTimeout = setTimeout(async () => {
                try {
                    const resp = await fetch(`${getBaseUrl()}/api/search?q=${encodeURIComponent(query)}`);
                    if (!resp.ok) return;

                    const data = await resp.json();
                    window.renderSearchSuggestions(data);
                } catch (err) {
                    console.error('Search failed:', err);
                }
            }, 300);
        });

        // Close suggestions on click outside
        document.addEventListener('click', (e) => {
            if (suggestions && !e.target.closest('.search-container')) {
                suggestions.classList.add('hidden');
            }
        });

        // Keyboard navigation
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                if (suggestions) suggestions.classList.add('hidden');
            }
        });
    }

    window.renderSearchSuggestions = function(data) {
        if (!suggestions) return;

        if (!data.photos || data.photos.length === 0) {
            suggestions.classList.add('hidden');
            return;
        }

        suggestions.innerHTML = data.photos.map(photo => `
            <div class="suggestion-item" data-photo-id="${photo.id}">
                <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" alt="">
                <span>${escapeHtml(photo.original_name)}</span>
            </div>
        `).join('');

        suggestions.querySelectorAll('.suggestion-item').forEach(item => {
            item.addEventListener('click', () => {
                const photoId = item.dataset.photoId;
                window.openPhoto?.(photoId);
                suggestions.classList.add('hidden');
                searchInput.value = '';
            });
        });

        suggestions.classList.remove('hidden');
    };

    window.performSearch = async function(query) {
        if (!query) return;

        try {
            const resp = await fetch(`${getBaseUrl()}/api/search?q=${encodeURIComponent(query)}`);
            if (!resp.ok) throw new Error('Search failed');

            const data = await resp.json();
            
            // Filter gallery items
            const gallery = document.getElementById('gallery');
            if (!gallery) return;

            const items = gallery.querySelectorAll('.gallery-item');
            const photoIds = new Set(data.photos.map(p => p.id));

            items.forEach(item => {
                const photoId = item.dataset.photoId;
                if (photoId) {
                    const hidden = !photoIds.has(photoId);
                    item.dataset.hidden = hidden ? 'true' : 'false';
                    item.style.display = hidden ? 'none' : '';
                }
            });

            // Rebuild masonry
            if (typeof window.rebuildMasonry === 'function') {
                window.rebuildMasonry(true);
            }
        } catch (err) {
            console.error('Search failed:', err);
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-search.js] Loaded');
})();
