/**
 * Navigation module - SPA navigation without page reload
 * Phase 2: Navigation Module
 */

(function() {
    // Current sort preference
    let currentSort = 'uploaded';
    
    // Expose to window for masonry sorting
    window.currentSortMode = currentSort;

    // Navigate to folder via SPA
    window.navigateToFolder = async function(folderId, pushState = true, event = null) {
        console.log('[SPA] Navigating to folder:', folderId);
        
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        
        const gallery = document.getElementById('gallery');
        if (gallery) {
            gallery.style.opacity = '0.5';
        }
        
        try {
            const resp = await fetch(`${getBaseUrl()}/api/folders/${folderId}/content`);
            if (!resp.ok) {
                throw new Error('Failed to load folder');
            }
            
            const data = await resp.json();
            console.log('[SPA] Loaded folder content:', data);
            
            if (pushState) {
                history.pushState({ folderId: folderId }, '', `${getBaseUrl()}/?folder_id=${folderId}`);
            }
            
            window.currentFolderId = folderId;
            
            if (data.sort) {
                currentSort = data.sort;
                window.currentSortMode = currentSort;
                updateSortUI(currentSort);
                // Also update dropdown UI if available
                if (typeof window.updateSortDropdownUI === 'function') {
                    window.updateSortDropdownUI(currentSort);
                }
            }
            
            renderFolderContent(data);
            
            if (typeof updateSidebarActiveState === 'function') {
                updateSidebarActiveState(folderId);
            }
            
            if (typeof loadFolderTree === 'function') {
                loadFolderTree();
            }
            
            // Reset folder header buttons
            const folderHeader = document.querySelector('.folder-header');
            if (folderHeader && data.folder) {
                const shareBtn = folderHeader.querySelector('button[title="Share"]');
                const editBtn = folderHeader.querySelector('button[title^="Edit"]');
                const sortBtn = folderHeader.querySelector('#sort-btn');
                
                if (sortBtn) sortBtn.style.display = '';
                
                const userId = window.SYNTH_USER_ID;
                const canShare = data.folder.user_id === userId;
                const canEdit = data.folder.permission === 'owner' || data.folder.permission === 'editor';
                
                if (shareBtn) shareBtn.style.display = canShare ? '' : 'none';
                if (editBtn) editBtn.style.display = canEdit ? '' : 'none';
                
                if (shareBtn) shareBtn.setAttribute('onclick', `openShareModal('${folderId}')`);
                if (editBtn) editBtn.setAttribute('onclick', `openEditFolder('${folderId}')`);
            }
            
            window.currentSafeId = null;
            
        } catch (err) {
            console.error('[SPA] Navigation failed:', err);
            window.location.href = `${getBaseUrl()}/?folder_id=${folderId}`;
        }
        
        return false;
    };

    // Navigate to default folder
    window.navigateToDefaultFolder = async function() {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/user/default-folder`);
            if (!resp.ok) throw new Error('Failed to get default folder');
            const data = await resp.json();
            if (data.folder_id) {
                navigateToFolder(data.folder_id);
            }
        } catch (err) {
            console.error('Failed to navigate to default folder:', err);
        }
    };

    // Render folder content in gallery
    window.renderFolderContent = function(data) {
        console.log('[renderFolderContent] Rendering folder:', data.folder?.name, 'items:', data.items?.length);
        
        // Update sort UI if sort data is available
        if (data.sort) {
            window.currentSortMode = data.sort;
            if (typeof window.updateSortDropdownUI === 'function') {
                window.updateSortDropdownUI(data.sort);
            }
        }
        
        const gallery = document.getElementById('gallery');
        
        if (!gallery) {
            console.error('[renderFolderContent] Gallery element not found!');
            return;
        }
        
        // Update folder header
        const folderHeader = document.querySelector('.folder-header');
        if (folderHeader && data.folder) {
            const folderNameEl = folderHeader.querySelector('h2');
            if (folderNameEl) {
                folderNameEl.textContent = data.folder.name;
            }
            
            const uploadBtn = folderHeader.querySelector('#folder-upload-btn');
            if (uploadBtn) {
                const canUpload = data.folder.permission === 'owner' || data.folder.permission === 'editor';
                uploadBtn.style.display = canUpload ? '' : 'none';
            }
            
            const shareBtn = folderHeader.querySelector('button[title="Share"]');
            const editBtn = folderHeader.querySelector('button[title^="Edit"]');
            const userId = window.SYNTH_USER_ID;
            if (shareBtn) shareBtn.style.display = data.folder.user_id === userId ? '' : 'none';
            if (editBtn) editBtn.style.display = data.folder.user_id === userId ? '' : 'none';
            
            if (shareBtn) shareBtn.setAttribute('onclick', `openShareModal('${data.folder.id}')`);
            if (editBtn) editBtn.setAttribute('onclick', `openEditFolder('${data.folder.id}')`);
        }
        
        // Handle subfolders
        const subfoldersSection = document.getElementById('subfolders-section');
        if (data.subfolders && data.subfolders.length > 0) {
            let html = '<div class="subfolders-grid">';
            data.subfolders.forEach(folder => {
                html += `
                    <a href="${getBaseUrl()}/?folder_id=${folder.id}" class="subfolder-tile" data-folder-id="${folder.id}" onclick="event.preventDefault(); event.stopPropagation(); navigateToFolder('${folder.id}'); return false;">
                        <svg class="subfolder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        </svg>
                        <span class="subfolder-name">${escapeHtml(folder.name)}</span>
                        <span class="subfolder-count">${folder.photo_count || 0}</span>
                    </a>
                `;
            });
            html += '</div>';
            subfoldersSection.innerHTML = html;
            subfoldersSection.style.display = '';
        } else if (subfoldersSection) {
            subfoldersSection.innerHTML = '';
            subfoldersSection.style.display = 'none';
        }
        
        // Build gallery HTML
        let html = '';
        const items = data.items || [];
        
        items.forEach(item => {
            if (item.type === 'album') {
                const album = item;
                const coverId = album.cover_photo_id || album.effective_cover_photo_id;
                const safeId = album.safe_id;
                const safeIdAttr = safeId ? `data-safe-id="${safeId}"` : '';
                
                // Use cover_thumb dimensions if available (v0.8.5 style), otherwise fallback to thumb dimensions
                const thumbWidth = album.cover_thumb_width || album.thumb_width;
                const thumbHeight = album.cover_thumb_height || album.thumb_height;
                const hasDims = thumbWidth && thumbHeight;
                
                // Default to square for albums without cover/dimensions
                const finalWidth = hasDims ? thumbWidth : 280;
                const finalHeight = hasDims ? thumbHeight : 280;
                const dimsAttr = `data-thumb-width="${finalWidth}" data-thumb-height="${finalHeight}"`;
                const aspectStyle = `style="aspect-ratio: ${finalWidth} / ${finalHeight};"`;
                
                // Handle safe thumbnails like photos
                let imgHtml;
                if (safeId && coverId) {
                    imgHtml = `
                        <div class="gallery-placeholder"></div>
                        <img data-safe-thumbnail="${coverId}"
                             data-safe-id="${safeId}"
                             alt="${escapeHtml(album.name)}"
                             loading="lazy"
                             onload="this.previousElementSibling.style.display='none'; this.style.opacity='1'; window.onGalleryImageLoad && window.onGalleryImageLoad(this);"
                             style="opacity: 0;">
                    `;
                } else if (coverId) {
                    imgHtml = `
                        <div class="gallery-placeholder"></div>
                        <img src="${getBaseUrl()}/thumbnails/${coverId}.jpg" 
                             alt="${escapeHtml(album.name)}"
                             loading="lazy"
                             onload="this.previousElementSibling.style.display='none'; this.style.opacity='1'; window.onGalleryImageLoad && window.onGalleryImageLoad(this);"
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
                
                // Add date attributes for sorting
                const uploadedAt = album.uploaded_at || '';
                const takenAt = album.taken_at || '';
                
                html += `
                    <div class="gallery-item album-item" data-album-id="${album.id}" data-item-type="album"
                         ${coverId ? `data-cover-photo-id="${coverId}"` : ''}
                         ${dimsAttr}
                         ${safeIdAttr}
                         data-uploaded-at="${uploadedAt}"
                         data-taken-at="${takenAt}">
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
                
                // Use stored dimensions or default to 4:3 aspect ratio
                const hasDims = photo.thumb_width && photo.thumb_height;
                const finalWidth = hasDims ? photo.thumb_width : 280;
                const finalHeight = hasDims ? photo.thumb_height : 210;
                const dimsAttr = `data-thumb-width="${finalWidth}" data-thumb-height="${finalHeight}"`;
                const aspectStyle = `style="aspect-ratio: ${finalWidth} / ${finalHeight};"`;
                
                // Add date attributes for sorting
                const uploadedAt = photo.uploaded_at || '';
                const takenAt = photo.taken_at || '';
                const dateAttrs = `data-uploaded-at="${uploadedAt}" data-taken-at="${takenAt}"`;
                
                if (safeId) {
                    html += `
                        <div class="gallery-item" 
                             data-photo-id="${photo.id}"
                             data-item-type="photo"
                             data-media-type="${mediaType}"
                             ${dimsAttr}
                             data-safe-id="${safeId}"
                             ${dateAttrs}>
                            <div class="gallery-link" onclick="openPhoto('${photo.id}')" ${aspectStyle}>
                                <div class="gallery-placeholder"></div>
                                <img data-safe-thumbnail="${photo.id}"
                                     data-safe-id="${safeId}"
                                     alt="${escapeHtml(photo.original_name)}"
                                     loading="lazy"
                                     onload="this.previousElementSibling.style.display='none'; this.style.opacity='1'; window.onGalleryImageLoad && window.onGalleryImageLoad(this);"
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
                             ${safeIdAttr}
                             ${dateAttrs}>
                            <div class="gallery-link" onclick="openPhoto('${photo.id}')" ${aspectStyle}>
                                <div class="gallery-placeholder"></div>
                                <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" 
                                     alt="${escapeHtml(photo.original_name)}"
                                     loading="lazy"
                                     onload="this.previousElementSibling.style.display='none'; this.style.opacity='1'; window.onGalleryImageLoad && window.onGalleryImageLoad(this);"
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
                    <p>No photos yet</p>
                    <p>Click "Upload" to add your first photos</p>
                </div>
            `;
        }
        
        gallery.innerHTML = html;
        gallery.style.opacity = '1';
        
        // Update allItems for masonry (gallery.html compatibility)
        window.allItems = Array.from(gallery.querySelectorAll('.gallery-item'));
        
        // Rebuild masonry after DOM update
        console.log('[navigation] About to schedule rebuildMasonry, window.rebuildMasonry:', typeof window.rebuildMasonry);
        setTimeout(() => {
            console.log('[navigation] In setTimeout, window.rebuildMasonry:', typeof window.rebuildMasonry);
            if (typeof window.rebuildMasonry === 'function') {
                console.log('[navigation] Calling rebuildMasonry, items:', gallery.querySelectorAll('.gallery-item').length);
                window.rebuildMasonry(true);
            } else {
                console.log('[navigation] rebuildMasonry not available');
            }
        }, 10);
        
        // Load safe thumbnails (will be defined in safes.js Phase 6)
        if (typeof window.loadSafeThumbnails === 'function') {
            window.loadSafeThumbnails();
        }
    };

    // Update sort UI - update tooltip only, SVG icon stays unchanged
    function updateSortUI(sort) {
        const sortBtn = document.getElementById('sort-btn');
        if (sortBtn) {
            const sortLabel = sort === 'taken' ? 'Sort: Date Taken' : 'Sort: Date Uploaded';
            sortBtn.setAttribute('title', sortLabel);
        }
    }

    // Handle browser back/forward
    window.addEventListener('popstate', (event) => {
        if (event.state && event.state.folderId) {
            navigateToFolder(event.state.folderId, false);
        } else {
            navigateToDefaultFolder();
        }
    });

    console.log('[navigation.js] Loaded');
})();
