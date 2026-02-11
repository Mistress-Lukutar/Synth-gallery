/**
 * Key Exchange - Secure key sharing between users
 * 
 * This module handles:
 * - ECC key pair generation for users
 * - ECIES-style encryption for sharing Content Keys
 * - Folder key derivation and sharing
 * 
 * Uses ECDH (Elliptic Curve Diffie-Hellman) for key agreement.
 */

const KeyExchange = (function() {
    'use strict';
    
    // ECDH parameters
    const EC_CURVE = 'P-256'; // NIST curve, widely supported
    
    /**
     * Generate ECC key pair for user
     * @returns {Promise<CryptoKeyPair>}
     */
    async function generateKeyPair() {
        return await crypto.subtle.generateKey(
            {
                name: 'ECDH',
                namedCurve: EC_CURVE
            },
            true,  // Extractable for export
            ['deriveKey']
        );
    }
    
    /**
     * Export public key as base64
     * @param {CryptoKey} publicKey 
     * @returns {Promise<string>}
     */
    async function exportPublicKey(publicKey) {
        const exported = await crypto.subtle.exportKey('spki', publicKey);
        return btoa(String.fromCharCode(...new Uint8Array(exported)));
    }
    
    /**
     * Import public key from base64
     * @param {string} base64Key 
     * @returns {Promise<CryptoKey>}
     */
    async function importPublicKey(base64Key) {
        const bytes = new Uint8Array(atob(base64Key).split('').map(c => c.charCodeAt(0)));
        return await crypto.subtle.importKey(
            'spki',
            bytes,
            { name: 'ECDH', namedCurve: EC_CURVE },
            true,
            []
        );
    }
    
    /**
     * Export private key as base64 (for encrypted backup)
     * @param {CryptoKey} privateKey 
     * @returns {Promise<string>}
     */
    async function exportPrivateKey(privateKey) {
        const exported = await crypto.subtle.exportKey('pkcs8', privateKey);
        return btoa(String.fromCharCode(...new Uint8Array(exported)));
    }
    
    /**
     * Import private key from base64
     * @param {string} base64Key 
     * @returns {Promise<CryptoKey>}
     */
    async function importPrivateKey(base64Key) {
        const bytes = new Uint8Array(atob(base64Key).split('').map(c => c.charCodeAt(0)));
        return await crypto.subtle.importKey(
            'pkcs8',
            bytes,
            { name: 'ECDH', namedCurve: EC_CURVE },
            true,
            ['deriveKey']
        );
    }
    
    /**
     * Derive shared secret using ECDH
     * @param {CryptoKey} privateKey - Our private key
     * @param {CryptoKey} publicKey - Their public key
     * @returns {Promise<CryptoKey>}
     */
    async function deriveSharedKey(privateKey, publicKey) {
        return await crypto.subtle.deriveKey(
            {
                name: 'ECDH',
                public: publicKey
            },
            privateKey,
            { name: 'AES-GCM', length: 256 },
            false,  // Not extractable
            ['encrypt', 'decrypt']
        );
    }
    
    /**
     * Encrypt a Content Key for another user
     * @param {CryptoKey} contentKey - The CK to encrypt
     * @param {string} recipientPublicKeyBase64 - Recipient's public key (base64)
     * @param {CryptoKey} myPrivateKey - Our private key for ECDH
     * @returns {Promise<{encryptedKey: string, iv: string}>}
     */
    async function encryptForUser(contentKey, recipientPublicKeyBase64, myPrivateKey) {
        // Import recipient's public key
        const recipientPublicKey = await importPublicKey(recipientPublicKeyBase64);
        
        // Derive shared key
        const sharedKey = await deriveSharedKey(myPrivateKey, recipientPublicKey);
        
        // Export CK to raw bytes
        const rawCK = await crypto.subtle.exportKey('raw', contentKey);
        
        // Encrypt CK with shared key
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const encrypted = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: iv },
            sharedKey,
            rawCK
        );
        
        return {
            encryptedKey: btoa(String.fromCharCode(...new Uint8Array(encrypted))),
            iv: btoa(String.fromCharCode(...iv))
        };
    }
    
    /**
     * Decrypt a Content Key shared by another user
     * @param {string} encryptedKeyBase64 - Encrypted CK
     * @param {string} ivBase64 - IV
     * @param {string} senderPublicKeyBase64 - Sender's public key
     * @param {CryptoKey} myPrivateKey - Our private key
     * @returns {Promise<CryptoKey>}
     */
    async function decryptFromUser(encryptedKeyBase64, ivBase64, senderPublicKeyBase64, myPrivateKey) {
        // Import sender's public key
        const senderPublicKey = await importPublicKey(senderPublicKeyBase64);
        
        // Derive shared key
        const sharedKey = await deriveSharedKey(myPrivateKey, senderPublicKey);
        
        // Decrypt
        const encrypted = new Uint8Array(atob(encryptedKeyBase64).split('').map(c => c.charCodeAt(0)));
        const iv = new Uint8Array(atob(ivBase64).split('').map(c => c.charCodeAt(0)));
        
        const decrypted = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: iv },
            sharedKey,
            encrypted
        );
        
        // Import as AES key
        return await crypto.subtle.importKey(
            'raw',
            decrypted,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    /**
     * Simpler alternative: Encrypt CK with recipient's DEK-derived key
     * This requires the server to provide recipient's encrypted DEK,
     * but works without ECDH. Used as fallback.
     * 
     * Actually, for true zero-trust, we should use a shared secret
     * that only the two users know. Let's use a simpler approach:
     * The sender generates a random wrapping key, encrypts CK with it,
     * then encrypts wrapping key with recipient's public key.
     */
    async function encryptCKForRecipient(contentKey, recipientPublicKeyBase64) {
        // Generate ephemeral key pair
        const ephemeralKeyPair = await generateKeyPair();
        
        // Import recipient's public key
        const recipientPublicKey = await importPublicKey(recipientPublicKeyBase64);
        
        // Derive shared secret
        const sharedKey = await deriveSharedKey(ephemeralKeyPair.privateKey, recipientPublicKey);
        
        // Export and encrypt CK
        const rawCK = await crypto.subtle.exportKey('raw', contentKey);
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const encryptedCK = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: iv },
            sharedKey,
            rawCK
        );
        
        // Export ephemeral public key
        const ephemeralPublicKey = await exportPublicKey(ephemeralKeyPair.publicKey);
        
        return {
            encryptedCK: btoa(String.fromCharCode(...new Uint8Array(encryptedCK))),
            iv: btoa(String.fromCharCode(...iv)),
            ephemeralPublicKey: ephemeralPublicKey
        };
    }
    
    /**
     * Decrypt CK from sender using our private key
     */
    async function decryptCKFromSender(encryptedCKBase64, ivBase64, ephemeralPublicKeyBase64, myPrivateKey) {
        // Import ephemeral public key
        const ephemeralPublicKey = await importPublicKey(ephemeralPublicKeyBase64);
        
        // Derive shared secret
        const sharedKey = await deriveSharedKey(myPrivateKey, ephemeralPublicKey);
        
        // Decrypt CK
        const encrypted = new Uint8Array(atob(encryptedCKBase64).split('').map(c => c.charCodeAt(0)));
        const iv = new Uint8Array(atob(ivBase64).split('').map(c => c.charCodeAt(0)));
        
        const decrypted = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: iv },
            sharedKey,
            encrypted
        );
        
        // Import as AES key
        return await crypto.subtle.importKey(
            'raw',
            decrypted,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    /**
     * Generate Folder DEK from master DEK and folder ID
     * Uses HKDF-like derivation
     * @param {CryptoKey} masterDEK - User's master DEK
     * @param {string} folderId - Folder UUID
     * @returns {Promise<CryptoKey>}
     */
    async function deriveFolderDEK(masterDEK, folderId) {
        // Export DEK
        const rawDEK = await crypto.subtle.exportKey('raw', masterDEK);
        
        // Use Web Crypto's deriveKey with HKDF
        // First, we need to import DEK as HKDF key
        const hkdfKey = await crypto.subtle.importKey(
            'raw',
            rawDEK,
            { name: 'HKDF' },
            false,
            ['deriveKey']
        );
        
        // Encode folder ID as info parameter
        const encoder = new TextEncoder();
        const info = encoder.encode(`folder:${folderId}`);
        const salt = encoder.encode('synth-gallery-folder-dek-v1');
        
        // Derive folder-specific key
        return await crypto.subtle.deriveKey(
            {
                name: 'HKDF',
                salt: salt,
                info: info,
                hash: 'SHA-256'
            },
            hkdfKey,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    /**
     * Derive file Content Key from folder DEK and file ID
     * @param {CryptoKey} folderDEK - Folder DEK
     * @param {string} fileId - File UUID
     * @returns {Promise<CryptoKey>}
     */
    async function deriveFileCKFromFolder(folderDEK, fileId) {
        const rawFolderDEK = await crypto.subtle.exportKey('raw', folderDEK);
        
        const hkdfKey = await crypto.subtle.importKey(
            'raw',
            rawFolderDEK,
            { name: 'HKDF' },
            false,
            ['deriveKey']
        );
        
        const encoder = new TextEncoder();
        const info = encoder.encode(`file:${fileId}`);
        const salt = encoder.encode('synth-gallery-file-ck-v1');
        
        return await crypto.subtle.deriveKey(
            {
                name: 'HKDF',
                salt: salt,
                info: info,
                hash: 'SHA-256'
            },
            hkdfKey,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    // Store key pair in memory
    let userKeyPair = null;
    
    // Public API
    return {
        /**
         * Generate new ECC key pair for user
         */
        async generateKeyPair() {
            return await generateKeyPair();
        },
        
        /**
         * Set user's key pair (after loading from encrypted storage or generation)
         * @param {CryptoKeyPair} keyPair 
         */
        setUserKeyPair(keyPair) {
            userKeyPair = keyPair;
        },
        
        /**
         * Get user's key pair
         * @returns {CryptoKeyPair|null}
         */
        getUserKeyPair() {
            return userKeyPair;
        },
        
        /**
         * Export public key for upload to server
         */
        async exportPublicKey(publicKey) {
            return await exportPublicKey(publicKey || userKeyPair?.publicKey);
        },
        
        /**
         * Import another user's public key
         */
        async importPublicKey(base64Key) {
            return await importPublicKey(base64Key);
        },
        
        /**
         * Encrypt Content Key for sharing with another user
         * @param {CryptoKey} contentKey 
         * @param {string} recipientPublicKeyBase64 
         * @returns {Promise<{encryptedCK: string, iv: string, ephemeralPublicKey: string}>}
         */
        async encryptForSharing(contentKey, recipientPublicKeyBase64) {
            return await encryptCKForRecipient(contentKey, recipientPublicKeyBase64);
        },
        
        /**
         * Decrypt Content Key shared by another user
         * @param {string} encryptedCKBase64 
         * @param {string} ivBase64 
         * @param {string} ephemeralPublicKeyBase64 
         * @returns {Promise<CryptoKey>}
         */
        async decryptShared(encryptedCKBase64, ivBase64, ephemeralPublicKeyBase64) {
            if (!userKeyPair) {
                throw new Error('User key pair not initialized');
            }
            return await decryptCKFromSender(encryptedCKBase64, ivBase64, ephemeralPublicKeyBase64, userKeyPair.privateKey);
        },
        
        /**
         * Derive folder DEK from master DEK
         */
        async deriveFolderDEK(masterDEK, folderId) {
            return await deriveFolderDEK(masterDEK, folderId);
        },
        
        /**
         * Derive file CK from folder DEK
         */
        async deriveFileCK(folderDEK, fileId) {
            return await deriveFileCKFromFolder(folderDEK, fileId);
        },
        
        /**
         * Clear key pair from memory
         */
        clearKeyPair() {
            userKeyPair = null;
        }
    };
})();

// Make global
window.KeyExchange = KeyExchange;

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { KeyExchange };
}

console.log('key-exchange.js loaded, KeyExchange:', typeof KeyExchange);
