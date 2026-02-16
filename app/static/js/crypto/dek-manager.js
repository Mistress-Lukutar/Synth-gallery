/**
 * DEK Manager - Client-side Data Encryption Key management
 * 
 * This module handles:
 * - Deriving DEK from user password using PBKDF2
 * - Secure in-memory storage of DEK (never persisted to storage)
 * - Content Key (CK) encryption/decryption
 * 
 * Security note: DEK lives only in closure scope, never in localStorage,
 * sessionStorage, or any persistent storage. It's cleared on page unload.
 */

const DEKManager = (function() {
    'use strict';
    
    // Check if Web Crypto is available (requires secure context)
    if (!window.crypto || !window.crypto.subtle) {
        console.error('Web Crypto API not available. This requires a secure context (HTTPS or localhost).');
        // Return a stub that shows clear error messages
        return {
            hasDEK: () => false,
            initializeFromPassword: async () => {
                throw new Error('Encryption requires a secure connection (HTTPS or localhost). Please access this page via https:// or localhost.');
            },
            generateContentKey: async () => {
                throw new Error('Encryption not available in insecure context');
            },
            encryptContentKey: async () => {
                throw new Error('Encryption not available in insecure context');
            },
            decryptContentKey: async () => {
                throw new Error('Encryption not available in insecure context');
            },
            exportDEK: async () => {
                throw new Error('Encryption not available in insecure context');
            },
            clearDEK: () => {},
            getSalt: () => null
        };
    }
    
    // Private DEK storage - lives only in closure
    let userDEK = null;
    let dekSalt = null;
    
    // Constants matching server-side values
    const PBKDF2_ITERATIONS = 600000;
    const SALT_SIZE = 32;
    const DEK_SIZE = 32;
    
    /**
     * Generate a random salt
     */
    function generateSalt() {
        return crypto.getRandomValues(new Uint8Array(SALT_SIZE));
    }
    
    /**
     * Derive DEK from password using PBKDF2
     * @param {string} password - User password
     * @param {Uint8Array} salt - Salt bytes
     * @returns {Promise<CryptoKey>} - Derived DEK as CryptoKey
     */
    async function deriveDEKFromPassword(password, salt) {
        const encoder = new TextEncoder();
        const passwordData = encoder.encode(password);
        
        // Import password as key material
        const keyMaterial = await crypto.subtle.importKey(
            'raw',
            passwordData,
            { name: 'PBKDF2' },
            false,
            ['deriveKey']
        );
        
        // Derive AES-GCM key
        const dek = await crypto.subtle.deriveKey(
            {
                name: 'PBKDF2',
                salt: salt,
                iterations: PBKDF2_ITERATIONS,
                hash: 'SHA-256'
            },
            keyMaterial,
            { name: 'AES-GCM', length: 256 },
            true,  // Extractable for export if needed
            ['encrypt', 'decrypt']
        );
        
        return dek;
    }
    
    /**
     * Generate a new random Content Key (CK)
     * @returns {Promise<CryptoKey>} - Random AES-GCM key
     */
    async function generateContentKey() {
        return await crypto.subtle.generateKey(
            { name: 'AES-GCM', length: 256 },
            true,  // Extractable for wrapping with DEK
            ['encrypt', 'decrypt']
        );
    }
    
    /**
     * Export key to raw bytes
     */
    async function exportKeyRaw(key) {
        return new Uint8Array(await crypto.subtle.exportKey('raw', key));
    }
    
    /**
     * Import key from raw bytes
     */
    async function importKeyRaw(rawBytes) {
        return await crypto.subtle.importKey(
            'raw',
            rawBytes,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    /**
     * Wrap (encrypt) a Content Key with DEK
     * @param {CryptoKey} contentKey - The CK to wrap
     * @param {CryptoKey} dek - The DEK to wrap with
     * @returns {Promise<{encryptedKey: Uint8Array, iv: Uint8Array}>}
     */
    async function wrapContentKey(contentKey, dek) {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        
        // Export CK to raw bytes, then encrypt with DEK
        const rawKey = await crypto.subtle.exportKey('raw', contentKey);
        
        const encryptedKey = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: iv },
            dek,
            rawKey
        );
        
        return {
            encryptedKey: new Uint8Array(encryptedKey),
            iv: iv
        };
    }
    
    /**
     * Unwrap (decrypt) a Content Key with DEK
     * @param {Uint8Array} encryptedKey - The encrypted CK bytes
     * @param {Uint8Array} iv - The IV used for encryption
     * @param {CryptoKey} dek - The DEK to unwrap with
     * @returns {Promise<CryptoKey>} - The unwrapped CK
     */
    async function unwrapContentKey(encryptedKey, iv, dek) {
        const decrypted = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: iv },
            dek,
            encryptedKey
        );
        
        return await crypto.subtle.importKey(
            'raw',
            decrypted,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    // Public API
    return {
        /**
         * Initialize DEK from password (on login)
         * @param {string} password - User password
         * @param {string|null} existingSalt - Existing salt (base64) or null for new user
         * @param {string|null} encryptedDEK - Existing encrypted DEK (base64) to verify password
         * @returns {Promise<{dek: CryptoKey, salt: string}>}
         * @throws {Error} If password is incorrect (cannot decrypt encryptedDEK)
         */
        async initializeFromPassword(password, existingSalt = null, encryptedDEK = null) {
            const salt = existingSalt 
                ? new Uint8Array(atob(existingSalt).split('').map(c => c.charCodeAt(0)))
                : generateSalt();
            
            userDEK = await deriveDEKFromPassword(password, salt);
            dekSalt = salt;
            
            // If encrypted DEK provided, verify we can decrypt it (password is correct)
            if (encryptedDEK) {
                try {
                    // Try to decrypt the stored encrypted DEK
                    // Format: iv (12 bytes) + ciphertext
                    const encryptedData = new Uint8Array(atob(encryptedDEK).split('').map(c => c.charCodeAt(0)));
                    const iv = encryptedData.slice(0, 12);
                    const ciphertext = encryptedData.slice(12);
                    
                    // Try to decrypt - if it fails, password is wrong
                    await crypto.subtle.decrypt(
                        { name: 'AES-GCM', iv: iv },
                        userDEK,
                        ciphertext
                    );
                    // If we got here, decryption succeeded - password is correct
                } catch (e) {
                    // Clear the invalid DEK
                    userDEK = null;
                    dekSalt = null;
                    throw new Error('Incorrect password');
                }
            }
            
            return {
                dek: userDEK,
                salt: btoa(String.fromCharCode(...salt))
            };
        },
        
        /**
         * Set DEK directly (e.g., after WebAuthn auth)
         * @param {Uint8Array} dekBytes - Raw DEK bytes
         */
        async setDEK(dekBytes) {
            userDEK = await importKeyRaw(dekBytes);
        },
        
        /**
         * Get current DEK
         * @returns {CryptoKey|null}
         */
        getDEK() {
            return userDEK;
        },
        
        /**
         * Check if DEK is available
         * @returns {boolean}
         */
        hasDEK() {
            return userDEK !== null;
        },
        
        /**
         * Clear DEK from memory (on logout)
         */
        clearDEK() {
            userDEK = null;
            dekSalt = null;
        },
        
        /**
         * Generate new Content Key for file encryption
         * @returns {Promise<CryptoKey>}
         */
        async generateContentKey() {
            return await generateContentKey();
        },
        
        /**
         * Encrypt a Content Key with DEK for storage
         * @param {CryptoKey} contentKey - The CK to encrypt
         * @returns {Promise<string>} - base64-encoded encrypted key with IV
         */
        async encryptContentKey(contentKey) {
            if (!userDEK) {
                throw new Error('DEK not initialized');
            }
            
            const { encryptedKey, iv } = await wrapContentKey(contentKey, userDEK);
            
            // Format: iv (12 bytes) + encrypted key
            const combined = new Uint8Array(iv.length + encryptedKey.length);
            combined.set(iv);
            combined.set(encryptedKey, iv.length);
            
            return btoa(String.fromCharCode(...combined));
        },
        
        /**
         * Decrypt a Content Key with DEK
         * @param {string} encryptedCKBase64 - base64-encoded encrypted key with IV
         * @returns {Promise<CryptoKey>} - The decrypted CK
         */
        async decryptContentKey(encryptedCKBase64) {
            if (!userDEK) {
                throw new Error('DEK not initialized');
            }
            
            const combined = new Uint8Array(atob(encryptedCKBase64).split('').map(c => c.charCodeAt(0)));
            const iv = combined.slice(0, 12);
            const encryptedKey = combined.slice(12);
            
            return await unwrapContentKey(encryptedKey, iv, userDEK);
        },
        
        /**
         * Export DEK as base64 (for backup/recovery)
         * @returns {Promise<string>} - base64-encoded DEK
         */
        async exportDEK() {
            if (!userDEK) {
                throw new Error('DEK not initialized');
            }
            
            const raw = await exportKeyRaw(userDEK);
            return btoa(String.fromCharCode(...raw));
        },
        
        /**
         * Get current salt
         * @returns {string|null} - base64-encoded salt
         */
        getSalt() {
            return dekSalt ? btoa(String.fromCharCode(...dekSalt)) : null;
        }
    };
})();

// Auto-clear DEK on page unload
window.addEventListener('beforeunload', () => {
    DEKManager.clearDEK();
});

// Make global for non-module environments
window.DEKManager = DEKManager;

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { DEKManager };
}

console.log('dek-manager.js loaded, DEKManager:', typeof DEKManager);
