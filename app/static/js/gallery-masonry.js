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
    let lastRebuildTime = 0;
    const MIN_REBUILD_INTERVAL = 100; // ms

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
    };

    window.rebuildMasonry = function(forceRebuild = false) {
        const now = Date.now();
        
        // Skip if rebuild was called too recently (unless forced)
        if (!forceRebuild && (now - lastRebuildTime < MIN_REBUILD_INTERVAL)) {
            console.log('[gallery-masonry] rebuildMasonry SKIPPED (too soon)');
            return;
        }
        
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

        // Always get fresh items from DOM - this is the source of truth
        allItems = Array.from(gallery.querySelectorAll('.gallery-item'));
        window.allItems = allItems;
        
        console.log('[gallery-masonry] rebuildMasonry START, items:', allItems.length, 'force:', forceRebuild);

        // Always rebuild if gallery has content but showing empty-state
        const hasItems = allItems.length > 0;
        const hasEmptyState = gallery.querySelector('.empty-state') !== null;
        
        if (hasItems && hasEmptyState) {
            forceRebuild = true;
        }

        // Skip rebuild if column count hasn't changed (unless forced or first build)
        if (masonryBuilt && !forceRebuild && columnCount === lastColumnCount) {
            console.log('[gallery-masonry] rebuildMasonry SKIPPED (same column count)');
            return;
        }

        let visibleItems = allItems.filter(item => item.dataset.hidden !== 'true');
        
        // Sort items based on current sort mode (excluding subfolders which are separate)
        const sortMode = window.currentSortMode || 'uploaded';
        visibleItems.sort((a, b) => {
            // Don't sort folders (they're handled separately), only albums and photos
            const aIsFolder = a.classList.contains('subfolder-tile') || a.dataset.itemType === 'folder';
            const bIsFolder = b.classList.contains('subfolder-tile') || b.dataset.itemType === 'folder';
            if (aIsFolder || bIsFolder) return 0;
            
            // Get dates for comparison
            let aDate, bDate;
            if (sortMode === 'taken') {
                aDate = a.dataset.takenAt || a.dataset.uploadedAt;
                bDate = b.dataset.takenAt || b.dataset.uploadedAt;
            } else {
                aDate = a.dataset.uploadedAt || a.dataset.takenAt;
                bDate = b.dataset.uploadedAt || b.dataset.takenAt;
            }
            
            // Parse dates (handle ISO strings)
            const parseDate = (d) => d ? new Date(d).getTime() : 0;
            const aTime = parseDate(aDate);
            const bTime = parseDate(bDate);
            
            // Descending order (newest first)
            return bTime - aTime;
        });
        
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

        // Navigation order is now determined chronologically in gallery-lightbox.js

        masonryBuilt = true;
        lastColumnCount = columnCount;
        lastGalleryWidth = galleryWidth;
        gallery.style.opacity = '1';
        lastRebuildTime = Date.now();

        requestAnimationFrame(() => window.scrollTo(0, scrollY));
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
    // No longer triggers masonry rebuild since placeholder sizes are now correct from DB
    window.onGalleryImageLoad = function(img) {
        // Just hide the placeholder and show the image
        // Geometry doesn't change since placeholder already has correct dimensions
        img.style.opacity = '1';
        const placeholder = img.previousElementSibling;
        if (placeholder && placeholder.classList.contains('gallery-placeholder')) {
            placeholder.style.display = 'none';
        }
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

    // Debounced resize handler
    let resizeDebounceTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeDebounceTimer);
        resizeDebounceTimer = setTimeout(() => {
            window.rebuildMasonry();
        }, 150);
    });

    console.log('[gallery-masonry.js] Loaded');
})();
