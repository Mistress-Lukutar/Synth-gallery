/**
 * Envelope Encryption Client - Main module
 * 
 * Coordinates all crypto operations:
 * - DEK management
 * - File encryption/decryption  
 * - Key exchange for sharing
 * - Integration with server API
 */

// Import submodules (when using ES modules)
// import { DEKManager } from './dek-manager.js';
// import { FileCrypto } from './file-crypto.js';
// import { KeyExchange } from './key-exchange.js';

// Check for secure context
if (!window.isSecureContext) {
    console.error('Insecure context detected. Web Crypto requires HTTPS or localhost.');
}

// Check Web Crypto availability
if (!window.crypto || !window.crypto.subtle) {
    console.error('Web Crypto API not available.');
}

console.log('Loading index.js...');
console.log('Secure context:', window.isSecureContext);
console.log('DEKManager available:', typeof DEKManager !== 'undefined');
console.log('FileCrypto available:', typeof FileCrypto !== 'undefined');
console.log('KeyExchange available:', typeof KeyExchange !== 'undefined');

const EnvelopeCrypto = (function() {
    'use strict';
    
    // Check dependencies
    if (typeof DEKManager === 'undefined') {
        console.error('DEKManager is not loaded!');
    }
    if (typeof FileCrypto === 'undefined') {
        console.error('FileCrypto is not loaded!');
    }
    if (typeof KeyExchange === 'undefined') {
        console.error('KeyExchange is not loaded!');
    }
    
    // API endpoints
    const API_BASE = '/api/envelope';
    
    /**
     * Get CSRF token from meta tag or cookie
     */
    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.getAttribute('content')) {
            return meta.getAttribute('content');
        }
        const match = document.cookie.match(/synth_csrf=([^;]+)/);
        return match ? match[1] : '';
    }
    
    /**
     * Make authenticated API request
     */
    async function apiRequest(endpoint, options = {}) {
        const url = `${API_BASE}${endpoint}`;
        const isMutating = ['POST', 'PUT', 'DELETE', 'PATCH'].includes((options.method || 'GET').toUpperCase());
        
        const headers = {
            'Content-Type': 'application/json',
            ...options.headers
        };
        
        // Add CSRF token for mutating requests
        if (isMutating) {
            headers['X-CSRF-Token'] = getCsrfToken();
        }
        
        const response = await fetch(url, {
            ...options,
            headers,
            credentials: 'same-origin'
        });
        
        if (!response.ok) {
            const error = await response.text();
            throw new Error(`API error: ${response.status} - ${error}`);
        }
        
        return response.json();
    }
    
    /**
     * Initialize encryption on login
     * @param {string} password - User password
     * @param {Object} serverKeys - Keys from server (encrypted_dek, salt, etc.)
     */
    async function initialize(password, serverKeys = null) {
        if (serverKeys && serverKeys.has_encryption && serverKeys.salt) {
            // Existing user - derive DEK from password and verify with encrypted_dek
            await DEKManager.initializeFromPassword(
                password, 
                serverKeys.salt,
                serverKeys.encrypted_dek  // Pass encrypted DEK to verify password
            );
        } else {
            // New user - generate new DEK
            const result = await DEKManager.initializeFromPassword(password);
            
            // Generate ECC key pair for sharing
            const keyPair = await KeyExchange.generateKeyPair();
            KeyExchange.setUserKeyPair(keyPair);
            
            // Upload public key to server
            const publicKeyBase64 = await KeyExchange.exportPublicKey();
            await uploadPublicKey(publicKeyBase64);
            
            return {
                salt: result.salt,
                publicKey: publicKeyBase64
            };
        }
        
        return { salt: DEKManager.getSalt() };
    }
    
    /**
     * Upload public key to server
     */
    async function uploadPublicKey(publicKeyBase64) {
        return await apiRequest('/my-public-key', {
            method: 'POST',
            body: JSON.stringify({ public_key: publicKeyBase64 })
        });
    }
    
    /**
     * Encrypt and upload a file
     * @param {File} file - The file to upload
     * @param {string} folderId - Target folder ID
     * @returns {Promise<Object>} - Upload result with photo_id
     */
    async function encryptAndUploadFile(file, folderId) {
        // Check DEK availability
        if (!DEKManager.hasDEK()) {
            throw new Error('DEK not initialized. Please log in.');
        }
        
        // Encrypt file client-side
        console.log('Encrypting file...');
        const encrypted = await FileCrypto.encryptFile(file);
        
        // Upload encrypted file
        const formData = new FormData();
        formData.append('file', encrypted.encryptedFile, file.name + '.encrypted');
        formData.append('folder_id', folderId);
        formData.append('encrypted_ck', encrypted.encryptedCK);
        formData.append('storage_mode', 'envelope');
        formData.append('original_name', file.name);
        formData.append('media_type', file.type.startsWith('video/') ? 'video' : 'image');
        
        if (encrypted.encryptedThumbnail) {
            formData.append('thumbnail', encrypted.encryptedThumbnail, 'thumb.jpg.encrypted');
            if (encrypted.thumbnailEncryptedCK) {
                formData.append('thumbnail_encrypted_ck', encrypted.thumbnailEncryptedCK);
            }
        }
        
        // Metadata
        formData.append('thumb_width', encrypted.metadata.width);
        formData.append('thumb_height', encrypted.metadata.height);
        formData.append('encrypted_size', encrypted.metadata.encryptedSize);
        
        console.log('Uploading encrypted file...');
        const response = await fetch('/upload', {
            method: 'POST',
            body: formData,
            credentials: 'same-origin'
        });
        
        if (!response.ok) {
            const error = await response.text();
            throw new Error(`Upload failed: ${error}`);
        }
        
        const result = await response.json();
        
        // Store the photo key on server
        await apiRequest(`/photos/${result.id}/key`, {
            method: 'POST',
            body: JSON.stringify({
                encrypted_ck: encrypted.encryptedCK,
                thumbnail_encrypted_ck: encrypted.thumbnailEncryptedCK
            })
        });
        
        return result;
    }
    
    /**
     * Download and decrypt a file
     * @param {string} photoId - Photo ID
     * @param {HTMLImageElement|HTMLVideoElement} targetElement - Element to display in
     */
    async function downloadAndDecrypt(photoId, targetElement = null) {
        // Get encrypted key
        const keyData = await apiRequest(`/photos/${photoId}/key`);
        
        if (keyData.storage_mode === 'legacy') {
            // Legacy file - server will decrypt
            const photo = await fetch(`${getBaseUrl()}/files/${photoId}`, { credentials: 'same-origin' });
            if (targetElement) {
                targetElement.src = URL.createObjectURL(await photo.blob());
            }
            return photo.blob();
        }
        
        // Decrypt Content Key
        const contentKey = await DEKManager.decryptContentKey(keyData.encrypted_ck);
        
        // Download encrypted file
        const encryptedResponse = await fetch(`${getBaseUrl()}/files/${photoId}`, { credentials: 'same-origin' });
        const encryptedBlob = await encryptedResponse.blob();
        
        // Decrypt file
        const mimeType = 'image/jpeg'; // Could get from metadata
        const decryptedBlob = await FileCrypto.decryptFile(encryptedBlob, contentKey, mimeType);
        
        // Display if element provided
        if (targetElement) {
            const url = URL.createObjectURL(decryptedBlob);
            targetElement.onload = () => URL.revokeObjectURL(url);
            targetElement.src = url;
        }
        
        return decryptedBlob;
    }
    
    /**
     * Share a photo with another user
     * @param {string} photoId - Photo ID
     * @param {number} targetUserId - User to share with
     */
    async function sharePhoto(photoId, targetUserId) {
        // Get target user's public key
        const targetUser = await apiRequest(`/users/${targetUserId}/public-key`);
        
        // Get our encrypted CK
        const keyData = await apiRequest(`/photos/${photoId}/key`);
        
        // Decrypt CK
        const contentKey = await DEKManager.decryptContentKey(keyData.encrypted_ck);
        
        // Encrypt CK for target user
        const encryptedForTarget = await KeyExchange.encryptForSharing(
            contentKey, 
            targetUser.public_key
        );
        
        // Send to server
        await apiRequest(`/photos/${photoId}/share`, {
            method: 'POST',
            body: JSON.stringify({
                user_id: targetUserId,
                encrypted_ck_for_user: encryptedForTarget.encryptedCK
                // Note: iv and ephemeralPublicKey would also be included in real implementation
            })
        });
        
        return { success: true };
    }
    
    /**
     * Create encrypted folder key for sharing
     * @param {string} folderId - Folder ID
     */
    async function createFolderKey(folderId) {
        if (!DEKManager.hasDEK()) {
            throw new Error('DEK not initialized');
        }
        
        // Derive folder DEK from master DEK
        const folderDEK = await KeyExchange.deriveFolderDEK(
            DEKManager.getDEK(),
            folderId
        );
        
        // Encrypt folder DEK with our DEK
        const encryptedFolderDEK = await DEKManager.encryptContentKey(folderDEK);
        
        // Send to server
        // TODO: need to get current user ID from context
        const encryptedMap = JSON.stringify({
            owner: encryptedFolderDEK
        });
        
        await apiRequest(`/folders/${folderId}/key`, {
            method: 'POST',
            body: JSON.stringify({
                encrypted_folder_dek: btoa(encryptedMap)
            })
        });
        
        return { success: true };
    }
    
    /**
     * Get folder DEK (decrypt if needed)
     * @param {string} folderId 
     * @returns {Promise<CryptoKey>}
     */
    async function getFolderDEK(folderId) {
        const folderKey = await apiRequest(`/folders/${folderId}/key`);
        return await DEKManager.decryptContentKey(folderKey.encrypted_folder_dek);
    }
    
    /**
     * Upload file to shared folder using folder DEK
     */
    async function uploadToSharedFolder(file, folderId) {
        // Get folder DEK
        const folderDEK = await getFolderDEK(folderId);
        
        // Derive file CK from folder DEK
        // We need to generate photo_id first, or use temp ID
        const tempFileId = crypto.randomUUID();
        const fileCK = await KeyExchange.deriveFileCK(folderDEK, tempFileId);
        
        // Encrypt file with fileCK
        const fileData = new Uint8Array(await file.arrayBuffer());
        const iv = crypto.getRandomValues(new Uint8Array(12));
        
        const encryptedData = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: iv },
            fileCK,
            fileData
        );
        
        // Combine IV + encrypted data
        const encryptedFile = new Uint8Array(iv.length + encryptedData.byteLength);
        encryptedFile.set(iv);
        encryptedFile.set(new Uint8Array(encryptedData), iv.length);
        
        // Upload
        // ... (similar to regular upload)
    }
    
    // Public API
    return {
        // Initialization
        initialize,
        
        // Core crypto modules
        DEKManager,
        FileCrypto,
        KeyExchange,
        
        // File operations
        encryptAndUploadFile,
        downloadAndDecrypt,
        
        // Sharing
        sharePhoto,
        createFolderKey,
        getFolderDEK,
        uploadToSharedFolder,
        
        // Migration
        async getMigrationStatus() {
            return await apiRequest('/migration/status');
        },
        
        async getPendingMigrationPhotos() {
            return await apiRequest('/migration/pending-photos');
        },
        
        async migratePhotos(photoKeys) {
            return await apiRequest('/migration/batch', {
                method: 'POST',
                body: JSON.stringify({ photo_keys: photoKeys })
            });
        }
    };
})();

// Auto-clear keys on logout
function setupLogoutCleanup() {
    // Intercept logout clicks
    document.addEventListener('click', (e) => {
        if (e.target.matches('[href*="logout"], [data-action="logout"]')) {
            EnvelopeCrypto.DEKManager.clearDEK();
            EnvelopeCrypto.KeyExchange.clearKeyPair();
        }
    });
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupLogoutCleanup);
} else {
    setupLogoutCleanup();
}

// Make global
window.EnvelopeCrypto = EnvelopeCrypto;

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { EnvelopeCrypto };
}

console.log('index.js loaded, EnvelopeCrypto:', typeof EnvelopeCrypto);
