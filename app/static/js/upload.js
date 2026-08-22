/**
 * Upload module - File upload functionality
 * Handles file/folder upload, drag & drop, tags, albums
 */

(function() {
    // State
    let isUploading = false;
    let uploadMode = 'files';
    let folderFiles = [];
    let selectedFiles = [];
    let uploadedFileIds = []; // Track uploaded files for potential deletion
    let abortController = null; // For cancelling uploads
    // No fixed client-side upload size cap: the server uses a streaming
    // pipeline and supports arbitrarily large files (e.g. multi-GiB MKVs).
    
    // Element references (populated on init)
    let modal, closeBtn, dropZone, fileInput, folderInput;
    let filesDropText, folderDropText, previewContainer, preview, previewCount;
    let clearFilesBtn, uploadOptions, albumCheckbox;
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
        progressDiv = document.getElementById('upload-progress');
        progressFill = document.getElementById('progress-fill');
        progressText = document.getElementById('progress-text');
        cancelBtn = document.getElementById('cancel-upload-btn');
        submitBtn = document.getElementById('submit-upload-btn');

        setupEventListeners();
    }

    // Open modal
    window.openUploadModal = function() {
        if (modal) modal.classList.remove('hidden');
        
        // Register with BackButtonManager for mobile back button support
        if (window.BackButtonManager) {
            window.BackButtonManager.register('upload-modal', closeModal);
        }
    };

    async function closeModal() {
        // Abort ongoing upload
        if (isUploading && abortController) {
            abortController.abort();
            isUploading = false;
        }
        
        // Delete any uploaded files (even if upload completed, user may want to cancel)
        if (uploadedFileIds.length > 0) {
            if (progressText) progressText.textContent = 'Cleaning up...';
            
            for (const photoId of uploadedFileIds) {
                try {
                    const resp = await csrfFetch(`${getBaseUrl()}/api/items/${photoId}`, {
                        method: 'DELETE'
                    });
                    if (!resp.ok) {
                        console.error(`Failed to delete photo ${photoId}:`, resp.status);
                    }
                } catch (e) {
                    console.error('Failed to delete uploaded file:', e);
                }
            }
        }
        
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
        uploadedFileIds = [];
        abortController = null;
        if (preview) preview.innerHTML = '';
        if (previewContainer) previewContainer.classList.add('hidden');
        if (uploadOptions) uploadOptions.classList.add('hidden');
        if (progressDiv) progressDiv.classList.add('hidden');
        if (albumCheckbox) albumCheckbox.checked = false;
        if (submitBtn) submitBtn.disabled = true;
        if (progressFill) {
            progressFill.style.width = '0%';
            progressFill.classList.remove('indeterminate');
        }
        if (cancelBtn) cancelBtn.disabled = false;
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
        if (file.type) {
            if (file.type.startsWith('text/')) {
                // HTML/SVG texts are not note material; only the note formats apply
                return ['text/plain', 'text/markdown', 'text/csv', 'text/yaml', 'text/x-yaml'].includes(file.type);
            }
            if (file.type.startsWith('image/') ||
                file.type === 'video/mp4' ||
                file.type === 'video/webm' ||
                file.type === 'video/x-matroska' ||
                file.type === 'video/x-mkv' ||
                file.type === 'video/matroska' ||
                file.type === 'video/webp') {
                return true;
            }
            return ['application/json', 'application/yaml', 'application/x-yaml'].includes(file.type);
        }
        // Fallback to extension when the browser doesn't report a MIME type
        const ext = file.name.split('.').pop().toLowerCase();
        return ['jpg', 'jpeg', 'png', 'gif', 'webp', 'jxl', 'mp4', 'webm', 'mkv',
                'txt', 'md', 'json', 'csv', 'yaml', 'yml'].includes(ext);
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

    // Render file preview with remove overlay and drag-drop reorder
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
            wrapper.dataset.index = index;
            wrapper.style.cssText = 'position:relative;display:inline-block;margin:5px;cursor:grab;';
            wrapper.draggable = true;

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
            thumb.style.cssText = 'display:block;pointer-events:none;';

            const overlay = document.createElement('div');
            overlay.className = 'preview-overlay';
            overlay.style.cssText = `
                position: absolute; top: 0; left: 0; right: 0; bottom: 0;
                background: rgba(0, 0, 0, 0.5); display: flex;
                align-items: center; justify-content: center;
                opacity: 0; transition: opacity 0.2s; border-radius: 4px;
                z-index: 10;
            `;
            overlay.innerHTML = '<span style="color:white;font-size:24px;font-weight:bold;">&times;</span>';

            // Drag and drop handlers
            wrapper.ondragstart = (e) => {
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', index);
                wrapper.style.opacity = '0.5';
                wrapper.style.cursor = 'grabbing';
            };

            wrapper.ondragend = () => {
                wrapper.style.opacity = '1';
                wrapper.style.cursor = 'grab';
                // Remove all drop indicators
                document.querySelectorAll('.preview-drop-indicator').forEach(el => el.remove());
            };

            wrapper.ondragover = (e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                
                // Add visual indicator
                const rect = wrapper.getBoundingClientRect();
                const midpoint = rect.left + rect.width / 2;
                const isBefore = e.clientX < midpoint;
                
                // Remove other indicators
                document.querySelectorAll('.preview-drop-indicator').forEach(el => el.remove());
                
                const indicator = document.createElement('div');
                indicator.className = 'preview-drop-indicator';
                indicator.style.cssText = `
                    position: absolute;
                    top: 0;
                    bottom: 0;
                    width: 3px;
                    background: var(--accent);
                    z-index: 20;
                    ${isBefore ? 'left: -4px;' : 'right: -4px;'}
                `;
                wrapper.appendChild(indicator);
            };

            wrapper.ondragleave = () => {
                const indicator = wrapper.querySelector('.preview-drop-indicator');
                if (indicator) indicator.remove();
            };

            wrapper.ondrop = (e) => {
                e.preventDefault();
                const fromIndex = parseInt(e.dataTransfer.getData('text/plain'));
                const toIndex = index;
                
                if (fromIndex !== toIndex) {
                    reorderFiles(fromIndex, toIndex);
                }
            };

            wrapper.onmouseenter = () => overlay.style.opacity = '1';
            wrapper.onmouseleave = () => overlay.style.opacity = '0';
            overlay.onclick = (e) => {
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

    // Reorder files after drag-drop
    function reorderFiles(fromIndex, toIndex) {
        const file = selectedFiles.splice(fromIndex, 1)[0];
        selectedFiles.splice(toIndex, 0, file);
        renderFilePreview();
    }

    // Human-readable byte size (1 decimal below 100 units)
    function formatBytes(bytes) {
        if (!Number.isFinite(bytes) || bytes < 0) return '?';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        let value = bytes;
        let unit = 0;
        while (value >= 1024 && unit < units.length - 1) {
            value /= 1024;
            unit++;
        }
        return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
    }

    // Update the progress bar and text from byte counters.
    // Returns the clamped percentage so callers can restore it later.
    function updateUploadProgress(label, loaded, total) {
        const pct = total > 0 ? Math.min(100, (loaded / total) * 100) : 0;
        progressFill.classList.remove('indeterminate');
        progressFill.style.width = `${pct.toFixed(1)}%`;
        if (total > 0) {
            progressText.textContent =
                `${label} — ${formatBytes(loaded)} / ${formatBytes(total)} (${Math.floor(pct)}%)`;
        } else {
            progressText.textContent = label;
        }
        return pct;
    }

    // Indeterminate state: all bytes are sent, server is still processing
    // (probing, thumbnails, encryption, album creation).
    // Bar width stays at the byte-progress value (≈100%).
    function showProcessingState(label) {
        progressFill.classList.add('indeterminate');
        progressText.textContent = label;
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

    // Main upload handler
    async function handleUpload() {
        const targetFolderId = window.currentFolderId;

        if (!targetFolderId) {
            alert('No folder selected. Please navigate to a folder first.');
            return;
        }

        isUploading = true;
        uploadedFileIds = []; // Reset uploaded files tracker
        abortController = new AbortController();
        submitBtn.disabled = true;
        if (cancelBtn) cancelBtn.disabled = false; // Enable cancel button
        progressDiv.classList.remove('hidden');

        let uploadedIds = [];

        try {
            if (uploadMode === 'folder') {
                // Bulk folder upload
                if (!folderFiles.length) return;

                // Reject empty files early; size is otherwise uncapped
                // (server pipeline streams regardless of file size).
                const emptyFiles = folderFiles.filter(({ file }) => !file.size);
                if (emptyFiles.length > 0) {
                    const fileNames = emptyFiles.map(({ file }) => file.name).join(', ');
                    alert(`Empty file(s) cannot be uploaded: ${fileNames}`);
                    throw new Error('Empty files');
                }

                const formData = new FormData();
                const paths = [];

                folderFiles.forEach(({ file, relativePath }) => {
                    formData.append('files', file);
                    paths.push(relativePath);
                });

                formData.append('paths', JSON.stringify(paths));
                formData.append('folder_id', targetFolderId);

                const resp = await csrfUpload(`${getBaseUrl()}/upload-bulk`, formData, {
                    signal: abortController.signal,
                    onProgress: (e) => {
                        if (e.lengthComputable) {
                            updateUploadProgress(`Uploading ${folderFiles.length} files`, e.loaded, e.total);
                        }
                    },
                    onUploaded: () => showProcessingState('Processing on server...')
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

                const s = data.summary;
                let msg = `Uploaded: ${s.individual_photos} photos`;
                if (s.albums_created > 0) {
                    msg += `, ${s.albums_created} albums (${s.photos_in_albums} photos)`;
                }
                if (s.failed > 0) msg += ` | ${s.failed} failed`;
                if (s.skipped_nested > 0) msg += ` | ${s.skipped_nested} nested skipped`;
                progressFill.classList.remove('indeterminate');
                progressFill.style.width = '100%';
                progressText.textContent = msg;

            } else {
                // Regular file upload
                const files = selectedFiles;
                if (!files.length) return;

                // Reject empty files early; size is otherwise uncapped
                // (server pipeline streams regardless of file size).
                const emptyFiles = files.filter(f => !f.size);
                if (emptyFiles.length > 0) {
                    const fileNames = emptyFiles.map(f => f.name).join(', ');
                    alert(`Empty file(s) cannot be uploaded: ${fileNames}`);
                    throw new Error('Empty files');
                }

                const isAlbum = albumCheckbox.checked && files.length > 1;

                if (isAlbum) {
                    progressText.textContent = 'Uploading album...';

                    const formData = new FormData();
                    for (const file of files) {
                        formData.append('files', file);
                    }
                    formData.append('folder_id', targetFolderId);

                    const resp = await csrfUpload(`${getBaseUrl()}/upload-album`, formData, {
                        signal: abortController.signal,
                        onProgress: (e) => {
                            if (e.lengthComputable) {
                                updateUploadProgress('Uploading album', e.loaded, e.total);
                            }
                        },
                        onUploaded: () => showProcessingState('Processing on server...')
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
                    const items = data.items || [];
                    uploadedIds = items.map(p => p.id);
                    uploadedFileIds.push(...uploadedIds); // Track for potential deletion
                    progressFill.classList.remove('indeterminate');
                    progressFill.style.width = '100%';
                    progressText.textContent = `Album created with ${items.length} file${items.length === 1 ? '' : 's'}`;

                } else {
                    // Upload files in the order they appear in selectedFiles.
                    // Progress is aggregated across the whole batch by bytes.
                    const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
                    let sentBytes = 0;

                    for (let i = 0; i < files.length; i++) {
                        const file = files[i];
                        const formData = new FormData();
                        formData.append('file', file);
                        formData.append('folder_id', targetFolderId);

                        const resp = await csrfUpload(`${getBaseUrl()}/api/uploads`, formData, {
                            signal: abortController.signal,
                            onProgress: (e) => {
                                if (e.lengthComputable) {
                                    updateUploadProgress(
                                        `Uploading ${i + 1}/${files.length}: ${file.name}`,
                                        sentBytes + e.loaded,
                                        totalBytes
                                    );
                                }
                            }
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
                        uploadedFileIds.push(data.id); // Track for potential deletion
                        sentBytes += file.size;
                    }
                }

                progressFill.classList.remove('indeterminate');
                progressFill.style.width = '100%';
                progressText.textContent = 'Done!';
            }

            // Clear uploadedFileIds on success so they won't be deleted when closing
            uploadedFileIds = [];
            
            // Close and refresh
            setTimeout(() => {
                isUploading = false;
                submitBtn.disabled = false;
                if (cancelBtn) cancelBtn.disabled = false;
                closeModal();
                if (targetFolderId && typeof navigateToFolder === 'function') {
                    navigateToFolder(targetFolderId, false);
                } else {
                    location.reload();
                }
            }, 500);

        } catch (err) {
            if (progressFill) progressFill.classList.remove('indeterminate');
            if (err.name === 'AbortError') {
                progressText.textContent = 'Upload cancelled';
            } else {
                console.error('Upload error:', err);
                progressText.textContent = 'Upload failed: ' + err.message;
            }
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

})();
