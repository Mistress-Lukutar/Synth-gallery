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

    window.openPhoto = async function(photoId) {
        if (!lightbox) init();
        if (!lightbox) return;
        
        // Clear album context when opening single photo
        window.clearAlbumContext();
        
        currentPhotoId = photoId;
        
        // Get all visible photos from gallery
        const gallery = document.getElementById('gallery');
        if (gallery) {
            currentPhotos = Array.from(gallery.querySelectorAll('.gallery-item[data-item-type="photo"]'))
                .map(item => ({
                    id: item.dataset.photoId,
                    safeId: item.dataset.safeId
                }));
        }
        
        currentIndex = currentPhotos.findIndex(p => p.id === photoId);
        if (currentIndex === -1) currentIndex = 0;

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
    };

    window.navigateLightbox = function(direction) {
        // If in album context, use album navigation
        if (albumContext) {
            let newIndex = albumContext.index + direction;
            if (newIndex < 0) newIndex = albumContext.photos.length - 1;
            if (newIndex >= albumContext.photos.length) newIndex = 0;
            
            albumContext.index = newIndex;
            const photo = albumContext.photos[newIndex];
            if (photo) {
                window.loadPhoto(photo.id);
                window.updateAlbumIndicator?.(newIndex, albumContext.photos.length);
            }
            return;
        }
        
        // Otherwise use gallery navigation
        if (currentPhotos.length <= 1) return;
        
        currentIndex += direction;
        if (currentIndex < 0) currentIndex = currentPhotos.length - 1;
        if (currentIndex >= currentPhotos.length) currentIndex = 0;
        
        const photo = currentPhotos[currentIndex];
        if (photo) {
            window.loadPhoto(photo.id);
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
            
            if (photo.media_type === 'video') {
                mediaContainer.innerHTML = `
                    <video class="lightbox-video" controls autoplay src="${getBaseUrl()}/uploads/${photoId}${ext}${photo.safe_id ? '?safe=' + photo.safe_id : ''}"></video>
                `;
            } else {
                mediaContainer.innerHTML = `
                    <img class="lightbox-image" src="${getBaseUrl()}/uploads/${photoId}${ext}${photo.safe_id ? '?safe=' + photo.safe_id : ''}" alt="${escapeHtml(photo.original_name || '')}">
                `;
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
