/**
 * Gallery AI Tagging module
 * Queue selected items for AI tagging and poll progress.
 */

(function() {
    let pollInterval = null;
    let currentJobIds = [];
    let currentItemIds = [];

    function init() {
        const btn = document.getElementById('ai-tag-selected-btn');
        if (!btn) return;

        btn.addEventListener('click', () => {
            startAITagging();
        });
    }

    window.startAITagging = async function() {
        const photos = window.selectedPhotos ? Array.from(window.selectedPhotos) : [];
        const albums = window.selectedAlbums ? Array.from(window.selectedAlbums) : [];

        if (photos.length === 0 && albums.length === 0) {
            showToast('No items selected', true);
            return;
        }

        // Collect all item IDs from selected photos and albums
        const itemIdSet = new Set(photos);
        const albumItemSafeIds = new Map(); // itemId -> safe_id from album API

        // Fetch items from selected albums
        if (albums.length > 0) {
            const albumPromises = albums.map(albumId =>
                fetch(`${getBaseUrl()}/api/albums/${albumId}`)
                    .then(r => r.ok ? r.json() : null)
                    .catch(() => null)
            );
            const albumData = await Promise.all(albumPromises);
            for (const album of albumData) {
                if (album && album.items) {
                    for (const item of album.items) {
                        itemIdSet.add(item.id);
                        if (item.safe_id) {
                            albumItemSafeIds.set(item.id, item.safe_id);
                        }
                    }
                }
            }
        }

        // Filter out encrypted / safe items
        const eligibleIds = [];
        const skippedIds = [];
        for (const itemId of itemIdSet) {
            // Check if item came from an album with safe_id
            if (albumItemSafeIds.has(itemId)) {
                skippedIds.push(itemId);
                continue;
            }
            // Check DOM for selected photos
            const itemEl = document.querySelector(`[data-item-id="${itemId}"]`);
            if (itemEl && (itemEl.dataset.safeId || itemEl.dataset.isEncrypted === 'true')) {
                skippedIds.push(itemId);
            } else {
                eligibleIds.push(itemId);
            }
        }

        if (skippedIds.length > 0) {
            showToast(`${skippedIds.length} encrypted items skipped`, true);
        }

        if (eligibleIds.length === 0) {
            showToast('No eligible items for AI tagging', true);
            return;
        }

        try {
            const resp = await csrfFetch(`${getBaseUrl()}/api/ai/jobs`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ item_ids: eligibleIds })
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || 'Failed to create jobs');
            }

            const data = await resp.json();
            const jobs = data.jobs || [];
            currentJobIds = jobs.map(j => j.id);
            currentItemIds = eligibleIds;

            showToast(`${jobs.length} item${jobs.length !== 1 ? 's' : ''} queued for AI tagging`);

            // Start polling
            startPolling();
        } catch (err) {
            console.error('AI tagging error:', err);
            showToast('AI tagging failed: ' + err.message, true);
        }
    };

    function startPolling() {
        if (pollInterval) {
            clearInterval(pollInterval);
        }

        updateStatusUI();
        pollInterval = setInterval(pollProgress, 3000);
        // Immediate first poll
        pollProgress();
    }

    function stopPolling() {
        if (pollInterval) {
            clearInterval(pollInterval);
            pollInterval = null;
        }
        const statusEl = document.getElementById('ai-tag-status');
        if (statusEl) {
            statusEl.classList.add('hidden');
        }
    }

    async function pollProgress() {
        if (currentJobIds.length === 0) {
            stopPolling();
            return;
        }

        try {
            const idsParam = currentJobIds.join(',');
            const resp = await fetch(`${getBaseUrl()}/api/ai/jobs/progress?job_ids=${encodeURIComponent(idsParam)}`);
            if (!resp.ok) return;

            const stats = await resp.json();
            const done = (stats.completed || 0) + (stats.failed || 0);
            const total = stats.total || currentJobIds.length;

            updateStatusUI(done, total);

            if (done >= total) {
                stopPolling();
                const failed = stats.failed || 0;
                if (failed > 0) {
                    showToast(`AI tagging complete — ${stats.completed || 0} done, ${failed} failed`, true);
                } else {
                    showToast(`AI tagging complete — ${stats.completed || 0} tags added`);
                }

                // Refresh tags for current item if it was in the batch
                const currentItemId = window.currentLightboxPhotoId || window.editingItemId;
                if (currentItemId && currentItemIds.includes(currentItemId) && window.loadItemDetails) {
                    window.loadItemDetails(currentItemId);
                }

                currentJobIds = [];
                currentItemIds = [];
            }
        } catch (err) {
            console.error('Polling error:', err);
        }
    }

    function updateStatusUI(done, total) {
        const statusEl = document.getElementById('ai-tag-status');
        if (!statusEl) return;

        if (typeof done === 'number' && typeof total === 'number' && total > 0) {
            statusEl.textContent = `${done}/${total} processing...`;
            statusEl.classList.remove('hidden');
        } else if (currentJobIds.length > 0) {
            statusEl.textContent = 'processing...';
            statusEl.classList.remove('hidden');
        } else {
            statusEl.classList.add('hidden');
        }
    }

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
