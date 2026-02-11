/**
 * File Crypto - Client-side file encryption/decryption
 * 
 * This module handles:
 * - Encrypting files with Content Keys (CK)
 * - Generating thumbnails client-side (Canvas API)
 * - Chunked/streaming encryption for large files
 * 
 * All encryption happens in the browser using WebCrypto API.
 */

const FileCrypto = (function() {
    'use strict';
    
    const CHUNK_SIZE = 1024 * 1024; // 1MB chunks for large files
    
    /**
     * Generate thumbnail from image/video file using Canvas
     * @param {File} file - The media file
     * @param {number} maxSize - Max thumbnail dimension
     * @returns {Promise<Blob>} - JPEG thumbnail blob
     */
    async function generateThumbnail(file, maxSize = 400) {
        return new Promise((resolve, reject) => {
            const isVideo = file.type.startsWith('video/');
            
            if (isVideo) {
                generateVideoThumbnail(file, maxSize).then(resolve).catch(reject);
            } else if (file.type.startsWith('image/')) {
                generateImageThumbnail(file, maxSize).then(resolve).catch(reject);
            } else {
                reject(new Error('Unsupported file type for thumbnail'));
            }
        });
    }
    
    /**
     * Generate thumbnail from image
     */
    async function generateImageThumbnail(file, maxSize) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            const url = URL.createObjectURL(file);
            
            img.onload = () => {
                URL.revokeObjectURL(url);
                
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                
                // Calculate dimensions maintaining aspect ratio
                let { width, height } = img;
                if (width > height) {
                    if (width > maxSize) {
                        height *= maxSize / width;
                        width = maxSize;
                    }
                } else {
                    if (height > maxSize) {
                        width *= maxSize / height;
                        height = maxSize;
                    }
                }
                
                canvas.width = Math.round(width);
                canvas.height = Math.round(height);
                
                // Use better quality settings
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                
                canvas.toBlob(
                    (blob) => {
                        if (blob) resolve(blob);
                        else reject(new Error('Failed to create thumbnail blob'));
                    },
                    'image/jpeg',
                    0.85
                );
            };
            
            img.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error('Failed to load image'));
            };
            
            img.src = url;
        });
    }
    
    /**
     * Generate thumbnail from video
     */
    async function generateVideoThumbnail(file, maxSize) {
        return new Promise((resolve, reject) => {
            const video = document.createElement('video');
            const url = URL.createObjectURL(file);
            
            video.onloadedmetadata = () => {
                // Seek to 1 second or 25% of duration
                video.currentTime = Math.min(1, video.duration * 0.25);
            };
            
            video.onseeked = () => {
                URL.revokeObjectURL(url);
                
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                
                // Calculate dimensions
                let { videoWidth: width, videoHeight: height } = video;
                if (width > height) {
                    if (width > maxSize) {
                        height *= maxSize / width;
                        width = maxSize;
                    }
                } else {
                    if (height > maxSize) {
                        width *= maxSize / height;
                        height = maxSize;
                    }
                }
                
                canvas.width = Math.round(width);
                canvas.height = Math.round(height);
                
                ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                
                canvas.toBlob(
                    (blob) => {
                        if (blob) resolve(blob);
                        else reject(new Error('Failed to create video thumbnail blob'));
                    },
                    'image/jpeg',
                    0.85
                );
            };
            
            video.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error('Failed to load video'));
            };
            
            video.src = url;
            video.load();
        });
    }
    
    /**
     * Encrypt a Blob with AES-GCM
     * @param {Blob} blob - Data to encrypt
     * @param {CryptoKey} key - AES-GCM key
     * @returns {Promise<{encryptedData: Uint8Array, iv: Uint8Array}>}
     */
    async function encryptBlob(blob, key) {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const data = new Uint8Array(await blob.arrayBuffer());
        
        const encrypted = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: iv },
            key,
            data
        );
        
        return {
            encryptedData: new Uint8Array(encrypted),
            iv: iv
        };
    }
    
    /**
     * Decrypt data with AES-GCM
     * @param {Uint8Array} encryptedData - Encrypted bytes
     * @param {Uint8Array} iv - IV used for encryption
     * @param {CryptoKey} key - AES-GCM key
     * @returns {Promise<Uint8Array>} - Decrypted data
     */
    async function decryptData(encryptedData, iv, key) {
        const decrypted = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: iv },
            key,
            encryptedData
        );
        
        return new Uint8Array(decrypted);
    }
    
    /**
     * Encrypt a file using envelope encryption
     * @param {File} file - The file to encrypt
     * @returns {Promise<{
     *   encryptedFile: Blob,
     *   encryptedThumbnail: Blob|null,
     *   encryptedCK: string,
     *   thumbnailEncryptedCK: string|null,
     *   metadata: {width: number, height: number}
     * }>}
     */
    async function encryptFile(file) {
        // Generate Content Key for this file
        const contentKey = await crypto.subtle.generateKey(
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
        
        // Generate thumbnail first (before encryption)
        let thumbnailBlob = null;
        let thumbnailDimensions = { width: 0, height: 0 };
        
        try {
            thumbnailBlob = await generateThumbnail(file, 400);
            // Get dimensions from the blob (we'll need to load it)
            const img = await loadImageFromBlob(thumbnailBlob);
            thumbnailDimensions = { width: img.width, height: img.height };
        } catch (e) {
            console.warn('Thumbnail generation failed:', e);
        }
        
        // Encrypt file content
        const { encryptedData: encryptedFileData, iv: fileIv } = await encryptBlob(file, contentKey);
        
        // Combine IV + encrypted data
        const encryptedFileArray = new Uint8Array(fileIv.length + encryptedFileData.length);
        encryptedFileArray.set(fileIv);
        encryptedFileArray.set(encryptedFileData, fileIv.length);
        
        let encryptedThumbnailArray = null;
        let thumbnailEncryptedCK = null;
        
        if (thumbnailBlob) {
            // Use same CK for thumbnail (simpler) or generate separate key
            const { encryptedData: encryptedThumbData, iv: thumbIv } = await encryptBlob(thumbnailBlob, contentKey);
            
            encryptedThumbnailArray = new Uint8Array(thumbIv.length + encryptedThumbData.length);
            encryptedThumbnailArray.set(thumbIv);
            encryptedThumbnailArray.set(encryptedThumbData, thumbIv.length);
        }
        
        // Encrypt Content Key with user's DEK
        const { DEKManager } = await import('./dek-manager.js');
        const encryptedCK = await DEKManager.encryptContentKey(contentKey);
        
        return {
            encryptedFile: new Blob([encryptedFileArray]),
            encryptedThumbnail: encryptedThumbnailArray ? new Blob([encryptedThumbnailArray]) : null,
            encryptedCK: encryptedCK,
            thumbnailEncryptedCK: null, // Same key for now
            metadata: {
                ...thumbnailDimensions,
                originalSize: file.size,
                encryptedSize: encryptedFileArray.length
            }
        };
    }
    
    /**
     * Load image from blob to get dimensions
     */
    function loadImageFromBlob(blob) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            const url = URL.createObjectURL(blob);
            
            img.onload = () => {
                URL.revokeObjectURL(url);
                resolve(img);
            };
            
            img.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error('Failed to load image'));
            };
            
            img.src = url;
        });
    }
    
    /**
     * Decrypt a file
     * @param {Blob} encryptedBlob - The encrypted file blob
     * @param {CryptoKey} contentKey - The Content Key
     * @param {string} mimeType - Original MIME type
     * @returns {Promise<Blob>} - Decrypted file blob
     */
    async function decryptFile(encryptedBlob, contentKey, mimeType) {
        const encryptedData = new Uint8Array(await encryptedBlob.arrayBuffer());
        
        // Extract IV (first 12 bytes)
        const iv = encryptedData.slice(0, 12);
        const ciphertext = encryptedData.slice(12);
        
        const decrypted = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: iv },
            contentKey,
            ciphertext
        );
        
        return new Blob([decrypted], { type: mimeType });
    }
    
    /**
     * Decrypt and display an image in an img element
     * @param {Blob} encryptedBlob - Encrypted image blob
     * @param {CryptoKey} contentKey - The Content Key
     * @param {HTMLImageElement} imgElement - Target image element
     */
    async function decryptAndDisplayImage(encryptedBlob, contentKey, imgElement) {
        const decryptedBlob = await decryptFile(encryptedBlob, contentKey, 'image/jpeg');
        const url = URL.createObjectURL(decryptedBlob);
        
        imgElement.onload = () => {
            URL.revokeObjectURL(url);
        };
        
        imgElement.src = url;
    }
    
    // Public API
    return {
        /**
         * Encrypt a file for upload
         */
        async encryptFile(file) {
            return await encryptFile(file);
        },
        
        /**
         * Decrypt a downloaded file
         */
        async decryptFile(encryptedBlob, contentKey, mimeType) {
            return await decryptFile(encryptedBlob, contentKey, mimeType);
        },
        
        /**
         * Generate thumbnail from file
         */
        async generateThumbnail(file, maxSize = 400) {
            return await generateThumbnail(file, maxSize);
        },
        
        /**
         * Decrypt and display image
         */
        async decryptAndDisplayImage(encryptedBlob, contentKey, imgElement) {
            return await decryptAndDisplayImage(encryptedBlob, contentKey, imgElement);
        },
        
        /**
         * Get file MIME type from filename
         */
        getMimeType(filename) {
            const ext = filename.split('.').pop().toLowerCase();
            const types = {
                'jpg': 'image/jpeg',
                'jpeg': 'image/jpeg',
                'png': 'image/png',
                'gif': 'image/gif',
                'webp': 'image/webp',
                'mp4': 'video/mp4',
                'webm': 'video/webm'
            };
            return types[ext] || 'application/octet-stream';
        }
    };
})();

// Make global
window.FileCrypto = FileCrypto;

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { FileCrypto };
}

console.log('file-crypto.js loaded, FileCrypto:', typeof FileCrypto);
