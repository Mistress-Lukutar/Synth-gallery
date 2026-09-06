/**
 * Gallery Download - format/quality selection modal for batch downloads.
 *
 * Replaces the direct fetch previously done by gallery-selection.js:
 * the download button now opens this modal, the user picks a target
 * format (Original/as-stored default, JXL/JPEG/PNG/WebP) with per-format
 * settings, and the confirm button performs the
 * POST /api/items/batch-download request.
 */

(function() {
    'use strict';

    let modal = null;
    let confirmBtn = null;
    let summaryEl = null;

    const FORMAT_BLOCKS = {
        jxl: 'download-fmt-jxl',
        jpeg: 'download-fmt-jpeg',
        png: 'download-fmt-png',
        webp: 'download-fmt-webp'
    };

    let isDownloading = false;

    function init() {
        modal = document.getElementById('download-modal');
        if (!modal) return;

        confirmBtn = document.getElementById('download-confirm-btn');
        summaryEl = document.getElementById('download-summary');

        confirmBtn.addEventListener('click', startDownload);

        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeDownloadModal();
        });

        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
                closeDownloadModal();
            }
        });

        document.querySelectorAll('input[name="download-format"]').forEach((radio) => {
            radio.addEventListener('change', updateFormatBlocks);
        });

        const jpegQuality = document.getElementById('download-jpeg-quality');
        jpegQuality.addEventListener('input', () => {
            document.getElementById('download-jpeg-quality-value').textContent = jpegQuality.value;
        });

        const webpQuality = document.getElementById('download-webp-quality');
        webpQuality.addEventListener('input', () => {
            document.getElementById('download-webp-quality-value').textContent = webpQuality.value;
        });

        const webpLossless = document.getElementById('download-webp-lossless');
        webpLossless.addEventListener('change', () => {
            webpQuality.disabled = webpLossless.checked;
        });
    }

    function currentFormat() {
        const checked = document.querySelector('input[name="download-format"]:checked');
        return checked ? checked.value : 'original';
    }

    function updateFormatBlocks() {
        const fmt = currentFormat();
        Object.entries(FORMAT_BLOCKS).forEach(([f, id]) => {
            document.getElementById(id).classList.toggle('hidden', f !== fmt);
        });
    }

    // Count selected standalone items by media type using the DOM dataset
    function buildSummary() {
        const photos = window.selectedPhotos || new Set();
        const albums = window.selectedAlbums || new Set();

        let images = 0;
        let videos = 0;
        photos.forEach((id) => {
            const el = document.querySelector(`.gallery-item[data-item-id="${id}"]`);
            if (el && el.dataset.mediaType === 'video') {
                videos++;
            } else {
                images++;
            }
        });

        const parts = [];
        if (images > 0) parts.push(`${images} image${images > 1 ? 's' : ''}`);
        if (videos > 0) parts.push(`${videos} video${videos > 1 ? 's' : ''}`);
        if (albums.size > 0) parts.push(`${albums.size} album${albums.size > 1 ? 's' : ''}`);

        summaryEl.textContent = `Selected: ${parts.join(', ')}`;
    }

    function collectOptions() {
        const fmt = currentFormat();
        const options = { format: fmt };

        if (fmt === 'jpeg') {
            options.jpeg_quality = parseInt(document.getElementById('download-jpeg-quality').value, 10);
        } else if (fmt === 'png') {
            options.png_optimize = document.getElementById('download-png-optimize').checked;
        } else if (fmt === 'webp') {
            options.webp_quality = parseInt(document.getElementById('download-webp-quality').value, 10);
            options.webp_lossless = document.getElementById('download-webp-lossless').checked;
        } else if (fmt === 'jxl') {
            options.jxl_effort = parseInt(document.getElementById('download-jxl-effort').value, 10);
        }

        return options;
    }

    function filenameFromResponse(resp) {
        const cd = resp.headers.get('Content-Disposition') || '';

        // RFC 5987 UTF-8 name first, ASCII fallback second
        const utf8Match = cd.match(/filename\*=UTF-8''([^;]+)/i);
        if (utf8Match) {
            try {
                return decodeURIComponent(utf8Match[1].trim());
            } catch (e) { /* fall through */ }
        }
        const quoted = cd.match(/filename="([^"]+)"/i);
        if (quoted) return quoted[1];
        const plain = cd.match(/filename=([^;]+)/i);
        return plain ? plain[1].trim() : null;
    }

    function fallbackFilename(blob) {
        if (blob.type === 'application/zip') return `photos-${Date.now()}.zip`;
        const ext = (blob.type.split('/')[1] || 'bin').split('+')[0];
        return `photo-${Date.now()}.${ext}`;
    }

    function saveBlob(blob, filename) {
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    }

    async function startDownload() {
        if (isDownloading) return;

        const itemIds = Array.from(window.selectedPhotos || []);
        const albumIds = Array.from(window.selectedAlbums || []);
        if (itemIds.length === 0 && albumIds.length === 0) return;

        isDownloading = true;
        const originalHTML = confirmBtn.innerHTML;
        confirmBtn.disabled = true;
        confirmBtn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18" class="spinner"><circle cx="12" cy="12" r="10" stroke-dasharray="60" stroke-dashoffset="20"/></svg>';

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/items/batch-download`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    item_ids: itemIds,
                    album_ids: albumIds,
                    options: collectOptions()
                })
            });

            if (!resp.ok) {
                let detail = `HTTP ${resp.status}`;
                try {
                    const err = await resp.json();
                    if (err && err.detail) detail = err.detail;
                } catch (e) { /* keep status detail */ }
                throw new Error(detail);
            }

            const blob = await resp.blob();
            saveBlob(blob, filenameFromResponse(resp) || fallbackFilename(blob));
            closeDownloadModal();
        } catch (err) {
            console.error('Download error:', err);
            showToast('Download failed: ' + err.message, true);
        } finally {
            isDownloading = false;
            confirmBtn.disabled = false;
            confirmBtn.innerHTML = originalHTML;
        }
    }

    window.openDownloadModal = function() {
        if (!modal) return;
        const photos = window.selectedPhotos || new Set();
        const albums = window.selectedAlbums || new Set();
        if (photos.size === 0 && albums.size === 0) return;

        buildSummary();
        updateFormatBlocks();
        modal.classList.remove('hidden');

        if (window.BackButtonManager) {
            window.BackButtonManager.register('download-modal', closeDownloadModal);
        }
    };

    window.closeDownloadModal = function() {
        if (!modal) return;
        modal.classList.add('hidden');

        if (window.BackButtonManager) {
            window.BackButtonManager.unregister('download-modal', true);
        }
    };

    document.addEventListener('DOMContentLoaded', init);
})();
