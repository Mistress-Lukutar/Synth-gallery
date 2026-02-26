/**
 * Gallery Search module
 * Phase 5: Search functionality
 */

(function() {
    let searchInput = null;
    let suggestions = null;
    let searchTimeout = null;
    
    // Store last folder content for clearing search
    let lastFolderData = null;
    let isSearchActive = false;

    function init() {
        searchInput = document.getElementById('tag-search-input');
        suggestions = document.getElementById('tag-suggestions');

        if (!searchInput) {
            console.log('[gallery-search] No search input');
            return;
        }

        setupEventListeners();
        
        // Initialize sort UI from current mode
        if (window.currentSortMode && typeof window.updateSortDropdownUI === 'function') {
            window.updateSortDropdownUI(window.currentSortMode);
        }
        
        console.log('[gallery-search] Initialized');
    }

    function setupEventListeners() {
        // Sort dropdown toggle
        const sortBtn = document.getElementById('sort-btn');
        const sortMenu = document.getElementById('sort-menu');
        if (sortBtn && sortMenu) {
            sortBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                sortMenu.classList.toggle('hidden');
            });

            // Sort option selection
            sortMenu.querySelectorAll('.sort-option').forEach(option => {
                option.addEventListener('click', () => {
                    const sort = option.dataset.sort;
                    sortMenu.classList.add('hidden');
                    
                    // Update active state
                    sortMenu.querySelectorAll('.sort-option').forEach(o => o.classList.remove('active'));
                    option.classList.add('active');
                    
                    // Update tooltip
                    sortBtn.setAttribute('title', sort === 'taken' ? 'Sort: Date Taken' : 'Sort: Date Uploaded');
                    
                    // Update sort mode and rebuild masonry
                    window.currentSortMode = sort;
                    if (typeof window.rebuildMasonry === 'function') {
                        window.rebuildMasonry(true);
                    }
                    
                    // Save preference to server
                    if (window.currentFolderId) {
                        csrfFetch(`${getBaseUrl()}/api/folders/${window.currentFolderId}/sort`, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ sort_by: sort })
                        }).then(resp => {
                            if (resp.ok) {
                                console.log('[Sort] Preference saved:', sort);
                            }
                        }).catch(err => {
                            console.error('[Sort] Error saving preference:', err);
                        });
                    }
                });
            });

            // Close sort menu on click outside
            document.addEventListener('click', (e) => {
                if (!e.target.closest('.sort-dropdown')) {
                    sortMenu.classList.add('hidden');
                }
            });
            
            // Export function to update sort UI from external code
            window.updateSortDropdownUI = function(sort) {
                sortMenu.querySelectorAll('.sort-option').forEach(o => o.classList.remove('active'));
                const activeOption = sortMenu.querySelector(`[data-sort="${sort}"]`);
                if (activeOption) activeOption.classList.add('active');
                sortBtn.setAttribute('title', sort === 'taken' ? 'Sort: Date Taken' : 'Sort: Date Uploaded');
            };
        }

        // Search input with debounce
        searchInput.addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            const query = e.target.value.trim();

            // Update URL without reloading
            updateSearchURL(query);

            if (query.length === 0) {
                // Clear search - return to folder view
                if (suggestions) suggestions.classList.add('hidden');
                isSearchActive = false;
                if (window.currentFolderId) {
                    window.navigateToFolder(window.currentFolderId, false);
                }
                return;
            }

            // Show suggestions for autocomplete
            if (query.length >= 2) {
                searchTimeout = setTimeout(() => {
                    loadSearchSuggestions(query);
                }, 150);
            } else {
                if (suggestions) suggestions.classList.add('hidden');
            }

            // Perform search with debounce
            searchTimeout = setTimeout(() => {
                performSearch(query);
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
                // Optional: clear search on Escape
                // searchInput.value = '';
                // performSearch('');
            }
        });
    }

    function updateSearchURL(query) {
        const url = new URL(window.location);
        if (query) {
            url.searchParams.set('q', query);
        } else {
            url.searchParams.delete('q');
        }
        window.history.replaceState({}, '', url);
    }

    async function loadSearchSuggestions(query) {
        try {
            // Get last word being typed
            const words = query.split(/\s+/);
            const currentWord = words[words.length - 1].toLowerCase();
            
            if (currentWord.length < 2) return;

            // Fetch matching tags
            const resp = await fetch(`${getBaseUrl()}/api/tags/all`);
            if (!resp.ok) return;

            const allTags = await resp.json();
            const existingWords = words.slice(0, -1).map(w => w.toLowerCase());
            
            const matches = allTags.filter(t => 
                t.tag.toLowerCase().includes(currentWord) &&
                !existingWords.includes(t.tag.toLowerCase())
            ).slice(0, 8);

            if (matches.length === 0) {
                if (suggestions) suggestions.classList.add('hidden');
                return;
            }

            if (!suggestions) return;

            suggestions.innerHTML = matches.map(t => `
                <div class="suggestion-item" data-tag="${escapeHtml(t.tag)}" style="--tag-color: ${t.color || '#6b7280'}">
                    <span class="tag-dot" style="background-color: ${t.color || '#6b7280'}"></span>
                    <span>${escapeHtml(t.tag)}</span>
                </div>
            `).join('');

            suggestions.querySelectorAll('.suggestion-item').forEach(item => {
                item.addEventListener('click', () => {
                    const tag = item.dataset.tag.toLowerCase();
                    const value = searchInput.value.trim();
                    const words = value.split(/\s+/);
                    words[words.length - 1] = tag;
                    searchInput.value = words.join(' ') + ' ';
                    suggestions.classList.add('hidden');
                    searchInput.focus();
                    updateSearchURL(searchInput.value.trim());
                    performSearch(searchInput.value.trim());
                });
            });

            suggestions.classList.remove('hidden');
        } catch (err) {
            console.error('Failed to load suggestions:', err);
        }
    }

    async function performSearch(query) {
        if (!query) {
            isSearchActive = false;
            return;
        }

        // Need folder context for search
        if (!window.currentFolderId) {
            console.warn('[search] No current folder ID available');
            return;
        }

        console.log('[search] Performing search for:', query, 'in folder:', window.currentFolderId);
        isSearchActive = true;

        try {
            const resp = await fetch(`${getBaseUrl()}/api/search?tags=${encodeURIComponent(query)}&folder_id=${encodeURIComponent(window.currentFolderId)}`);
            if (!resp.ok) throw new Error('Search failed');

            const data = await resp.json();
            console.log('[search] Results:', data.items?.length, 'items');

            // Render search results using same format as folder content
            renderSearchResults(data);
        } catch (err) {
            console.error('Search failed:', err);
        }
    }

    function renderSearchResults(data) {
        const gallery = document.getElementById('gallery');
        if (!gallery) return;

        // Update folder header to show search mode
        const folderNameEl = document.querySelector('.folder-header h2');
        if (folderNameEl) {
            folderNameEl.textContent = `Search: "${searchInput.value.trim()}"`;
        }

        // Build gallery HTML using same format as navigation.js
        let html = '';
        const items = data.items || [];

        items.forEach(item => {
            if (item.type === 'album') {
                const album = item;
                const coverId = album.cover_photo_id;
                const safeId = album.safe_id;
                const safeIdAttr = safeId ? `data-safe-id="${safeId}"` : '';
                
                const thumbWidth = album.cover_thumb_width || 280;
                const thumbHeight = album.cover_thumb_height || 280;
                const dimsAttr = `data-thumb-width="${thumbWidth}" data-thumb-height="${thumbHeight}"`;
                const aspectStyle = `style="aspect-ratio: ${thumbWidth} / ${thumbHeight};"`;
                
                let imgHtml;
                if (safeId && coverId) {
                    imgHtml = `
                        <div class="gallery-placeholder"></div>
                        <img data-safe-thumbnail="${coverId}"
                             data-safe-id="${safeId}"
                             alt="${escapeHtml(album.name)}"
                             loading="lazy"
                             onload="this.previousElementSibling.style.display='none'; this.style.opacity='1';"
                             style="opacity: 0;">
                    `;
                } else if (coverId) {
                    imgHtml = `
                        <div class="gallery-placeholder"></div>
                        <img src="${getBaseUrl()}/thumbnails/${coverId}" 
                             alt="${escapeHtml(album.name)}"
                             loading="lazy"
                             onload="this.previousElementSibling.style.display='none'; this.style.opacity='1';"
                             onerror="handleImageError(this, 'access')"
                             style="opacity: 0;">
                    `;
                } else {
                    imgHtml = `
                        <div class="album-placeholder">
                            <span>Empty Album</span>
                        </div>
                    `;
                }
                
                html += `
                    <div class="gallery-item album-item" data-album-id="${album.id}" data-item-type="album"
                         ${coverId ? `data-cover-photo-id="${coverId}"` : ''}
                         ${dimsAttr}
                         ${safeIdAttr}>
                        <div class="gallery-link" onclick="handleAlbumClick('${album.id}')" ${aspectStyle}>
                            ${imgHtml}
                            <div class="album-badge">
                                <svg viewBox="0 0 24 24" fill="currentColor" width="12" height="12">
                                    <rect x="3" y="3" width="7" height="7" rx="1"/>
                                    <rect x="14" y="3" width="7" height="7" rx="1"/>
                                    <rect x="3" y="14" width="7" height="7" rx="1"/>
                                    <rect x="14" y="14" width="7" height="7" rx="1"/>
                                </svg>
                                <span>${album.photo_count || 0}</span>
                            </div>
                        </div>
                        <div class="select-indicator" title="Select">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                <polyline points="20 6 9 17 4 12"></polyline>
                            </svg>
                        </div>
                    </div>
                `;
            } else if (item.type === 'photo') {
                const photo = item;
                const safeId = photo.safe_id;
                const safeIdAttr = safeId ? `data-safe-id="${safeId}"` : '';
                const mediaType = photo.media_type || 'image';
                
                const hasDims = photo.thumb_width && photo.thumb_height;
                const finalWidth = hasDims ? photo.thumb_width : 280;
                const finalHeight = hasDims ? photo.thumb_height : 210;
                const dimsAttr = `data-thumb-width="${finalWidth}" data-thumb-height="${finalHeight}"`;
                const aspectStyle = `style="aspect-ratio: ${finalWidth} / ${finalHeight};"`;
                
                if (safeId) {
                    html += `
                        <div class="gallery-item" 
                             data-photo-id="${photo.id}"
                             data-item-type="photo"
                             data-media-type="${mediaType}"
                             ${dimsAttr}
                             data-safe-id="${safeId}">
                            <div class="gallery-link" onclick="openPhoto('${photo.id}')" ${aspectStyle}>
                                <div class="gallery-placeholder"></div>
                                <img data-safe-thumbnail="${photo.id}"
                                     data-safe-id="${safeId}"
                                     alt="${escapeHtml(photo.original_name)}"
                                     loading="lazy"
                                     onload="this.previousElementSibling.style.display='none'; this.style.opacity='1';"
                                     style="opacity: 0;">
                                ${mediaType === 'video' ? `
                                    <div class="video-badge">
                                        <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                            <polygon points="5 3 19 12 5 21 5 3"></polygon>
                                        </svg>
                                    </div>
                                ` : ''}
                            </div>
                            <div class="select-indicator" title="Select">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                    <polyline points="20 6 9 17 4 12"></polyline>
                                </svg>
                            </div>
                        </div>
                    `;
                } else {
                    html += `
                        <div class="gallery-item" 
                             data-photo-id="${photo.id}"
                             data-item-type="photo"
                             data-media-type="${mediaType}"
                             ${dimsAttr}
                             ${safeIdAttr}>
                            <div class="gallery-link" onclick="openPhoto('${photo.id}')" ${aspectStyle}>
                                <div class="gallery-placeholder"></div>
                                <img src="${getBaseUrl()}/thumbnails/${photo.id}" 
                                     alt="${escapeHtml(photo.original_name)}"
                                     loading="lazy"
                                     onload="this.previousElementSibling.style.display='none'; this.style.opacity='1';"
                                     onerror="handleImageError(this, 'access')"
                                     style="opacity: 0;">
                                ${mediaType === 'video' ? `
                                    <div class="video-badge">
                                        <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
                                            <polygon points="5 3 19 12 5 21 5 3"></polygon>
                                        </svg>
                                    </div>
                                ` : ''}
                            </div>
                            <div class="select-indicator" title="Select">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3">
                                    <polyline points="20 6 9 17 4 12"></polyline>
                                </svg>
                            </div>
                        </div>
                    `;
                }
            }
        });

        // Empty state
        if (html === '') {
            html = `
                <div class="empty-state">
                    <p>No results found</p>
                    <p>Try different tags or check your spelling</p>
                </div>
            `;
        }

        // Clear and set HTML
        gallery.innerHTML = html;
        gallery.style.opacity = '1';

        // Trigger masonry rebuild
        if (typeof window.rebuildMasonry === 'function') {
            window.rebuildMasonry(true);
        }

        // Load safe thumbnails
        if (typeof window.loadSafeThumbnails === 'function') {
            window.loadSafeThumbnails();
        }
    }

    // Export performSearch for external use
    window.performSearch = performSearch;
    window.renderSearchResults = renderSearchResults;

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
        
        // Check for search query in URL on page load
        const urlParams = new URLSearchParams(window.location.search);
        const searchQuery = urlParams.get('q');
        if (searchQuery && searchInput) {
            searchInput.value = searchQuery;
            performSearch(searchQuery);
        }
    });

    console.log('[gallery-search.js] Loaded');
})();
