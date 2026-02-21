/**
 * Gallery Masonry module
 * Phase 1: Masonry Module for gallery.html refactor
 * Based on v0.8.5 implementation - column-based layout with visual order tracking
 */

(function() {
    let gallery = null;
    const MIN_COLUMN_WIDTH = 280;
    let masonryBuilt = false;
    let lastColumnCount = 0;
    let lastGalleryWidth = 0;
    let allItems = [];

    function init() {
        gallery = document.getElementById('gallery');
        if (!gallery) {
            console.log('[gallery-masonry] No gallery element found');
            return;
        }
        console.log('[gallery-masonry] Initialized');
    }

    window.getColumnCount = function() {
        if (!gallery) return 2;
        const width = gallery.clientWidth || 800;
        return Math.max(2, Math.floor(width / MIN_COLUMN_WIDTH));
    };

    window.initMasonry = function() {
        if (!gallery) init();
        if (!gallery) return;
        allItems = Array.from(gallery.querySelectorAll('.gallery-item'));
        window.allItems = allItems;
        window.rebuildMasonry(true);
        window.processPendingDimensions?.();
    };

    window.rebuildMasonry = function(forceRebuild = false) {
        console.log('[gallery-masonry] rebuildMasonry START, gallery:', gallery, 'force:', forceRebuild);
        
        if (!gallery) {
            console.log('[gallery-masonry] No gallery, calling init');
            init();
        }
        if (!gallery) {
            console.log('[gallery-masonry] Still no gallery, cannot rebuild');
            return;
        }
        
        const galleryWidth = gallery.clientWidth || 800;
        const columnCount = Math.max(2, Math.floor(galleryWidth / MIN_COLUMN_WIDTH));
        const columnWidth = galleryWidth / columnCount;

        // Always rebuild if gallery has content but showing empty-state
        const items = gallery.querySelectorAll('.gallery-item');
        const hasItems = items.length > 0;
        const hasEmptyState = gallery.querySelector('.empty-state') !== null;
        
        if (hasItems && hasEmptyState) {
            forceRebuild = true;
        }

        // Sync allItems with window.allItems (for SPA navigation compatibility)
        if (window.allItems !== undefined) {
            allItems = window.allItems;
        }

        // Skip rebuild if column count hasn't changed (unless forced or first build)
        if (masonryBuilt && !forceRebuild && columnCount === lastColumnCount) {
            return;
        }

        // Update allItems reference
        window.allItems = allItems;

        const visibleItems = allItems.filter(item => item.dataset.hidden !== 'true');
        const scrollY = window.scrollY;

        gallery.innerHTML = '';

        if (visibleItems.length === 0) {
            gallery.innerHTML = `
                <div class="empty-state">
                    <p>No photos yet</p>
                    <p>Click "Upload" to add your first photos</p>
                </div>
            `;
            masonryBuilt = true;
            lastColumnCount = columnCount;
            lastGalleryWidth = galleryWidth;
            gallery.style.opacity = '1';
            return;
        }

        // Create columns with height tracking
        const columns = [];
        const columnHeights = [];
        for (let i = 0; i < columnCount; i++) {
            const col = document.createElement('div');
            col.className = 'gallery-column';
            columns.push(col);
            columnHeights.push(0);
            gallery.appendChild(col);
        }

        // Track visual order for navigation (row by row reading order)
        const itemPositions = []; // {item, column, row}

        // Distribute items to shortest column for balanced heights
        visibleItems.forEach((item) => {
            // Find shortest column
            let shortestIndex = 0;
            let shortestHeight = columnHeights[0];
            for (let i = 1; i < columnCount; i++) {
                if (columnHeights[i] < shortestHeight) {
                    shortestHeight = columnHeights[i];
                    shortestIndex = i;
                }
            }

            // Track position for navigation
            const row = columns[shortestIndex].children.length;
            itemPositions.push({ item, column: shortestIndex, row });

            columns[shortestIndex].appendChild(item);

            // Update height estimate using data-attributes (preferred) or fallback
            const thumbW = parseInt(item.dataset.thumbWidth) || 0;
            const thumbH = parseInt(item.dataset.thumbHeight) || 0;

            let itemHeight;
            if (thumbW > 0 && thumbH > 0) {
                // Use stored dimensions
                itemHeight = (thumbH / thumbW) * columnWidth;
            } else {
                // Fallback for legacy photos - use square placeholder or loaded image
                const img = item.querySelector('img');
                if (img && img.naturalHeight && img.naturalWidth) {
                    itemHeight = (img.naturalHeight / img.naturalWidth) * columnWidth;
                } else {
                    itemHeight = columnWidth; // Square fallback
                }
            }
            columnHeights[shortestIndex] += itemHeight + 16; // 16 = gap
        });

        // Sort by visual reading order (row first, then column)
        itemPositions.sort((a, b) => {
            if (a.row !== b.row) return a.row - b.row;
            return a.column - b.column;
        });

        // Save navigation order to sessionStorage
        const navOrder = itemPositions.map(p => ({
            type: p.item.dataset.itemType,
            id: p.item.dataset.photoId || p.item.dataset.albumId
        }));
        saveNavigationOrder(navOrder);

        masonryBuilt = true;
        lastColumnCount = columnCount;
        lastGalleryWidth = galleryWidth;
        gallery.style.opacity = '1';

        requestAnimationFrame(() => window.scrollTo(0, scrollY));
    };

    function saveNavigationOrder(order) {
        sessionStorage.setItem('galleryNavOrder', JSON.stringify(order));
    }

    window.processPendingDimensions = function() {
        if (!gallery) return;
        const items = (allItems || []).filter(item => !item.dataset.thumbWidth);
        if (items.length === 0) return;

        let pending = items.length;
        let needsRebuild = false;

        items.forEach(item => {
            const img = item.querySelector('img');
            if (!img) {
                pending--;
                return;
            }

            const saveAndUpdate = () => {
                if (img.naturalWidth && img.naturalHeight) {
                    item.dataset.thumbWidth = img.naturalWidth;
                    item.dataset.thumbHeight = img.naturalHeight;

                    const link = item.querySelector('.gallery-link');
                    if (link) {
                        link.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
                    }

                    const photoId = item.dataset.photoId || item.dataset.coverPhotoId;
                    if (photoId) {
                        window.saveDimensionsToServer?.(photoId, img.naturalWidth, img.naturalHeight);
                    }
                    needsRebuild = true;
                }
                pending--;
                if (pending <= 0 && needsRebuild) {
                    window.rebuildMasonry(true);
                }
            };

            if (img.complete && img.naturalWidth) {
                saveAndUpdate();
            } else {
                img.addEventListener('load', saveAndUpdate, { once: true });
                img.addEventListener('error', () => { pending--; }, { once: true });
            }
        });
    };

    window.saveDimensionsToServer = async function(photoId, width, height) {
        try {
            await csrfFetch(`${getBaseUrl()}/api/photos/${photoId}/dimensions`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ width, height })
            });
        } catch (e) {
            console.warn('Failed to save dimensions:', photoId);
        }
    };

    // Called when gallery image loads (from inline onload handler)
    // Debounced masonry rebuild for image load events
    let imageLoadDebounceTimer;
    window.onGalleryImageLoad = function(img) {
        // Save dimensions to item dataset for masonry calculations
        const item = img.closest('.gallery-item');
        if (item && img.naturalWidth && img.naturalHeight) {
            item.dataset.thumbWidth = img.naturalWidth;
            item.dataset.thumbHeight = img.naturalHeight;
            
            // Update aspect-ratio on gallery-link
            const link = item.querySelector('.gallery-link');
            if (link) {
                link.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
            }
        }
        
        // Debounce masonry rebuild to avoid excessive recalculations
        clearTimeout(imageLoadDebounceTimer);
        imageLoadDebounceTimer = setTimeout(() => {
            rebuildMasonry(true);
        }, 100);
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    // Also init on load (for when gallery items are rendered by navigation.js)
    window.addEventListener('load', () => {
        if (!gallery) init();
        const items = gallery?.querySelectorAll('.gallery-item');
        if (items && items.length > 0) {
            window.initMasonry();
        }
    });

    window.addEventListener('resize', () => {
        window.rebuildMasonry();
    });

    console.log('[gallery-masonry.js] Loaded');
})();
