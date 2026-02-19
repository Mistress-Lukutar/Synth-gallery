/**
 * Navigation module - SPA navigation without page reload
 * Phase 2: Navigation Module
 */

(function() {
    // Current sort preference
    let currentSort = 'uploaded';

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
                updateSortUI(currentSort);
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
        
        const gallery = document.getElementById('gallery');
        const breadcrumbs = document.getElementById('folder-breadcrumbs');
        
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
        
        // Update breadcrumbs
        if (breadcrumbs) {
            if (data.breadcrumbs && data.breadcrumbs.length > 0) {
                let html = `<a href="${getBaseUrl()}/" onclick="event.preventDefault(); event.stopPropagation(); navigateToDefaultFolder(); return false;">Home</a>`;
                data.breadcrumbs.forEach((crumb, index) => {
                    const isLast = index === data.breadcrumbs.length - 1;
                    if (isLast) {
                        html += ` <span class="separator">/</span> <span>${escapeHtml(crumb.name)}</span>`;
                    } else {
                        html += ` <span class="separator">/</span> <a href="${getBaseUrl()}/?folder_id=${crumb.id}" onclick="event.preventDefault(); event.stopPropagation(); navigateToFolder('${crumb.id}'); return false;">${escapeHtml(crumb.name)}</a>`;
                    }
                });
                breadcrumbs.innerHTML = html;
                breadcrumbs.style.display = '';
            } else {
                breadcrumbs.innerHTML = '';
                breadcrumbs.style.display = 'none';
            }
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
                const coverId = album.cover_photo_id;
                const safeIdAttr = album.safe_id ? `data-safe-id="${album.safe_id}"` : '';
                const hasDims = album.thumb_width && album.thumb_height;
                const dimsAttr = hasDims ? `data-thumb-width="${album.thumb_width}" data-thumb-height="${album.thumb_height}"` : '';
                const aspectStyle = hasDims ? `style="aspect-ratio: ${album.thumb_width} / ${album.thumb_height};"` : '';
                html += `
                    <div class="gallery-item album-item" data-album-id="${album.id}" data-item-type="album"
                         ${coverId ? `data-cover-photo-id="${coverId}"` : ''}
                         ${dimsAttr}
                         ${safeIdAttr}>
                        <div class="gallery-link" onclick="openAlbum('${album.id}')" ${aspectStyle}>
                            ${coverId ? `
                                <div class="gallery-placeholder"></div>
                                <img src="${getBaseUrl()}/thumbnails/${coverId}.jpg" 
                                     alt="${escapeHtml(album.name)}"
                                     loading="lazy"
                                     onload="this.previousElementSibling.style.display='none'; this.style.opacity='1'; window.onGalleryImageLoad && window.onGalleryImageLoad(this);"
                                     style="opacity: 0;">
                            ` : `
                                <div class="album-placeholder">
                                    <span>Empty Album</span>
                                </div>
                            `}
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
                const dimsAttr = hasDims ? `data-thumb-width="${photo.thumb_width}" data-thumb-height="${photo.thumb_height}"` : '';
                const aspectStyle = hasDims ? `style="aspect-ratio: ${photo.thumb_width} / ${photo.thumb_height};"` : '';
                
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
                                <img src="${getBaseUrl()}/thumbnails/${photo.id}.jpg" 
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
        
        // Rebuild masonry
        if (typeof window.rebuildMasonry === 'function') {
            window.rebuildMasonry(true);
        }
        
        // Load safe thumbnails
        if (typeof loadSafeThumbnails === 'function') {
            loadSafeThumbnails();
        }
    };

    // Update sort UI
    function updateSortUI(sort) {
        const sortBtn = document.getElementById('sort-btn');
        if (sortBtn) {
            sortBtn.textContent = sort === 'taken' ? 'Sort: Date Taken' : 'Sort: Date Uploaded';
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
