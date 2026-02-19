/**
 * Upload module - File upload functionality
 * Phase 4: Upload Module
 */

(function() {
    const modal = document.getElementById('upload-modal');
    if (!modal) return; // Exit if not on gallery page

    const closeBtn = modal.querySelector('.close');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const previewContainer = document.getElementById('upload-preview-container');
    const preview = document.getElementById('upload-preview');
    const previewCount = document.getElementById('preview-count');
    const clearFilesBtn = document.getElementById('clear-files-btn');
    const submitBtn = document.getElementById('submit-upload-btn');
    const cancelBtn = document.getElementById('cancel-upload-btn');

    let selectedFiles = [];
    let isUploading = false;

    // Open upload modal
    window.openUploadModal = function() {
        modal.classList.remove('hidden');
    };

    function closeModal() {
        if (isUploading) return;
        modal.classList.add('hidden');
        resetForm();
    }

    function resetForm() {
        if (fileInput) fileInput.value = '';
        selectedFiles = [];
        if (previewContainer) previewContainer.classList.add('hidden');
        if (submitBtn) submitBtn.disabled = true;
    }

    // Event listeners
    if (closeBtn) closeBtn.onclick = closeModal;
    if (cancelBtn) cancelBtn.onclick = closeModal;

    modal.onclick = (e) => {
        if (e.target === modal && !isUploading) closeModal();
    };

    // Drag & drop
    if (dropZone) {
        dropZone.onclick = () => fileInput.click();

        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });

        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            addFiles(Array.from(e.dataTransfer.files));
        });
    }

    if (fileInput) {
        fileInput.onchange = () => {
            addFiles(Array.from(fileInput.files));
        };
    }

    if (clearFilesBtn) {
        clearFilesBtn.onclick = () => {
            selectedFiles = [];
            if (fileInput) fileInput.value = '';
            if (previewContainer) previewContainer.classList.add('hidden');
            if (submitBtn) submitBtn.disabled = true;
        };
    }

    function addFiles(files) {
        selectedFiles = selectedFiles.concat(files);
        renderPreview();
        if (submitBtn) submitBtn.disabled = false;
    }

    function renderPreview() {
        if (!preview || !previewCount) return;

        preview.innerHTML = '';
        selectedFiles.forEach(file => {
            const div = document.createElement('div');
            div.className = 'preview-item';
            div.textContent = file.name;
            preview.appendChild(div);
        });

        previewCount.textContent = `${selectedFiles.length} files selected`;
        previewContainer.classList.remove('hidden');
    }

    // Upload
    if (submitBtn) {
        submitBtn.onclick = async () => {
            if (selectedFiles.length === 0 || isUploading) return;

            isUploading = true;
            submitBtn.disabled = true;

            const folderId = window.currentFolderId || 'default';
            const formData = new FormData();
            formData.append('folder_id', folderId);
            selectedFiles.forEach(file => formData.append('files', file));

            try {
                const resp = await csrfFetch(`${getBaseUrl()}/upload`, {
                    method: 'POST',
                    body: formData
                });

                if (!resp.ok) throw new Error('Upload failed');

                closeModal();
                // Refresh gallery
                if (window.currentFolderId) {
                    navigateToFolder(window.currentFolderId, false);
                }
            } catch (err) {
                console.error('Upload error:', err);
                alert('Upload failed: ' + err.message);
            } finally {
                isUploading = false;
                submitBtn.disabled = false;
            }
        };
    }

    console.log('[upload.js] Loaded');
})();
