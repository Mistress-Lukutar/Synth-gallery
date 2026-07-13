/**
 * File Access Service - Direct file access for thumbnails and full files
 * 
 * Server-side encrypted files are decrypted transparently by the server.
 * This service only resolves direct URLs and manages a metadata cache.
 * 
 * Usage:
 *   const url = await FileAccessService.getFileUrl(photoId);
 *   img.src = url;
 */
var FileAccessService = (function() {
    'use strict';

    // Cache for photo metadata to avoid repeated fetches
    const metadataCache = new Map();
    const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

    /**
     * Get base URL helper
     */
    function getBaseUrl() {
        return (typeof window.getBaseUrl === 'function') ? window.getBaseUrl() : '';
    }

    /**
     * Fetch photo metadata from server
     */
    async function fetchMetadata(photoId) {
        const cached = metadataCache.get(photoId);
        if (cached && Date.now() - cached.fetchedAt < CACHE_TTL) {
            return cached.data;
        }

        const response = await fetch(`${getBaseUrl()}/api/items/${photoId}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch metadata for ${photoId}: ${response.status}`);
        }

        const data = await response.json();
        metadataCache.set(photoId, { data, fetchedAt: Date.now() });
        return data;
    }

    // Public API
    return {
        /**
         * Get URL for viewing a file.
         * 
         * @param {string} photoId - Photo ID
         * @param {Object} options - Options
         * @param {Object} options.photo - Pre-fetched photo metadata (optional)
         * @returns {Promise<string>} - Direct URL to the file
         */
        async getFileUrl(photoId, options = {}) {
            return `${getBaseUrl()}/files/${photoId}`;
        },

        /**
         * Get URL for viewing a thumbnail.
         * 
         * @param {string} photoId - Photo ID  
         * @param {Object} options - Options
         * @param {Object} options.photo - Pre-fetched photo metadata (optional)
         * @returns {Promise<string>} - Direct URL for thumbnail
         */
        async getThumbnailUrl(photoId, options = {}) {
            return `${getBaseUrl()}/files/${photoId}/thumbnail`;
        },

        /**
         * Get thumbnail URL synchronously (for initial render).
         * 
         * @param {string} photoId - Photo ID
         * @param {Object} photo - Photo metadata
         * @returns {string} - Direct thumbnail URL
         */
        getThumbnailUrlSync(photoId, photo) {
            return `${getBaseUrl()}/files/${photoId}/thumbnail`;
        },

        /**
         * Resolve thumbnail for an element asynchronously.
         * 
         * For direct-served files, the src is already set; this is a no-op.
         * 
         * @param {HTMLImageElement} imgElement - Image element to update
         * @param {string} photoId - Photo ID
         * @param {Object} photo - Photo metadata
         * @returns {Promise<boolean>} - True if resolved successfully
         */
        async resolveThumbnail(imgElement, photoId, photo) {
            return true;
        },

        /**
         * Get photo metadata (with caching).
         * 
         * @param {string} photoId - Photo ID
         * @param {boolean} skipCache - Force refresh from server
         * @returns {Promise<Object>} - Photo metadata
         */
        async getPhotoMetadata(photoId, skipCache = false) {
            if (skipCache) {
                metadataCache.delete(photoId);
            }
            return fetchMetadata(photoId);
        },

        /**
         * Clear metadata cache.
         */
        clearCache() {
            metadataCache.clear();
        }
    };
})();

// Make global
window.FileAccessService = FileAccessService;
