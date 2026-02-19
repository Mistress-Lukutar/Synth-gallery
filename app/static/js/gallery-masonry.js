/**
 * Gallery Masonry module
 * Phase 1: Masonry Module for gallery.html refactor
 */

(function() {
    let gallery = null;
    const MIN_COLUMN_WIDTH = 280;
    let masonryBuilt = false;
    let lastColumnCount = 0;
    let lastGalleryWidth = 0;

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
        window.allItems = Array.from(gallery.querySelectorAll('.gallery-item'));
        window.rebuildMasonry(true);
        window.processPendingDimensions?.();
    };

    window.rebuildMasonry = function(forceRebuild = false) {
        if (!gallery) init();
        if (!gallery) return;
        
        console.log('[gallery-masonry] rebuildMasonry called, force:', forceRebuild);
        
        const galleryWidth = gallery.clientWidth || 800;
        const columnCount = Math.max(2, Math.floor(galleryWidth / MIN_COLUMN_WIDTH));
        const columnWidth = galleryWidth / columnCount;

        // Always rebuild if gallery has content but showing empty-state
        const items = gallery.querySelectorAll('.gallery-item');
        const hasItems = items.length > 0;
        const hasEmptyState = gallery.querySelector('.empty-state') !== null;
        
        console.log('[gallery-masonry] items:', items.length, 'hasEmptyState:', hasEmptyState);
        
        if (hasItems && hasEmptyState) {
            forceRebuild = true;
        }

        if (masonryBuilt && !forceRebuild && columnCount === lastColumnCount) {
            return;
        }

        // Update allItems
        window.allItems = Array.from(items);

        const visibleItems = window.allItems.filter(item => item.dataset.hidden !== 'true');
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

        // Create columns
        const columns = [];
        const columnHeights = [];
        for (let i = 0; i < columnCount; i++) {
            const col = document.createElement('div');
            col.className = 'gallery-column';
            columns.push(col);
            columnHeights.push(0);
            gallery.appendChild(col);
        }

        // Distribute items
        visibleItems.forEach(item => {
            const shortestIdx = columnHeights.indexOf(Math.min(...columnHeights));
            columns[shortestIdx].appendChild(item);

            const height = parseInt(item.dataset.thumbHeight) || 200;
            const width = parseInt(item.dataset.thumbWidth) || 280;
            const aspectHeight = (height / width) * columnWidth;
            columnHeights[shortestIdx] += aspectHeight + 10;
        });

        masonryBuilt = true;
        lastColumnCount = columnCount;
        lastGalleryWidth = galleryWidth;
        gallery.style.opacity = '1';

        requestAnimationFrame(() => window.scrollTo(0, scrollY));
    };

    window.processPendingDimensions = function() {
        if (!gallery) return;
        const items = (window.allItems || []).filter(item => !item.dataset.thumbWidth);
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
