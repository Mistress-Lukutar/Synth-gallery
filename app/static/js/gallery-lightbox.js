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

    function init() {
        lightbox = document.getElementById('lightbox');
        if (!lightbox) {
            console.log('[gallery-lightbox] No lightbox element');
            return;
        }
        setupEventListeners();
        console.log('[gallery-lightbox] Initialized');
    }

    function setupEventListeners() {
        // Keyboard navigation
        document.addEventListener('keydown', (e) => {
            if (lightbox.classList.contains('hidden')) return;
            
            if (e.key === 'Escape') {
                window.closeLightbox();
            } else if (e.key === 'ArrowLeft') {
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
    window.rebuildLightboxNavOrder = async function() {
        flatNavOrder = await buildFlatNavOrder();
        console.log('[lightbox] Nav order rebuilt:', flatNavOrder.length, 'items');
    };

    // Get navigation order from sessionStorage (set by masonry)
    function getGalleryNavOrder() {
        try {
            const stored = sessionStorage.getItem('galleryNavOrder');
            if (stored) {
                return JSON.parse(stored);
            }
        } catch (e) {
            console.warn('[lightbox] Failed to load nav order:', e);
        }
        return null;
    }
    
    // Build flat navigation list from gallery order
    // Expands albums into their photos for seamless navigation
    async function buildFlatNavOrder() {
        const order = getGalleryNavOrder();
        if (!order || order.length === 0) {
            // Fallback: get photos from DOM
            const gallery = document.getElementById('gallery');
            if (gallery) {
                return Array.from(gallery.querySelectorAll('.gallery-item[data-item-type="photo"]'))
                    .map(item => ({
                        type: 'photo',
                        id: item.dataset.photoId,
                        safeId: item.dataset.safeId
                    }));
            }
            return [];
        }
        
        // For albums, we need to fetch their photos to expand them in navigation
        const flatOrder = [];
        for (const item of order) {
            if (item.type === 'album') {
                // Album - fetch its photos and add them as a group
                try {
                    const resp = await fetch(`${getBaseUrl()}/api/albums/${item.id}`);
                    if (resp.ok) {
                        const album = await resp.json();
                        if (album.photos && album.photos.length > 0) {
                            // Add album marker first
                            flatOrder.push({
                                type: 'album_marker',
                                id: item.id,
                                albumName: album.name
                            });
                            // Add all album photos
                            for (const photo of album.photos) {
                                flatOrder.push({
                                    type: 'photo',
                                    id: photo.id,
                                    safeId: photo.safe_id,
                                    albumId: item.id
                                });
                            }
                        }
                    }
                } catch (e) {
                    console.warn('[lightbox] Failed to load album:', item.id);
                }
            } else if (item.type === 'photo') {
                // Standalone photo
                const gallery = document.getElementById('gallery');
                const el = gallery?.querySelector(`.gallery-item[data-photo-id="${item.id}"]`);
                if (el) {
                    flatOrder.push({
                        type: 'photo',
                        id: item.id,
                        safeId: el.dataset.safeId
                    });
                }
            }
        }
        return flatOrder;
    }
    
    // Store flat navigation order
    let flatNavOrder = [];
    let currentNavIndex = -1;

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
        
        // Build flat navigation order (photos + expanded albums)
        flatNavOrder = await buildFlatNavOrder();
        
        // Find current photo in flat order
        currentNavIndex = flatNavOrder.findIndex(p => p.id === photoId && p.type === 'photo');
        if (currentNavIndex === -1) {
            // Photo not in order, add it
            flatNavOrder = [{ type: 'photo', id: photoId, safeId: galleryItem?.dataset.safeId }];
            currentNavIndex = 0;
        }
        
        // Check if this photo is part of an album
        const currentItem = flatNavOrder[currentNavIndex];
        if (currentItem && currentItem.albumId) {
            // Find all photos in this album
            const albumPhotos = flatNavOrder.filter(p => p.albumId === currentItem.albumId);
            const albumIndex = albumPhotos.findIndex(p => p.id === photoId);
            
            // Set album context
            albumContext = {
                photos: albumPhotos,
                index: albumIndex >= 0 ? albumIndex : 0,
                albumId: currentItem.albumId
            };
        } else {
            window.clearAlbumContext();
        }
        
        currentPhotoId = photoId;

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
    };

    window.closeLightbox = function() {
        if (!lightbox) return;
        lightbox.classList.add('hidden');
        document.body.style.overflow = '';
        // Also close any open panels
        window.closeTagEditor?.();
        window.closeAlbumEditor?.();
        // Clear album context
        window.clearAlbumContext();
        
        // Remove photo_id from URL
        const url = new URL(window.location.href);
        if (url.searchParams.has('photo_id')) {
            url.searchParams.delete('photo_id');
            window.history.pushState({}, '', url.toString());
        }
    };

    window.navigateLightbox = async function(direction) {
        if (flatNavOrder.length === 0) return;
        
        let newPhotoId = null;
        
        // If in album context, try to navigate within album first
        if (albumContext && albumContext.photos.length > 0) {
            let newAlbumIndex = albumContext.index + direction;
            
            // Check if we're still within album bounds
            if (newAlbumIndex >= 0 && newAlbumIndex < albumContext.photos.length) {
                // Navigate within album
                albumContext.index = newAlbumIndex;
                const photo = albumContext.photos[newAlbumIndex];
                if (photo) {
                    newPhotoId = photo.id;
                    currentPhotoId = newPhotoId;
                    currentNavIndex = flatNavOrder.findIndex(p => p.id === newPhotoId && p.type === 'photo');
                    await window.loadPhoto(newPhotoId);
                    window.updateAlbumIndicator?.(newAlbumIndex, albumContext.photos.length);
                    
                    // Update URL
                    const url = new URL(window.location.href);
                    url.searchParams.set('photo_id', newPhotoId);
                    window.history.pushState({ photoId: newPhotoId }, '', url.toString());
                    return;
            }
            }
            
            // Reached end of album, exit to next/prev item in gallery
            // Clear album context and continue to gallery navigation
            window.clearAlbumContext();
        }
        
        // Navigate in flat gallery order
        let newNavIndex = currentNavIndex + direction;
        
        // Wrap around
        if (newNavIndex < 0) newNavIndex = flatNavOrder.length - 1;
        if (newNavIndex >= flatNavOrder.length) newNavIndex = 0;
        
        currentNavIndex = newNavIndex;
        const nextItem = flatNavOrder[currentNavIndex];
        
        if (!nextItem) return;
        
        if (nextItem.type === 'photo') {
            newPhotoId = nextItem.id;
            
            // Check if this photo is part of an album
            if (nextItem.albumId) {
                // Find all photos in this album
                const albumPhotos = flatNavOrder.filter(p => p.albumId === nextItem.albumId);
                const albumIndex = albumPhotos.findIndex(p => p.id === newPhotoId);
                
                // Set album context for internal album navigation
                albumContext = {
                    photos: albumPhotos,
                    index: albumIndex >= 0 ? albumIndex : 0,
                    albumId: nextItem.albumId
                };
            }
            
            currentPhotoId = newPhotoId;
            await window.loadPhoto(newPhotoId);
            
            // Update album indicator if in album
            if (albumContext) {
                window.updateAlbumIndicator?.(albumContext.index, albumContext.photos.length);
            }
            
        } else if (nextItem.type === 'album_marker') {
            // This shouldn't happen as we navigate to photos, not markers
            // But if it does, skip to next item
            return window.navigateLightbox(direction);
        }
        
        // Update URL with new photo_id
        if (newPhotoId) {
            const url = new URL(window.location.href);
            url.searchParams.set('photo_id', newPhotoId);
            window.history.pushState({ photoId: newPhotoId }, '', url.toString());
        }
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

        try {
            const resp = await fetch(`${getBaseUrl()}/api/photos/${photoId}`);
            if (!resp.ok) throw new Error('Failed to load photo');
            
            const photo = await resp.json();
            
            // Render media - use original extension from filename
            let ext = '.jpg';
            if (photo.filename) {
                const match = photo.filename.match(/\.([^.]+)$/);
                if (match) ext = '.' + match[1].toLowerCase();
            } else if (photo.original_name) {
                const match = photo.original_name.match(/\.([^.]+)$/);
                if (match) ext = '.' + match[1].toLowerCase();
            }
            
            // Progressive loading: thumbnail first, then full image
            const isSafeFile = photo.safe_id && typeof SafeCrypto !== 'undefined' && SafeCrypto.isUnlocked && SafeCrypto.isUnlocked(photo.safe_id);
            
            if (photo.media_type === 'video') {
                // Videos don't use progressive loading
                if (isSafeFile) {
                    try {
                        const fileResp = await fetch(`${getBaseUrl()}/uploads/${photoId}${ext}?safe=${photo.safe_id}`);
                        if (!fileResp.ok) throw new Error('Failed to fetch encrypted file');
                        
                        const encryptedBlob = await fileResp.blob();
                        const decryptedBlob = await SafeCrypto.decryptFileFromSafe(
                            encryptedBlob,
                            photo.safe_id,
                            'video/mp4'
                        );
                        const objectUrl = URL.createObjectURL(decryptedBlob);
                        mediaContainer.innerHTML = `<video class="lightbox-video" controls autoplay src="${objectUrl}"></video>`;
                    } catch (err) {
                        console.error('[lightbox] Decrypt failed:', err);
                        mediaContainer.innerHTML = '<p>Error: Failed to decrypt video</p>';
                    }
                } else {
                    mediaContainer.innerHTML = `<video class="lightbox-video" controls autoplay src="${getBaseUrl()}/uploads/${photoId}${ext}"></video>`;
                }
            } else {
                // Images: show thumbnail first, then load full image
                const thumbUrl = isSafeFile 
                    ? null // Safe thumbnails handled differently
                    : `${getBaseUrl()}/thumbnails/${photoId}.jpg`;
                const fullUrl = isSafeFile
                    ? `${getBaseUrl()}/uploads/${photoId}${ext}?safe=${photo.safe_id}`
                    : `${getBaseUrl()}/uploads/${photoId}${ext}`;
                
                // Start with thumbnail
                if (isSafeFile) {
                    // For safe files, show loading state until decrypted
                    mediaContainer.innerHTML = `
                        <div class="lightbox-loading">
                            <div class="loading-spinner"></div>
                            <p>Decrypting...</p>
                        </div>
                    `;
                    
                    try {
                        const fileResp = await fetch(fullUrl);
                        if (!fileResp.ok) throw new Error('Failed to fetch encrypted file');
                        
                        const encryptedBlob = await fileResp.blob();
                        const mimeType = `image/${ext.slice(1) || 'jpeg'}`;
                        const decryptedBlob = await SafeCrypto.decryptFileFromSafe(
                            encryptedBlob,
                            photo.safe_id,
                            mimeType
                        );
                        const objectUrl = URL.createObjectURL(decryptedBlob);
                        
                        mediaContainer.innerHTML = `
                            <img class="lightbox-image" src="${objectUrl}" alt="${escapeHtml(photo.original_name || '')}">
                        `;
                    } catch (err) {
                        console.error('[lightbox] Decrypt failed:', err);
                        mediaContainer.innerHTML = '<p>Error: Failed to decrypt image</p>';
                    }
                } else {
                    // Regular files: progressive loading with thumbnail
                    mediaContainer.innerHTML = `
                        <img class="lightbox-image" src="${thumbUrl}" alt="${escapeHtml(photo.original_name || '')}">
                    `;
                    
                    // Load full image in background
                    const fullImg = new Image();
                    fullImg.onload = () => {
                        mediaContainer.innerHTML = `
                            <img class="lightbox-image" src="${fullUrl}" alt="${escapeHtml(photo.original_name || '')}">
                        `;
                    };
                    fullImg.onerror = () => {
                        // Keep thumbnail on error
                        console.warn('[lightbox] Failed to load full image, keeping thumbnail');
                    };
                    fullImg.src = fullUrl;
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

            // Album indicator - show if in album context
            if (albumContext) {
                if (albumIndicator) albumIndicator.classList.remove('hidden');
                if (albumBars) {
                    albumBars.innerHTML = albumContext.photos.map((p, i) => 
                        `<div class="album-bar ${i === albumContext.index ? 'active' : ''}" onclick="window.loadPhoto('${p.id}'); window.setAlbumContext(albumContext.photos, ${i});"></div>`
                    ).join('');
                }
                if (editAlbumBtn) editAlbumBtn.classList.remove('hidden');
            } else if (photo.album) {
                // Photo is part of an album
                if (albumIndicator) albumIndicator.classList.remove('hidden');
                if (albumBars) {
                    albumBars.innerHTML = photo.album.photo_ids.map((id, i) => 
                        `<div class="album-bar ${i + 1 === photo.album.current ? 'active' : ''}" onclick="window.loadPhoto('${id}')"></div>`
                    ).join('');
                }
                if (editAlbumBtn) {
                    editAlbumBtn.classList.remove('hidden');
                    editAlbumBtn.onclick = () => window.openAlbumEditor?.(photo.album.id);
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
            console.error('Failed to load photo:', err);
            mediaContainer.innerHTML = '<p>Error loading photo</p>';
        }
    };

    window.updateAlbumIndicator = function(index, total) {
        const albumBars = document.getElementById('lightbox-album-bars');
        
        if (albumBars && albumContext) {
            albumBars.innerHTML = albumContext.photos.map((p, i) => 
                `<div class="album-bar ${i === index ? 'active' : ''}" onclick="window.loadPhoto('${p.id}'); window.setAlbumContext(albumContext.photos, ${i});"></div>`
            ).join('');
        }
    };

    // Init on DOM ready
    document.addEventListener('DOMContentLoaded', () => {
        init();
    });

    console.log('[gallery-lightbox.js] Loaded');
})();
