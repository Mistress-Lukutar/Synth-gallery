/**
 * Gallery Lightbox module
 * Phase 3: Lightbox, photo navigation
 */

(function() {
    let lightbox = null;
    let currentPhotoId = null;
    let currentPhotos = [];
    let currentIndex = 0;
    
    // Album context
    let albumContext = null; // { photos: [], index: 0 }
    
    // Cancellation support for image loading
    let currentFetchController = null;
    let currentImageLoadController = null;
    let currentFullImageLoader = null;
    
    // Cancel any pending image loads for lightbox only (does not affect gallery thumbnails)
    function cancelImageLoading() {
        if (currentImageLoadController) {
            currentImageLoadController.abort();
            currentImageLoadController = null;
        }
        if (currentFetchController) {
            currentFetchController.abort();
            currentFetchController = null;
        }
        if (currentFullImageLoader) {
            currentFullImageLoader.src = '';
            currentFullImageLoader.onload = null;
            currentFullImageLoader.onerror = null;
            currentFullImageLoader = null;
        }
        // Note: window.stop() is NOT used here as it cancels ALL network requests including gallery thumbnails
    }
    
    // Cancel all page loads - used only when closing lightbox
    function cancelAllLoading() {
        cancelImageLoading();
        // Stop browser from continuing to download images when closing lightbox
        if (window.stop) {
            window.stop();
        }
    }

    function init() {
        lightbox = document.getElementById('lightbox');
        if (!lightbox) {
            console.log('[gallery-lightbox] No lightbox element');
            return;
        }
        setupEventListeners();
        console.log('[gallery-lightbox] Initialized');
    }
    
    // Lazy load album when navigating to it
    async function expandAlbumInNavOrder(albumId, insertIndex) {
        // Check cache first
        const cached = albumCache.get(albumId);
        if (cached && (Date.now() - cached.timestamp) < ALBUM_CACHE_TTL) {
            return cached.photos;
        }
        
        // Fetch album
        try {
            const resp = await fetch(`${getBaseUrl()}/api/albums/${albumId}`);
            if (!resp.ok) return null;
            
            const album = await resp.json();
            if (!album.photos || album.photos.length === 0) return null;
            
            // Cache album data
            albumCache.set(albumId, {
                photos: album.photos,
                name: album.name,
                timestamp: Date.now()
            });
            
            // Expand in flatNavOrder - Phase 5: polymorphic items
            const albumPhotos = album.photos.map(p => ({
                type: 'item',  // Phase 5: polymorphic item
                id: p.id,
                safeId: p.safe_id,
                albumId: albumId
            }));
            
            // Replace placeholder with actual photos
            flatNavOrder.splice(insertIndex, 1, 
                { type: 'album_marker', id: albumId, albumName: album.name },
                ...albumPhotos
            );
            
            return albumPhotos;
        } catch (e) {
            console.warn('[lightbox] Failed to expand album:', albumId);
            return null;
        }
    }

    function setupEventListeners() {
        // Keyboard navigation (Escape is handled by BackButtonManager)
        document.addEventListener('keydown', (e) => {
            if (lightbox.classList.contains('hidden')) return;
            
            if (e.key === 'ArrowLeft') {
                window.navigateLightbox(-1);
            } else if (e.key === 'ArrowRight') {
                window.navigateLightbox(1);
            }
        });

        // Close on overlay click
        const overlay = lightbox.querySelector('.lightbox-overlay');
        if (overlay) {
            overlay.addEventListener('click', window.closeLightbox);
        }

        // Prev/Next buttons
        const prevBtn = lightbox.querySelector('.lightbox-prev');
        const nextBtn = lightbox.querySelector('.lightbox-next');
        if (prevBtn) prevBtn.addEventListener('click', () => window.navigateLightbox(-1));
        if (nextBtn) nextBtn.addEventListener('click', () => window.navigateLightbox(1));

        // Close button
        const closeBtn = lightbox.querySelector('.lightbox-close');
        if (closeBtn) closeBtn.addEventListener('click', window.closeLightbox);
        
        // Touch swipe navigation for mobile
        setupTouchNavigation();
    }
    
    // Touch swipe navigation
    let touchStartX = 0;
    let touchStartY = 0;
    let isSwiping = false;
    
    function setupTouchNavigation() {
        const mediaContainer = lightbox?.querySelector('.lightbox-media');
        if (!mediaContainer) return;
        
        mediaContainer.addEventListener('touchstart', handleTouchStart, { passive: true });
        mediaContainer.addEventListener('touchmove', handleTouchMove, { passive: true });
        mediaContainer.addEventListener('touchend', handleTouchEnd, { passive: true });
    }
    
    function handleTouchStart(e) {
        if (lightbox.classList.contains('hidden')) return;
        // Ignore multi-touch gestures (pinch-to-zoom)
        if (e.touches.length > 1) {
            isSwiping = false;
            return;
        }
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        isSwiping = true;
    }
    
    function handleTouchMove(e) {
        if (!isSwiping || lightbox.classList.contains('hidden')) return;
        // Could add visual feedback here in the future
    }
    
    function handleTouchEnd(e) {
        if (!isSwiping || lightbox.classList.contains('hidden')) return;
        // Ignore if gesture ended with multiple touches (pinch-to-zoom)
        if (e.changedTouches.length > 1) {
            isSwiping = false;
            return;
        }
        isSwiping = false;
        
        const touchEndX = e.changedTouches[0].clientX;
        const touchEndY = e.changedTouches[0].clientY;
        
        const diffX = touchStartX - touchEndX;
        const diffY = touchStartY - touchEndY;
        
        const swipeThreshold = 50; // Minimum swipe distance
        
        if (Math.abs(diffX) > Math.abs(diffY)) {
            // Horizontal swipe - navigation
            if (Math.abs(diffX) > swipeThreshold) {
                if (diffX > 0) {
                    // Swiped left - go to next
                    window.navigateLightbox(1, true);
                } else {
                    // Swiped right - go to previous
                    window.navigateLightbox(-1, true);
                }
            }
        } else {
            // Vertical swipe - close lightbox
            if (Math.abs(diffY) > swipeThreshold) {
                // diffY > 0 means user swiped UP (touch moved up), so image goes UP
                // diffY < 0 means user swiped DOWN (touch moved down), so image goes DOWN
                window.closeLightboxWithAnimation(diffY > 0 ? 'up' : 'down');
            }
        }
    }

    // Set album context for navigation
    window.setAlbumContext = function(photos, startIndex = 0) {
        albumContext = {
            photos: photos,
            index: startIndex
        };
        console.log('[gallery-lightbox] Album context set:', photos.length, 'photos');
    };

    // Clear album context
    window.clearAlbumContext = function() {
        albumContext = null;
    };

    // Expose rebuild function for external use (e.g., when opening album)
    window.rebuildLightboxNavOrder = function() {
        flatNavOrder = buildFlatNavOrder();
        console.log('[lightbox] Nav order rebuilt:', flatNavOrder.length, 'items');
    };
    
    // Set navigation order directly from album items (when opening album from gallery)
    window.setLightboxNavOrderFromAlbum = function(photos, startIndex = 0) {
        flatNavOrder = photos.map(p => ({
            type: 'item',  // Phase 5: polymorphic item
            id: p.id,
            safeId: p.safeId,
            albumId: p.albumId || null
        }));
        currentNavIndex = startIndex;
        console.log('[lightbox] Nav order set from album:', flatNavOrder.length, 'photos, index:', startIndex);
    };
    
    // Expand album in gallery navigation order (allows navigating beyond the album)
    window.expandAlbumInLightboxNav = function(albumId, albumPhotos, startIndex = 0) {
        // Build base nav order from gallery
        flatNavOrder = buildFlatNavOrder();
        
        // Find the album in the order
        const albumIndex = flatNavOrder.findIndex(p => 
            (p.type === 'album' || p.type === 'album_placeholder') && p.id === albumId
        );
        
        // Prepare album items for insertion
        const expandedPhotos = albumPhotos.map(p => ({
            type: 'item',  // Phase 5: polymorphic item
            id: p.id,
            safeId: p.safeId,
            albumId: albumId
        }));
        
        if (albumIndex >= 0) {
            // Replace album with its photos
            flatNavOrder.splice(albumIndex, 1, ...expandedPhotos);
            // Set current index to the specified photo within the album
            currentNavIndex = albumIndex + startIndex;
        } else {
            // Album not found in gallery, fallback to album-only navigation
            flatNavOrder = expandedPhotos;
            currentNavIndex = startIndex;
        }
        
        console.log('[lightbox] Album expanded in nav:', albumId, 'total items:', flatNavOrder.length, 'current index:', currentNavIndex);
    };

    // Build flat navigation list from gallery order
    // Uses chronological order based on current sort mode, not visual masonry order
    function buildFlatNavOrder() {
        const gallery = document.getElementById('gallery');
        if (!gallery) return [];
        
        // Get all gallery items (photos and albums) from DOM
        const allItems = Array.from(gallery.querySelectorAll('.gallery-item'));
        if (allItems.length === 0) return [];
        
        // Get current sort mode
        const sortMode = window.currentSortMode || 'uploaded';
        
        // Sort items chronologically based on current sort mode
        // This ensures lightbox navigation follows chronological order,
        // not visual masonry order (which is broken by different image heights)
        allItems.sort((a, b) => {
            let aDate, bDate;
            
            // Get dates based on sort mode
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
        
        // Build flat order - Phase 5: supports polymorphic items (type: 'item')
        const flatOrder = [];
        for (const item of allItems) {
            const itemType = item.dataset.itemType;
            
            if (itemType === 'album') {
                const albumId = item.dataset.albumId;
                // Check cache first
                const cached = albumCache.get(albumId);
                if (cached && (Date.now() - cached.timestamp) < ALBUM_CACHE_TTL) {
                    // Use cached album data
                    if (cached.photos.length > 0) {
                        flatOrder.push({ type: 'album_marker', id: albumId, albumName: cached.name });
                        for (const photo of cached.photos) {
                            flatOrder.push({
                                type: 'item',  // Phase 5: polymorphic item
                                id: photo.id,
                                safeId: photo.safe_id,
                                albumId: albumId
                            });
                        }
                    }
                } else {
                    // Add placeholder - will be expanded when navigated to
                    flatOrder.push({
                        type: 'album_placeholder',
                        id: albumId,
                        albumId: albumId
                    });
                }
            } else if (itemType === 'item' || itemType === 'photo') {
                // Standalone item (Phase 5: type 'item', legacy: 'photo')
                const itemId = item.dataset.itemId || item.dataset.photoId;
                flatOrder.push({
                    type: 'item',  // Phase 5: polymorphic item
                    id: itemId,
                    safeId: item.dataset.safeId
                });
            }
        }
        
        return flatOrder;
    }
    
    // Store flat navigation order
    let flatNavOrder = [];
    let currentNavIndex = -1;
    let albumCache = new Map(); // Cache album data: albumId -> { photos, timestamp }
    const ALBUM_CACHE_TTL = 5 * 60 * 1000; // 5 minutes

    window.openPhoto = async function(photoId) {
        if (!lightbox) init();
        if (!lightbox) return;
        
        // Check access permissions on the gallery item
        const gallery = document.getElementById('gallery');
        const galleryItem = gallery?.querySelector(`.gallery-item[data-photo-id="${photoId}"]`);
        
        if (galleryItem) {
            const access = galleryItem.dataset.access;
            const safeId = galleryItem.dataset.safeId;
            
            if (access === 'denied') {
                console.log('[openPhoto] Access denied for photo:', photoId);
                return;
            }
            
            if (access === 'locked' && safeId) {
                console.log('[openPhoto] Safe locked, showing unlock modal for:', safeId);
                
                let safeName = 'Safe';
                let unlockType = 'password';
                if (window.userSafes) {
                    const safe = window.userSafes.find(s => s.id === safeId);
                    if (safe) {
                        safeName = safe.name;
                        unlockType = safe.unlock_type;
                    }
                }
                
                if (typeof openSafeUnlock === 'function') {
                    openSafeUnlock(safeId, safeName, unlockType);
                } else {
                    console.error('[openPhoto] openSafeUnlock not available');
                }
                return;
            }
        }
        
        // Check if clicked item is an album (not a photo)
        const clickedAlbum = gallery?.querySelector(`.gallery-item[data-album-id="${photoId}"]`);
        if (clickedAlbum) {
            // User clicked on album - let openAlbum handle it
            if (typeof window.openAlbum === 'function') {
                window.openAlbum(photoId);
                return;
            }
        }
        
        // Build flat navigation order (quick, without waiting for album fetches)
        flatNavOrder = buildFlatNavOrder();
        
        // Find current item in flat order - Phase 5: support polymorphic items
        currentNavIndex = flatNavOrder.findIndex(p => p.id === photoId && (p.type === 'item' || p.type === 'photo'));
        
        // If not found, check if it's in an album that needs to be expanded
        if (currentNavIndex === -1) {
            // Check if this photo belongs to an album in the gallery
            const albumItem = gallery?.querySelector('.gallery-item[data-item-type="album"]');
            if (albumItem) {
                const albumId = albumItem.dataset.albumId;
                // Try to find and expand this album
                const placeholderIndex = flatNavOrder.findIndex(p => p.type === 'album_placeholder' && p.albumId === albumId);
                if (placeholderIndex >= 0) {
                    await expandAlbumInNavOrder(albumId, placeholderIndex);
                    // Re-find item after expansion
                    currentNavIndex = flatNavOrder.findIndex(p => p.id === photoId && (p.type === 'item' || p.type === 'photo'));
                }
            }
        }
        
        // If still not found, add as standalone - Phase 5: polymorphic item
        if (currentNavIndex === -1) {
            flatNavOrder = [{ type: 'item', id: photoId, safeId: galleryItem?.dataset.safeId }];  // Phase 5
            currentNavIndex = 0;
            window.clearAlbumContext();
        } else {
            // Check if this photo is part of an album
            const finalItem = flatNavOrder[currentNavIndex];
            if (finalItem && finalItem.albumId) {
                // Find all photos in this album
                const albumPhotos = flatNavOrder.filter(p => p.albumId === finalItem.albumId);
                const albumIndex = albumPhotos.findIndex(p => p.id === photoId);
                
                // Set album context
                albumContext = {
                    photos: albumPhotos,
                    index: albumIndex >= 0 ? albumIndex : 0,
                    albumId: finalItem.albumId
                };
            } else {
                window.clearAlbumContext();
            }
        }
        
        currentPhotoId = photoId;
        
        // Cancel any pending image load
        if (currentImageLoadController) {
            currentImageLoadController.abort();
            currentImageLoadController = null;
        }

        // Update URL with photo_id without reloading page
        const url = new URL(window.location.href);
        url.searchParams.set('photo_id', photoId);
        window.history.pushState({ photoId: photoId }, '', url.toString());

        await window.loadPhoto(currentPhotoId);
        window.showLightbox();
    };

    window.showLightbox = function() {
        if (!lightbox) return;
        lightbox.classList.remove('hidden');
        document.body.style.overflow = 'hidden';
        
        // Register with BackButtonManager for mobile back button support
        // skipHistoryPush = true because openPhoto() already did pushState for URL
        if (window.BackButtonManager) {
            window.BackButtonManager.register('lightbox', window.closeLightbox, { 
                backState: { photoId: currentPhotoId },
                skipHistoryPush: true
            });
        }
    };

    window.closeLightbox = function() {
        if (!lightbox) return;
        
        // Unregister from BackButtonManager first
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('lightbox', true); // skipHistoryBack since we handle it below
        }
        
        // Cancel any pending image loads (including all network requests)
        cancelAllLoading();
        
        lightbox.classList.add('hidden');
        document.body.style.overflow = '';
        // Also close any open panels (skip refresh since lightbox is closing)
        window.closeTagEditor?.();
        window.closeAlbumEditor?.(true);
        // Clear album context
        window.clearAlbumContext();
        // Clear current photo ID
        window.currentLightboxPhotoId = null;
        
        // Remove photo_id from URL
        const url = new URL(window.location.href);
        if (url.searchParams.has('photo_id')) {
            url.searchParams.delete('photo_id');
            window.history.pushState({}, '', url.toString());
        }
    };

    window.closeLightboxWithAnimation = async function(direction = 'down') {
        if (!lightbox) return;
        
        const mediaContainer = lightbox.querySelector('.lightbox-media');
        const currentImg = mediaContainer?.querySelector('img, video');
        
        if (currentImg) {
            // Animate the image sliding out
            const containerHeight = mediaContainer.offsetHeight;
            const slideOutY = direction === 'down' ? containerHeight : -containerHeight;
            
            currentImg.style.transition = 'transform 0.25s ease-out, opacity 0.25s ease-out';
            currentImg.style.transform = `translateY(${slideOutY}px)`;
            currentImg.style.opacity = '0';
            
            // Wait for animation
            await new Promise(resolve => setTimeout(resolve, 250));
        }
        
        // Call the standard close function
        window.closeLightbox();
        
        // Reset styles after closing (for next open)
        if (currentImg) {
            currentImg.style.transition = '';
            currentImg.style.transform = '';
            currentImg.style.opacity = '';
        }
    };

    window.navigateLightbox = async function(direction, animate = false) {
        if (flatNavOrder.length === 0) return;
        
        const mediaContainer = lightbox?.querySelector('.lightbox-media');
        
        // Cancel any pending image load
        if (mediaContainer) {
            mediaContainer.dataset.loadingId = '';
        }
        
        // Abort any pending fetch
        if (currentFetchController) {
            currentFetchController.abort();
            currentFetchController = null;
        }
        
        // Abort any pending full image load
        if (currentFullImageLoader) {
            currentFullImageLoader.src = '';
            currentFullImageLoader = null;
        }
        
        let targetIndex = currentNavIndex + direction;
        
        // Wrap around
        if (targetIndex < 0) targetIndex = flatNavOrder.length - 1;
        if (targetIndex >= flatNavOrder.length) targetIndex = 0;
        
        let item = flatNavOrder[targetIndex];
        let attempts = 0;
        const maxAttempts = flatNavOrder.length;
        
        // Find next valid item (skip album_markers, expand placeholders)
        // Phase 5: supports polymorphic items (type: 'item')
        while (attempts < maxAttempts) {
            if (!item) break;
            
            if (item.type === 'item' || item.type === 'photo') {
                // Found an item - this is our target (Phase 5: 'item', legacy: 'photo')
                break;
            } else if (item.type === 'album_placeholder') {
                // Need to expand album
                const albumPhotos = await expandAlbumInNavOrder(item.albumId, targetIndex);
                if (albumPhotos && albumPhotos.length > 0) {
                    // Album expanded, get first/last photo based on direction
                    if (direction > 0) {
                        // Going forward - first photo of album
                        targetIndex = targetIndex + 1; // Skip marker, go to first photo
                        item = flatNavOrder[targetIndex];
                    } else {
                        // Going backward - last photo of album
                        targetIndex = targetIndex + albumPhotos.length;
                        item = flatNavOrder[targetIndex];
                    }
                    break;
                } else {
                    // Album empty or failed to load, skip it
                    targetIndex += direction;
                    if (targetIndex < 0) targetIndex = flatNavOrder.length - 1;
                    if (targetIndex >= flatNavOrder.length) targetIndex = 0;
                    item = flatNavOrder[targetIndex];
                }
            } else if (item.type === 'album_marker') {
                // Skip album markers, go to next item
                targetIndex += direction;
                if (targetIndex < 0) targetIndex = flatNavOrder.length - 1;
                if (targetIndex >= flatNavOrder.length) targetIndex = 0;
                item = flatNavOrder[targetIndex];
            }
            attempts++;
        }
        
        // Phase 5: support polymorphic items (type: 'item') and legacy (type: 'photo')
        if (!item || (item.type !== 'item' && item.type !== 'photo')) return;
        
        // Animation setup
        let currentImg = null;
        let slideOutX = 0;
        let slideInX = 0;
        
        if (animate && mediaContainer) {
            currentImg = mediaContainer.querySelector('img, video');
            if (currentImg) {
                const containerWidth = mediaContainer.offsetWidth;
                slideOutX = direction > 0 ? -containerWidth : containerWidth;
                slideInX = direction > 0 ? containerWidth : -containerWidth;
                
                // Apply transition to current image
                currentImg.style.transition = 'transform 0.2s ease-out';
                currentImg.style.transform = `translateX(${slideOutX}px)`;
                
                // Wait for animation
                await new Promise(resolve => setTimeout(resolve, 200));
            }
        }
        
        currentNavIndex = targetIndex;
        const newPhotoId = item.id;
        currentPhotoId = newPhotoId;
        
        // Check if this photo is part of an album
        if (item.albumId) {
            // Find all photos in this album and set context
            const albumPhotos = flatNavOrder.filter(p => p.albumId === item.albumId);
            const albumIndex = albumPhotos.findIndex(p => p.id === newPhotoId);
            
            albumContext = {
                photos: albumPhotos,
                index: albumIndex >= 0 ? albumIndex : 0,
                albumId: item.albumId
            };
        } else {
            window.clearAlbumContext();
        }
        
        // Load and display
        await window.loadPhoto(newPhotoId);
        
        // Animate new image in
        if (animate && slideInX !== 0) {
            const newImg = mediaContainer?.querySelector('img, video');
            if (newImg) {
                newImg.style.transition = 'none';
                newImg.style.transform = `translateX(${slideInX}px)`;
                
                // Force reflow
                newImg.offsetHeight;
                
                // Animate in
                newImg.style.transition = 'transform 0.2s ease-out';
                newImg.style.transform = 'translateX(0)';
            }
        }
        
        // Update album indicator if in album
        if (albumContext) {
            window.updateAlbumIndicator?.(albumContext.index, albumContext.photos.length);
        }
        
        // Update URL
        const url = new URL(window.location.href);
        url.searchParams.set('photo_id', newPhotoId);
        window.history.pushState({ photoId: newPhotoId }, '', url.toString());
    };

    window.loadPhoto = async function(photoId) {
        if (!lightbox) return;
        
        const mediaContainer = lightbox.querySelector('.lightbox-media');
        const datesEl = document.getElementById('lightbox-dates');
        const tagsEl = document.getElementById('lightbox-tags');
        const albumIndicator = document.getElementById('lightbox-album-indicator');
        const albumBars = document.getElementById('lightbox-album-bars');
        const editAlbumBtn = document.getElementById('lightbox-edit-album');
        const editTagsBtn = document.getElementById('lightbox-edit-tags');

        if (!mediaContainer) return;

        // Cancel any previous image loading
        cancelImageLoading();

        try {
            // Create new AbortController for this fetch
            currentFetchController = new AbortController();
            const signal = currentFetchController.signal;
            
            const resp = await fetch(`${getBaseUrl()}/api/photos/${photoId}`, { signal });
            if (!resp.ok) throw new Error('Failed to load photo');
            
            const photo = await resp.json();
            console.log('[lightbox] Raw photo data received:', JSON.stringify(photo).slice(0, 500));
            
            // Check if cancelled
            if (signal.aborted) {
                console.log('[lightbox] Photo fetch was cancelled, aborting render');
                return;
            }
            
            // Update current photo ID
            currentPhotoId = photoId;
            window.currentLightboxPhotoId = photoId;  // For tag editor compatibility
            
            // Render media using unified FileAccessService
            // This handles all encryption types (none, server-side, E2E/Safe) uniformly
            const mimeType = photo.content_type || (photo.media_type === 'video' ? 'video/mp4' : 'image/jpeg');
            const isE2E = !!photo.safe_id;
            
            if (photo.media_type === 'video') {
                // Videos: load directly via FileAccessService
                try {
                    const videoUrl = await FileAccessService.getFileUrl(photoId, { photo });
                    mediaContainer.innerHTML = `<video class="lightbox-video" controls autoplay src="${videoUrl}"></video>`;
                } catch (err) {
                    console.error('[lightbox] Failed to load video:', err);
                    mediaContainer.innerHTML = `<p>Error: ${isE2E ? 'Safe is locked' : 'Failed to load video'}</p>`;
                }
            } else {
                // Images: progressive loading with thumbnail, then full image
                const loadId = Date.now();
                mediaContainer.dataset.loadingId = loadId;
                
                // Start with thumbnail
                try {
                    const thumbUrl = await FileAccessService.getThumbnailUrl(photoId, { photo });
                    mediaContainer.innerHTML = `
                        <img class="lightbox-image" src="${thumbUrl}" alt="${escapeHtml(photo.original_name || '')}">
                    `;
                } catch (err) {
                    console.error('[lightbox] Failed to load thumbnail:', err);
                    mediaContainer.innerHTML = `<p>Error: ${isE2E ? 'Safe is locked' : 'Failed to load thumbnail'}</p>`;
                    return; // Don't try to load full image if thumbnail failed
                }
                
                // Load full image in background
                try {
                    const fullUrl = await FileAccessService.getFileUrl(photoId, { photo });
                    const fullImg = new Image();
                    currentFullImageLoader = fullImg;
                    
                    fullImg.onload = () => {
                        if (mediaContainer.dataset.loadingId == loadId && currentPhotoId === photoId && currentFullImageLoader === fullImg) {
                            currentFullImageLoader = null;
                            mediaContainer.innerHTML = `
                                <img class="lightbox-image" src="${fullUrl}" alt="${escapeHtml(photo.original_name || '')}">
                            `;
                        }
                    };
                    fullImg.onerror = () => {
                        if (currentFullImageLoader === fullImg) {
                            currentFullImageLoader = null;
                        }
                        console.warn('[lightbox] Failed to load full image, keeping thumbnail');
                    };
                    fullImg.src = fullUrl;
                } catch (err) {
                    console.warn('[lightbox] Failed to start full image load:', err);
                }
            }

            // Update info
            if (datesEl) {
                datesEl.textContent = photo.taken_at 
                    ? new Date(photo.taken_at).toLocaleDateString()
                    : new Date(photo.uploaded_at).toLocaleDateString();
            }

            if (tagsEl) {
                tagsEl.innerHTML = (photo.tags || []).map(tag => 
                    `<span class="tag" style="--tag-color: ${tag.color || '#6b7280'}">${escapeHtml(tag.tag || tag.name || tag)}</span>`
                ).join('');
            }

            // Debug album data
            console.log('[lightbox] Photo data:', photo);
            console.log('[lightbox] photo.album:', photo.album);

            // Album indicator - show if in album context
            if (albumContext) {
                if (albumIndicator) albumIndicator.classList.remove('hidden');
                if (albumBars) {
                    albumBars.innerHTML = albumContext.photos.map((p, i) => 
                        `<div class="album-bar ${i === albumContext.index ? 'active' : ''}"></div>`
                    ).join('');
                }
                if (editAlbumBtn) {
                    editAlbumBtn.classList.remove('hidden');
                    // Get album ID from first photo in album context
                    const albumId = albumContext.photos[0]?.albumId;
                    console.log('[lightbox] Album context mode, albumId:', albumId);
                    if (albumId) {
                        editAlbumBtn.onclick = function() {
                            if (typeof window.openAlbumEditor === 'function') {
                                window.openAlbumEditor(albumId);
                            }
                        };
                    }
                }
            } else if (photo.album) {
                // Photo is part of an album
                if (albumIndicator) albumIndicator.classList.remove('hidden');
                if (albumBars) {
                    albumBars.innerHTML = photo.album.photo_ids.map((id, i) => 
                        `<div class="album-bar ${i + 1 === photo.album.current ? 'active' : ''}"></div>`
                    ).join('');
                }
                if (editAlbumBtn) {
                    editAlbumBtn.classList.remove('hidden');
                    const albumId = photo.album.id;
                    console.log('[lightbox] Setting up edit album button for album:', albumId, 'photo.album:', photo.album);
                    if (!albumId) {
                        console.error('[lightbox] Album ID is undefined! photo.album:', photo.album);
                        editAlbumBtn.classList.add('hidden');
                    } else {
                        editAlbumBtn.onclick = function() {
                            console.log('[lightbox] Edit album button clicked, calling openAlbumEditor with:', albumId);
                            if (typeof window.openAlbumEditor === 'function') {
                                window.openAlbumEditor(albumId);
                            } else {
                                console.error('[lightbox] openAlbumEditor is not available');
                            }
                        };
                    }
                }
            } else {
                if (albumIndicator) albumIndicator.classList.add('hidden');
                if (editAlbumBtn) editAlbumBtn.classList.add('hidden');
            }

            // Edit tags button
            if (editTagsBtn) {
                editTagsBtn.onclick = () => window.openTagEditor?.(photoId);
            }

        } catch (err) {
            if (err.name === 'AbortError') {
                console.log('[lightbox] Photo load aborted (cancelled)');
                return;
            }
            console.error('Failed to load photo:', err);
            mediaContainer.innerHTML = '<p>Error loading photo</p>';
        } finally {
            // Clear fetch controller when done (unless aborted, which returns early)
            currentFetchController = null;
        }
    };

    window.updateAlbumIndicator = function(index, total) {
        const albumBars = document.getElementById('lightbox-album-bars');
        
        if (albumBars && albumContext) {
            albumBars.innerHTML = albumContext.photos.map((p, i) => 
                `<div class="album-bar ${i === index ? 'active' : ''}"></div>`
            ).join('');
        }
    };

    // Reload current photo (for refreshing tags after edit)
    window.reloadCurrentPhoto = function() {
        if (currentPhotoId) {
            window.loadPhoto(currentPhotoId);
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-lightbox.js] Loaded');
})();
