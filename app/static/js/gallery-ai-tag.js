/**
 * Gallery AI Tagging module
 * Uses Server-Sent Events for progress instead of polling.
 */

(function() {
    let eventSource = null;
    let currentItemIds = [];

    function init() {
        const btn = document.getElementById('ai-tag-selected-btn');
        if (btn) {
            btn.addEventListener('click', () => {
                startAITagging();
            });
        }

        // On page load, check for active jobs and reconnect SSE
        checkActiveJobs();
    }

    async function checkActiveJobs() {
        try {
            const resp = await fetch(`${getBaseUrl()}/api/ai/jobs/active`);
            if (!resp.ok) return;
            const data = await resp.json();
            const jobs = data.jobs || [];
            if (jobs.length === 0) {
                hideSpinner();
                return;
            }

            const jobIds = jobs.map(j => j.id);
            currentItemIds = []; // Will be filled from progress events
            showSpinner();
            updateTooltip({ queue: jobs.length });
            connectSSE(jobIds);
        } catch (err) {
            console.error('Failed to check active jobs:', err);
        }
    }

    function showSpinner() {
        const el = document.getElementById('ai-tag-progress');
        if (el) el.classList.remove('hidden');
    }

    function hideSpinner() {
        const el = document.getElementById('ai-tag-progress');
        if (el) el.classList.add('hidden');
    }

    function connectSSE(jobIds) {
        if (eventSource) {
            eventSource.close();
        }

        const url = `${getBaseUrl()}/api/ai/jobs/events`;
        eventSource = new EventSource(url);

        eventSource.addEventListener('progress', (e) => {
            try {
                const data = JSON.parse(e.data);
                showSpinner();
                if (data.jobs) {
                    currentItemIds = data.jobs.map(j => j.item_id);
                }
                if (data.stats) {
                    updateTooltip(data.stats);
                }
            } catch (err) {
                console.error('SSE progress parse error:', err);
            }
        });

        eventSource.addEventListener('complete', (e) => {
            try {
                const stats = JSON.parse(e.data);
                hideSpinner();
                const failed = stats.failed || 0;
                if (failed > 0) {
                    showToast(`AI tagging complete — ${stats.completed || 0} done, ${failed} failed`, true);
                } else {
                    showToast(`AI tagging complete — ${stats.completed || 0} tags added`);
                }
                refreshCurrentItemTags();
            } catch (err) {
                console.error('SSE complete parse error:', err);
            }
            closeSSE();
        });

        eventSource.addEventListener('done', () => {
            hideSpinner();
            closeSSE();
        });

        eventSource.addEventListener('error', () => {
            // SSE connection error — will auto-retry or close
            console.warn('SSE connection error');
        });
    }

    function updateTooltip(stats) {
        const tooltip = document.getElementById('ai-tag-tooltip');
        if (!tooltip) return;
        const queue = stats.pending || stats.queue || 0;
        const processing = stats.processing || 0;
        const totalQueue = queue + processing;
        tooltip.textContent = `AI Tagging: queue ${totalQueue}`;
    }

    function closeSSE() {
        if (eventSource) {
            eventSource.close();
            eventSource = null;
        }
    }

    function refreshCurrentItemTags() {
        const currentItemId = window.currentLightboxPhotoId || window.editingItemId;
        if (currentItemId && currentItemIds.includes(currentItemId) && window.loadItemDetails) {
            window.loadItemDetails(currentItemId);
        }
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
        const albumItemSafeIds = new Map();

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
            if (albumItemSafeIds.has(itemId)) {
                skippedIds.push(itemId);
                continue;
            }
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
            currentItemIds = eligibleIds;

            showToast(`${jobs.length} item${jobs.length !== 1 ? 's' : ''} queued for AI tagging`);
            showSpinner();
            updateTooltip({ queue: jobs.length });

            // Connect to SSE for progress
            const jobIds = jobs.map(j => j.id);
            connectSSE(jobIds);
        } catch (err) {
            console.error('AI tagging error:', err);
            showToast('AI tagging failed: ' + err.message, true);
        }
    };

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
