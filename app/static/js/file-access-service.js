/**
 * File Access Service - Unified file access for all encryption types
 * 
 * This service provides a single interface for accessing files regardless of
 * encryption type (none, server-side, or E2E/Safe).
 * 
 * Usage:
 *   const url = await FileAccessService.getFileUrl(photoId);
 *   img.src = url;
 *   
 *   // Cleanup blob URLs when done:
 *   FileAccessService.revokeUrl(url);
 * 
 * Architecture:
 * - Regular files: Returns direct URL (/files/{id})
 * - Server-side encrypted: Server decrypts, client gets plaintext
 * - E2E encrypted (Safes): Client fetches encrypted, decrypts with SafeCrypto,
 *   creates Blob URL
 */
const FileAccessService = (function() {
    'use strict';

    // Cache for photo metadata to avoid repeated fetches
    const metadataCache = new Map();
    const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

    // Track created blob URLs for cleanup
    const blobUrls = new Set();

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

        const response = await fetch(`${getBaseUrl()}/api/photos/${photoId}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch metadata for ${photoId}: ${response.status}`);
        }

        const data = await response.json();
        metadataCache.set(photoId, { data, fetchedAt: Date.now() });
        return data;
    }

    /**
     * Determine encryption type from metadata
     */
    function getEncryptionType(photo) {
        if (photo.safe_id) return 'e2e';
        // Note: server-side encryption is handled transparently by server
        return 'none';
    }

    /**
     * Fetch and decrypt E2E file
     */
    async function fetchAndDecryptE2E(photoId, safeId, contentType) {
        // Check if SafeCrypto is available and safe is unlocked
        if (typeof SafeCrypto === 'undefined' || !SafeCrypto.isAvailable) {
            throw new Error('SafeCrypto not available - cannot decrypt E2E files');
        }

        if (!SafeCrypto.isUnlocked(safeId)) {
            throw new Error(`Safe ${safeId} is locked - please unlock first`);
        }

        // Fetch encrypted file
        const response = await fetch(`${getBaseUrl()}/files/${photoId}`);
        if (!response.ok) {
            throw new Error(`Failed to fetch encrypted file: ${response.status}`);
        }

        const encryptedBlob = await response.blob();
        
        // Decrypt using SafeCrypto
        const decryptedBlob = await SafeCrypto.decryptFileFromSafe(
            encryptedBlob,
            safeId,
            contentType
        );

        // Create blob URL
        const url = URL.createObjectURL(decryptedBlob);
        blobUrls.add(url);
        
        return url;
    }

    /**
     * Fetch and decrypt E2E thumbnail
     */
    async function fetchAndDecryptE2EThumbnail(photoId, safeId) {
        if (typeof SafeCrypto === 'undefined' || !SafeCrypto.isAvailable) {
            throw new Error('SafeCrypto not available');
        }

        if (!SafeCrypto.isUnlocked(safeId)) {
            throw new Error(`Safe ${safeId} is locked`);
        }

        const response = await fetch(`${getBaseUrl()}/files/${photoId}/thumbnail`);
        if (!response.ok) {
            throw new Error(`Failed to fetch encrypted thumbnail: ${response.status}`);
        }

        const encryptedBlob = await response.blob();
        
        const decryptedBlob = await SafeCrypto.decryptFileFromSafe(
            encryptedBlob,
            safeId,
            'image/jpeg'
        );

        const url = URL.createObjectURL(decryptedBlob);
        blobUrls.add(url);
        
        return url;
    }

    // Public API
    return {
        /**
         * Get URL for viewing a file.
         * 
         * For regular files: returns direct URL string.
         * For E2E files: returns Blob URL (remember to revoke with revokeUrl()).
         * 
         * @param {string} photoId - Photo ID
         * @param {Object} options - Options
         * @param {Object} options.photo - Pre-fetched photo metadata (optional)
         * @param {boolean} options.skipCache - Skip metadata cache
         * @returns {Promise<string>} - URL to use in img.src or video.src
         */
        async getFileUrl(photoId, options = {}) {
            const photo = options.photo || await fetchMetadata(photoId);
            const encryption = getEncryptionType(photo);

            if (encryption === 'e2e') {
                return fetchAndDecryptE2E(
                    photoId,
                    photo.safe_id,
                    photo.content_type || 'image/jpeg'
                );
            }

            // Regular file - return direct URL
            return `${getBaseUrl()}/files/${photoId}`;
        },

        /**
         * Get URL for viewing a thumbnail.
         * 
         * Same semantics as getFileUrl() but for thumbnails.
         * 
         * @param {string} photoId - Photo ID  
         * @param {Object} options - Options
         * @param {Object} options.photo - Pre-fetched photo metadata (optional)
         * @returns {Promise<string>} - URL for thumbnail
         */
        async getThumbnailUrl(photoId, options = {}) {
            const photo = options.photo || await fetchMetadata(photoId);
            const encryption = getEncryptionType(photo);

            if (encryption === 'e2e') {
                return fetchAndDecryptE2EThumbnail(photoId, photo.safe_id);
            }

            // Regular thumbnail - return direct URL
            return `${getBaseUrl()}/files/${photoId}/thumbnail`;
        },

        /**
         * Get thumbnail URL synchronously (for initial render).
         * 
         * Returns a placeholder for E2E files that will be resolved asynchronously.
         * Use this for initial HTML generation, then call resolveThumbnail() when
         * the element is mounted.
         * 
         * @param {string} photoId - Photo ID
         * @param {Object} photo - Photo metadata (must include safe_id if applicable)
         * @returns {string} - URL or placeholder
         */
        getThumbnailUrlSync(photoId, photo) {
            if (photo.safe_id) {
                // Return placeholder - will be resolved by resolveThumbnail()
                return `data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7`; // 1px transparent
            }
            return `${getBaseUrl()}/files/${photoId}/thumbnail`;
        },

        /**
         * Resolve thumbnail for an element asynchronously.
         * 
         * For E2E files, this fetches, decrypts, and sets the image src.
         * For regular files, this is a no-op (src should already be set).
         * 
         * @param {HTMLImageElement} imgElement - Image element to update
         * @param {string} photoId - Photo ID
         * @param {Object} photo - Photo metadata
         * @returns {Promise<boolean>} - True if resolved successfully
         */
        async resolveThumbnail(imgElement, photoId, photo) {
            if (!photo.safe_id) {
                // Regular file - src should already be set
                return true;
            }

            try {
                const url = await this.getThumbnailUrl(photoId, { photo });
                imgElement.src = url;
                imgElement.style.opacity = '1';
                
                // Clean up placeholder if exists
                if (imgElement.previousElementSibling?.classList.contains('gallery-placeholder')) {
                    imgElement.previousElementSibling.style.display = 'none';
                }
                
                return true;
            } catch (err) {
                console.error(`[FileAccessService] Failed to resolve thumbnail for ${photoId}:`, err);
                imgElement.dispatchEvent(new Event('error'));
                return false;
            }
        },

        /**
         * Revoke a blob URL created by this service.
         * 
         * IMPORTANT: Always call this when done with a Blob URL to prevent memory leaks.
         * 
         * @param {string} url - URL to revoke
         */
        revokeUrl(url) {
            if (blobUrls.has(url)) {
                URL.revokeObjectURL(url);
                blobUrls.delete(url);
            }
        },

        /**
         * Revoke all blob URLs created by this service.
         * 
         * Call this when navigating away from a page with many E2E images.
         */
        revokeAllUrls() {
            blobUrls.forEach(url => URL.revokeObjectURL(url));
            blobUrls.clear();
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
         * Check if photo is E2E encrypted (Safe).
         * 
         * @param {string} photoId - Photo ID
         * @param {Object} photo - Pre-fetched metadata (optional)
         * @returns {Promise<boolean>}
         */
        async isE2E(photoId, photo) {
            const metadata = photo || await fetchMetadata(photoId);
            return !!metadata.safe_id;
        },

        /**
         * Check if a Safe is unlocked (for E2E files).
         * 
         * @param {string} safeId - Safe ID
         * @returns {boolean}
         */
        isSafeUnlocked(safeId) {
            if (typeof SafeCrypto === 'undefined' || !SafeCrypto.isAvailable) {
                return false;
            }
            return SafeCrypto.isUnlocked(safeId);
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

console.log('[FileAccessService] Loaded');
