/**
 * Upload module - File upload functionality
 * Handles file/folder upload, drag & drop, encryption for safes, tags, albums
 */

(function() {
    // State
    let isUploading = false;
    let uploadMode = 'files';
    let folderFiles = [];
    let selectedFiles = [];
    const MAX_FILE_SIZE = 1024 * 1024 * 1024; // 1GB
    
    // Element references (populated on init)
    let modal, closeBtn, dropZone, fileInput, folderInput;
    let filesDropText, folderDropText, previewContainer, preview, previewCount;
    let clearFilesBtn, uploadOptions, albumCheckbox, autoAiTagsCheckbox, tagsInput;
    let progressDiv, progressFill, progressText, cancelBtn, submitBtn;

    // Initialize when DOM is ready
    function init() {
        modal = document.getElementById('upload-modal');
        if (!modal) return; // Not on gallery page

        closeBtn = modal.querySelector('.close');
        dropZone = document.getElementById('drop-zone');
        fileInput = document.getElementById('file-input');
        folderInput = document.getElementById('folder-input');
        filesDropText = document.getElementById('files-drop-text');
        folderDropText = document.getElementById('folder-drop-text');
        previewContainer = document.getElementById('upload-preview-container');
        preview = document.getElementById('upload-preview');
        previewCount = document.getElementById('preview-count');
        clearFilesBtn = document.getElementById('clear-files-btn');
        uploadOptions = document.getElementById('upload-options');
        albumCheckbox = document.getElementById('upload-as-album');
        autoAiTagsCheckbox = document.getElementById('auto-ai-tags');
        tagsInput = document.getElementById('upload-tags-input');
        progressDiv = document.getElementById('upload-progress');
        progressFill = document.getElementById('progress-fill');
        progressText = document.getElementById('progress-text');
        cancelBtn = document.getElementById('cancel-upload-btn');
        submitBtn = document.getElementById('submit-upload-btn');

        setupEventListeners();
        console.log('[upload.js] Initialized');
    }

    // Open modal
    window.openUploadModal = function() {
        if (modal) modal.classList.remove('hidden');
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('upload-modal', closeModal);
        }
    };

    function closeModal() {
        if (isUploading) return;
        
        // Unregister from BackButtonManager first (skipHistoryBack = true for button clicks)
        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('upload-modal', true);
        }
        
        if (modal) modal.classList.add('hidden');
        resetUploadForm();
    }

    function resetUploadForm() {
        if (fileInput) fileInput.value = '';
        if (folderInput) folderInput.value = '';
        folderFiles = [];
        selectedFiles = [];
        if (preview) preview.innerHTML = '';
        if (previewContainer) previewContainer.classList.add('hidden');
        if (uploadOptions) uploadOptions.classList.add('hidden');
        if (progressDiv) progressDiv.classList.add('hidden');
        if (albumCheckbox) albumCheckbox.checked = false;
        if (autoAiTagsCheckbox) autoAiTagsCheckbox.checked = false;
        if (tagsInput) tagsInput.value = '';
        if (submitBtn) submitBtn.disabled = true;
        if (progressFill) progressFill.style.width = '0%';
        setUploadMode('files');
    }

    function setupEventListeners() {
        // Event listeners
        if (closeBtn) closeBtn.onclick = closeModal;
        if (cancelBtn) cancelBtn.onclick = closeModal;

        if (modal) {
            modal.onclick = (e) => {
                if (e.target === modal && !isUploading) closeModal();
            };
        }

        // Escape is handled by BackButtonManager

        // Open modal via folder upload button
        const folderUploadBtn = document.getElementById('folder-upload-btn');
        if (folderUploadBtn) {
            folderUploadBtn.onclick = () => {
                if (modal) modal.classList.remove('hidden');
            };
        }

        // Upload mode tabs
        document.querySelectorAll('.upload-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                setUploadMode(tab.dataset.mode);
            });
        });

        // Drop zone click
        if (dropZone) {
            dropZone.onclick = (e) => {
                if (e.target.tagName === 'INPUT') return;
                if (uploadMode === 'folder' && folderInput) {
                    folderInput.click();
                } else if (fileInput) {
                    fileInput.click();
                }
            };
        }

        if (clearFilesBtn) {
            clearFilesBtn.onclick = () => {
                if (fileInput) fileInput.value = '';
                if (folderInput) folderInput.value = '';
                folderFiles = [];
                selectedFiles = [];
                renderFilePreview();
            };
        }

        // File input
        if (fileInput) {
            fileInput.onchange = () => {
                addFilesToSelection(fileInput.files);
                fileInput.value = '';
            };
        }

        // Folder input
        if (folderInput) {
            folderInput.onchange = () => {
                const files = Array.from(folderInput.files);
                processFolderFiles(files);
            };
        }

        // Drag & drop
        if (dropZone) {
            dropZone.ondragover = (e) => {
                e.preventDefault();
                dropZone.classList.add('drag-over');
            };
            dropZone.ondragleave = () => dropZone.classList.remove('drag-over');
            dropZone.ondrop = (e) => {
                e.preventDefault();
                dropZone.classList.remove('drag-over');
                addFilesToSelection(e.dataTransfer.files);
            };
        }

        // Submit
        if (submitBtn) {
            submitBtn.onclick = handleUpload;
        }
    }

    function setUploadMode(mode) {
        uploadMode = mode;
        document.querySelectorAll('.upload-tab').forEach(t => {
            t.classList.toggle('active', t.dataset.mode === mode);
        });

        if (mode === 'folder') {
            if (filesDropText) filesDropText.classList.add('hidden');
            if (folderDropText) folderDropText.classList.remove('hidden');
            if (albumCheckbox) albumCheckbox.closest('.upload-option').style.display = 'none';
        } else {
            if (filesDropText) filesDropText.classList.remove('hidden');
            if (folderDropText) folderDropText.classList.add('hidden');
            if (albumCheckbox) albumCheckbox.closest('.upload-option').style.display = '';
        }

        // Clear selection when switching modes
        if (fileInput) fileInput.value = '';
        if (folderInput) folderInput.value = '';
        folderFiles = [];
        selectedFiles = [];
        if (preview) preview.innerHTML = '';
        if (previewContainer) previewContainer.classList.add('hidden');
        if (uploadOptions) uploadOptions.classList.add('hidden');
        if (submitBtn) submitBtn.disabled = true;
    }

    // Check valid media file
    function isValidMedia(file) {
        return file.type.startsWith('image/') ||
               file.type === 'video/mp4' ||
               file.type === 'video/webm';
    }

    // Add files to selection (accumulates)
    function addFilesToSelection(files) {
        const mediaFiles = Array.from(files).filter(isValidMedia);
        if (mediaFiles.length === 0) return;

        for (const file of mediaFiles) {
            const isDuplicate = selectedFiles.some(f => f.name === file.name && f.size === file.size);
            if (!isDuplicate) {
                selectedFiles.push(file);
            }
        }
        renderFilePreview();
    }

    // Render file preview with remove overlay
    function renderFilePreview() {
        if (!preview || !previewCount) return;

        if (selectedFiles.length === 0) {
            previewContainer.classList.add('hidden');
            uploadOptions.classList.add('hidden');
            submitBtn.disabled = true;
            return;
        }

        preview.innerHTML = '';
        selectedFiles.forEach((file, index) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'preview-item';
            wrapper.style.cssText = 'position:relative;display:inline-block;margin:5px;cursor:pointer;';

            let thumb;
            if (file.type.startsWith('video/')) {
                thumb = document.createElement('video');
                thumb.src = URL.createObjectURL(file);
                thumb.muted = true;
                thumb.preload = 'metadata';
            } else {
                thumb = document.createElement('img');
                thumb.src = URL.createObjectURL(file);
            }
            thumb.className = 'preview-thumb';
            thumb.title = file.name;
            thumb.style.cssText = 'display:block;';

            const overlay = document.createElement('div');
            overlay.className = 'preview-overlay';
            overlay.style.cssText = `
                position: absolute; top: 0; left: 0; right: 0; bottom: 0;
                background: rgba(0, 0, 0, 0.5); display: flex;
                align-items: center; justify-content: center;
                opacity: 0; transition: opacity 0.2s; border-radius: 4px;
            `;
            overlay.innerHTML = '<span style="color:white;font-size:24px;font-weight:bold;">&times;</span>';

            wrapper.onmouseenter = () => overlay.style.opacity = '1';
            wrapper.onmouseleave = () => overlay.style.opacity = '0';
            wrapper.onclick = (e) => {
                e.stopPropagation();
                removeFile(index);
            };

            wrapper.appendChild(thumb);
            wrapper.appendChild(overlay);
            preview.appendChild(wrapper);
        });

        previewCount.textContent = `${selectedFiles.length} file${selectedFiles.length > 1 ? 's' : ''} selected`;
        previewContainer.classList.remove('hidden');
        uploadOptions.classList.remove('hidden');
        submitBtn.disabled = false;

        const albumOption = albumCheckbox.closest('.upload-option');
        if (selectedFiles.length > 1) {
            albumOption.style.display = '';
        } else {
            albumOption.style.display = 'none';
            albumCheckbox.checked = false;
        }
    }

    function removeFile(index) {
        selectedFiles.splice(index, 1);
        renderFilePreview();
    }

    function processFolderFiles(files) {
        folderFiles = [];
        const groups = { '__root__': [] };

        files.forEach(file => {
            if (!isValidMedia(file)) return;
            const path = file.webkitRelativePath;
            const parts = path.split('/');

            if (parts.length === 2) {
                folderFiles.push({ file, relativePath: parts[1] });
                groups['__root__'].push(file);
            } else if (parts.length === 3) {
                const albumName = parts[1];
                folderFiles.push({ file, relativePath: `${parts[1]}/${parts[2]}` });
                if (!groups[albumName]) groups[albumName] = [];
                groups[albumName].push(file);
            }
        });

        if (folderFiles.length === 0) {
            previewContainer.classList.add('hidden');
            uploadOptions.classList.add('hidden');
            submitBtn.disabled = true;
            return;
        }

        preview.innerHTML = '';

        const summaryDiv = document.createElement('div');
        summaryDiv.className = 'folder-summary';
        const rootCount = groups['__root__'].length;
        const albumNames = Object.keys(groups).filter(k => k !== '__root__');

        let summaryHtml = '<div class="folder-structure">';
        if (rootCount > 0) {
            summaryHtml += `<div class="folder-item-preview">📄 ${rootCount} individual photo${rootCount > 1 ? 's' : ''}</div>`;
        }
        albumNames.forEach(name => {
            const count = groups[name].length;
            summaryHtml += `<div class="folder-item-preview">📁 "${name}" (${count} photo${count > 1 ? 's' : ''})</div>`;
        });
        summaryHtml += '</div>';
        summaryDiv.innerHTML = summaryHtml;
        preview.appendChild(summaryDiv);

        const thumbsDiv = document.createElement('div');
        thumbsDiv.className = 'preview-thumbs';
        const previewFiles = folderFiles.slice(0, 8);
        previewFiles.forEach(({ file }) => {
            if (file.type.startsWith('video/')) {
                const video = document.createElement('video');
                video.src = URL.createObjectURL(file);
                video.className = 'preview-thumb';
                video.title = file.name;
                video.muted = true;
                video.preload = 'metadata';
                thumbsDiv.appendChild(video);
            } else {
                const img = document.createElement('img');
                img.src = URL.createObjectURL(file);
                img.className = 'preview-thumb';
                img.title = file.name;
                thumbsDiv.appendChild(img);
            }
        });
        if (folderFiles.length > 8) {
            const moreDiv = document.createElement('div');
            moreDiv.className = 'preview-more';
            moreDiv.textContent = `+${folderFiles.length - 8} more`;
            thumbsDiv.appendChild(moreDiv);
        }
        preview.appendChild(thumbsDiv);

        const skippedNested = files.filter(f => f.webkitRelativePath.split('/').length > 3 && isValidMedia(f)).length;
        let countText = `${folderFiles.length} file${folderFiles.length > 1 ? 's' : ''} selected`;
        if (skippedNested > 0) {
            countText += ` (${skippedNested} nested skipped)`;
        }
        previewCount.textContent = countText;
        previewContainer.classList.remove('hidden');
        uploadOptions.classList.remove('hidden');
        submitBtn.disabled = false;
        albumCheckbox.closest('.upload-option').style.display = 'none';
    }

    // Get safe_id for folder
    function getFolderSafeId(folderId) {
        if (!folderId || typeof folderTree === 'undefined') return null;
        const folder = folderTree.find(f => f.id === folderId);
        return folder ? folder.safe_id : null;
    }

    // Encrypt file for safe
    async function encryptFileForSafeUpload(file, safeId) {
        if (!SafeCrypto.isUnlocked(safeId)) {
            throw new Error('Safe is locked. Please unlock it first.');
        }
        return await SafeCrypto.encryptFileForSafe(file, safeId);
    }

    // Get or create root folder for safe
    async function getSafeRootFolder(safeId) {
        const safeFolders = folderTree.filter(f => f.safe_id === safeId && !f.parent_id);
        if (safeFolders.length > 0) {
            return safeFolders[0].id;
        }

        const resp = await csrfFetch(`${getBaseUrl()}/api/folders`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'Root', safe_id: safeId })
        });

        if (!resp.ok) {
            throw new Error('Failed to create safe root folder');
        }

        const data = await resp.json();
        if (typeof loadFolderTree === 'function') {
            await loadFolderTree();
        }
        return data.folder_id;
    }

    // Main upload handler
    async function handleUpload() {
        let targetFolderId = window.currentFolderId;
        let targetSafeId = null;

        if (!targetFolderId && window.currentSafeId) {
            try {
                targetFolderId = await getSafeRootFolder(window.currentSafeId);
                targetSafeId = window.currentSafeId;
            } catch (e) {
                alert('Failed to prepare safe folder: ' + e.message);
                return;
            }
        }

        if (!targetFolderId) {
            alert('No folder selected. Please navigate to a folder first.');
            return;
        }

        if (!targetSafeId) {
            targetSafeId = getFolderSafeId(targetFolderId);
        }
        if (targetSafeId && !SafeCrypto.isUnlocked(targetSafeId)) {
            alert('This folder is in a locked safe. Please unlock the safe first.');
            return;
        }

        isUploading = true;
        submitBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = true;
        progressDiv.classList.remove('hidden');

        const useAiTags = autoAiTagsCheckbox.checked;
        const manualTags = tagsInput.value.trim()
            .split(',')
            .map(t => t.trim().toLowerCase())
            .filter(t => t.length > 0);

        let uploadedIds = [];

        try {
            if (uploadMode === 'folder') {
                // Bulk folder upload
                if (!folderFiles.length) return;

                const oversizedFiles = folderFiles.filter(({ file }) => file.size > MAX_FILE_SIZE);
                if (oversizedFiles.length > 0) {
                    const maxSizeMB = (MAX_FILE_SIZE / 1024 / 1024).toFixed(0);
                    const fileNames = oversizedFiles.map(({ file }) => file.name).join(', ');
                    alert(`File(s) too large (max ${maxSizeMB}MB): ${fileNames}`);
                    throw new Error('Files too large');
                }

                if (targetSafeId) {
                    alert('Folder upload is not supported in safes. Please upload files individually.');
                    throw new Error('Folder upload not supported in safes');
                }

                progressText.textContent = `Uploading ${folderFiles.length} files...`;
                progressFill.style.width = '30%';

                const formData = new FormData();
                const paths = [];

                folderFiles.forEach(({ file, relativePath }) => {
                    formData.append('files', file);
                    paths.push(relativePath);
                });

                formData.append('paths', JSON.stringify(paths));
                formData.append('folder_id', targetFolderId);

                progressFill.style.width = '50%';
                const resp = await csrfFetch(`${getBaseUrl()}/upload-bulk`, {
                    method: 'POST',
                    body: formData
                });

                if (!resp.ok) {
                    if (resp.status === 413) {
                        throw new Error('File too large. Please increase client_max_body_size in nginx config or upload smaller files.');
                    }
                    const contentType = resp.headers.get('content-type');
                    if (contentType && contentType.includes('application/json')) {
                        const err = await resp.json();
                        throw new Error(err.detail || 'Upload failed');
                    } else {
                        const text = await resp.text();
                        throw new Error(`Upload failed: HTTP ${resp.status}`);
                    }
                }

                const data = await resp.json();
                progressFill.style.width = '90%';

                const s = data.summary;
                let msg = `Uploaded: ${s.individual_photos} photos`;
                if (s.albums_created > 0) {
                    msg += `, ${s.albums_created} albums (${s.photos_in_albums} photos)`;
                }
                if (s.failed > 0) msg += ` | ${s.failed} failed`;
                if (s.skipped_nested > 0) msg += ` | ${s.skipped_nested} nested skipped`;
                progressText.textContent = msg;
                progressFill.style.width = '100%';

            } else {
                // Regular file upload
                const files = selectedFiles;
                if (!files.length) return;

                const oversizedFiles = files.filter(f => f.size > MAX_FILE_SIZE);
                if (oversizedFiles.length > 0) {
                    const maxSizeMB = (MAX_FILE_SIZE / 1024 / 1024).toFixed(0);
                    const fileNames = oversizedFiles.map(f => f.name).join(', ');
                    alert(`File(s) too large (max ${maxSizeMB}MB): ${fileNames}`);
                    throw new Error('Files too large');
                }

                const isAlbum = albumCheckbox.checked && files.length > 1;

                if (isAlbum) {
                    if (targetSafeId) {
                        alert('Albums are not supported in safes. Please upload files individually or uncheck "Create album".');
                        throw new Error('Albums not supported in safes');
                    }

                    progressText.textContent = 'Uploading album...';
                    progressFill.style.width = '50%';

                    const formData = new FormData();
                    for (const file of files) {
                        formData.append('files', file);
                    }
                    formData.append('folder_id', targetFolderId);

                    const resp = await csrfFetch(`${getBaseUrl()}/upload-album`, {
                        method: 'POST',
                        body: formData
                    });

                    if (!resp.ok) {
                        const contentType = resp.headers.get('content-type');
                        if (contentType && contentType.includes('application/json')) {
                            const err = await resp.json();
                            throw new Error(err.detail || 'Upload failed');
                        } else {
                            throw new Error(`Upload failed: HTTP ${resp.status}`);
                        }
                    }

                    const data = await resp.json();
                    uploadedIds = data.photos.map(p => p.id);
                    progressFill.style.width = '100%';

                } else {
                    for (let i = 0; i < files.length; i++) {
                        progressText.textContent = `Uploading ${i + 1}/${files.length}...`;
                        progressFill.style.width = `${((i + 1) / files.length) * 100}%`;

                        const formData = new FormData();

                        if (targetSafeId) {
                            try {
                                const encrypted = await encryptFileForSafeUpload(files[i], targetSafeId);
                                const encryptedFile = new File([encrypted.encryptedFile], files[i].name, {
                                    type: 'application/octet-stream'
                                });
                                formData.append('file', encryptedFile);
                                formData.append('encrypted_ck', 'safe');

                                if (encrypted.encryptedThumbnail) {
                                    const thumbFile = new File([encrypted.encryptedThumbnail], 'thumb.jpg.encrypted', {
                                        type: 'application/octet-stream'
                                    });
                                    formData.append('thumbnail', thumbFile);
                                    formData.append('thumb_width', encrypted.thumbWidth || 0);
                                    formData.append('thumb_height', encrypted.thumbHeight || 0);
                                }
                            } catch (encryptErr) {
                                throw new Error(`Encryption failed: ${encryptErr.message}`);
                            }
                        } else {
                            formData.append('file', files[i]);
                        }

                        formData.append('folder_id', targetFolderId);

                        const resp = await csrfFetch(`${getBaseUrl()}/upload`, {
                            method: 'POST',
                            body: formData
                        });

                        if (!resp.ok) {
                            if (resp.status === 413) {
                                throw new Error('File too large. Please increase client_max_body_size in nginx config.');
                            }
                            const contentType = resp.headers.get('content-type');
                            if (contentType && contentType.includes('application/json')) {
                                const err = await resp.json();
                                throw new Error(err.detail || 'Upload failed');
                            } else {
                                throw new Error(`Upload failed: HTTP ${resp.status}`);
                            }
                        }

                        const data = await resp.json();
                        uploadedIds.push(data.id);
                    }
                }

                // Apply AI tags
                if (useAiTags && uploadedIds.length > 0) {
                    progressText.textContent = 'Generating AI tags...';
                    await csrfFetch(`${getBaseUrl()}/api/photos/batch-ai-tags`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ photo_ids: uploadedIds })
                    });
                }

                // Apply manual tags
                if (manualTags.length > 0 && uploadedIds.length > 0) {
                    progressText.textContent = 'Applying tags...';
                    for (const photoId of uploadedIds) {
                        for (const tag of manualTags) {
                            await csrfFetch(`${getBaseUrl()}/api/photos/${photoId}/tag`, {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ tag: tag, category_id: 6 })
                            });
                        }
                    }
                }

                progressText.textContent = 'Done!';
            }

            // Close and refresh
            setTimeout(() => {
                isUploading = false;
                submitBtn.disabled = false;
                if (cancelBtn) cancelBtn.disabled = false;
                closeModal();
                if (targetFolderId && typeof navigateToFolder === 'function') {
                    navigateToFolder(targetFolderId, false);
                } else if (window.currentSafeId && typeof navigateToSafe === 'function') {
                    navigateToSafe(window.currentSafeId, false);
                } else {
                    location.reload();
                }
            }, 500);

        } catch (err) {
            console.error('Upload error:', err);
            progressText.textContent = 'Upload failed: ' + err.message;
            isUploading = false;
            submitBtn.disabled = false;
            if (cancelBtn) cancelBtn.disabled = false;
        }
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    console.log('[upload.js] Loaded');
})();
