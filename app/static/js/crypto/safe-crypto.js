/**
 * Safe Crypto - Client-side encryption for Safes (encrypted vaults)
 * 
 * Each Safe has its own DEK (Data Encryption Key) that is independent
 * of the user's master DEK. Safe DEK is encrypted with either:
 * - A password (PBKDF2)
 * - A WebAuthn credential
 * 
 * Safe keys are stored in memory only and never persisted.
 */

const SafeCrypto = (function() {
    'use strict';
    
    console.log('[SafeCrypto] Initializing...');
    console.log('[SafeCrypto] window.crypto:', typeof window.crypto);
    console.log('[SafeCrypto] window.isSecureContext:', window.isSecureContext);
    
    // Get crypto from window (for browsers that don't expose it globally in strict mode)
    const crypto = window.crypto || window.msCrypto;
    
    console.log('[SafeCrypto] crypto object:', typeof crypto);
    if (crypto) {
        console.log('[SafeCrypto] crypto.subtle:', typeof crypto.subtle);
    }
    
    // Check if Web Crypto is available
    if (!crypto || !crypto.subtle) {
        console.error('Web Crypto API not available. Safes require a secure context (HTTPS or localhost).');
        // Return stub that shows clear error
        return {
            isAvailable: false,
            createSafeWithPassword: async () => { throw new Error('Web Crypto API not available. Please use HTTPS or localhost.'); },
            createSafeWithWebAuthn: async () => { throw new Error('Web Crypto API not available. Please use HTTPS or localhost.'); },
            unlockWithPassword: async () => { throw new Error('Web Crypto API not available. Please use HTTPS or localhost.'); },
            encryptFileForSafe: async () => { throw new Error('Web Crypto API not available. Please use HTTPS or localhost.'); },
            decryptFileFromSafe: async () => { throw new Error('Web Crypto API not available. Please use HTTPS or localhost.'); },
            getSafeDEK: () => null,
            isUnlocked: () => false,
            lockSafe: () => {},
            lockAll: () => {}
        };
    }
    
    // Check dependencies
    if (typeof DEKManager === 'undefined') {
        console.error('DEKManager is required for SafeCrypto');
    }
    
    // Private storage for safe DEKs
    // Map: safe_id -> { dek: CryptoKey, unlocked_at: timestamp }
    const safeDEKs = new Map();
    
    // Constants
    const PBKDF2_ITERATIONS = 600000;
    const SALT_SIZE = 32;
    const DEK_SIZE = 32;
    const SESSION_KEY_SIZE = 32;
    
    // Flag to indicate crypto is available
    const isAvailable = true;
    
    // Helper: Base64 encode Uint8Array (URL-safe)
    function base64Encode(bytes) {
        const binString = Array.from(bytes, (b) => String.fromCharCode(b)).join("");
        return btoa(binString).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    }
    
    // Helper: Base64 decode to Uint8Array (URL-safe)
    function base64Decode(base64) {
        if (!base64 || typeof base64 !== 'string') {
            throw new Error('Invalid base64 input: ' + typeof base64);
        }
        // Restore standard base64
        let str = base64.replace(/-/g, '+').replace(/_/g, '/');
        // Add padding
        const pad = str.length % 4;
        if (pad) {
            str += '='.repeat(4 - pad);
        }
        const binString = atob(str);
        return Uint8Array.from(binString, (m) => m.charCodeAt(0));
    }
    
    // Generate a random session key for encrypting DEK in session
    async function generateSessionKey() {
        return crypto.getRandomValues(new Uint8Array(SESSION_KEY_SIZE));
    }
    
    // Derive key from password using PBKDF2
    async function deriveKeyFromPassword(password, salt) {
        const encoder = new TextEncoder();
        const passwordData = encoder.encode(password);
        
        const keyMaterial = await crypto.subtle.importKey(
            'raw',
            passwordData,
            { name: 'PBKDF2' },
            false,
            ['deriveKey']
        );
        
        return crypto.subtle.deriveKey(
            {
                name: 'PBKDF2',
                salt: salt,
                iterations: PBKDF2_ITERATIONS,
                hash: 'SHA-256'
            },
            keyMaterial,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    // Generate a new Safe DEK
    async function generateSafeDEK() {
        return await crypto.subtle.generateKey(
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    // Export key to raw bytes
    async function exportKeyRaw(key) {
        return new Uint8Array(await crypto.subtle.exportKey('raw', key));
    }
    
    // Import key from raw bytes
    async function importKeyRaw(rawBytes) {
        return await crypto.subtle.importKey(
            'raw',
            rawBytes,
            { name: 'AES-GCM', length: 256 },
            true,
            ['encrypt', 'decrypt']
        );
    }
    
    // Encrypt DEK with a key
    async function encryptDEK(dek, key) {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const rawDEK = await exportKeyRaw(dek);
        
        const encrypted = await crypto.subtle.encrypt(
            { name: 'AES-GCM', iv: iv },
            key,
            rawDEK
        );
        
        // Combine IV + encrypted data
        const combined = new Uint8Array(iv.length + encrypted.byteLength);
        combined.set(iv);
        combined.set(new Uint8Array(encrypted), iv.length);
        
        return combined;
    }
    
    // Decrypt DEK with a key
    async function decryptDEK(encryptedData, key) {
        const iv = encryptedData.slice(0, 12);
        const ciphertext = encryptedData.slice(12);
        
        const decrypted = await crypto.subtle.decrypt(
            { name: 'AES-GCM', iv: iv },
            key,
            ciphertext
        );
        
        return importKeyRaw(new Uint8Array(decrypted));
    }
    
    // Encrypt safe DEK for session storage
    async function encryptDEKForSession(dek) {
        const sessionKey = await generateSessionKey();
        const sessionKeyObj = await crypto.subtle.importKey(
            'raw',
            sessionKey,
            { name: 'AES-GCM', length: 256 },
            false,
            ['encrypt', 'decrypt']
        );
        
        const encrypted = await encryptDEK(dek, sessionKeyObj);
        
        return {
            encryptedDEK: base64Encode(encrypted),
            sessionKey: sessionKey // Client keeps this in memory
        };
    }
    
    // Decrypt safe DEK from session
    async function decryptDEKFromSession(encryptedDEKBase64, sessionKey) {
        const encryptedData = new Uint8Array(
base64Decode(encryptedDEKBase64)
        );
        
        const sessionKeyObj = await crypto.subtle.importKey(
            'raw',
            sessionKey,
            { name: 'AES-GCM', length: 256 },
            false,
            ['decrypt']
        );
        
        return decryptDEK(encryptedData, sessionKeyObj);
    }
    
    // Public API
    return {
        // Availability flag
        isAvailable,
        
        /**
         * Create a new safe with password protection
         * @param {string} name - Safe name
         * @param {string} password - Safe password
         * @returns {Promise<Object>} - Safe creation data for server
         */
        async createSafeWithPassword(name, password) {
            // Generate salt
            const salt = crypto.getRandomValues(new Uint8Array(SALT_SIZE));
            
            // Derive key from password
            const keyFromPassword = await deriveKeyFromPassword(password, salt);
            
            // Generate new Safe DEK
            const safeDEK = await generateSafeDEK();
            
            // Encrypt Safe DEK with password-derived key
            const encryptedDEK = await encryptDEK(safeDEK, keyFromPassword);
            
            // Create session encryption for the unlock response
            const sessionData = await this.encryptDEKForSession(safeDEK);
            
            return {
                name,
                unlock_type: 'password',
                encrypted_dek: base64Encode(encryptedDEK),
                salt: base64Encode(salt),
                session_encrypted_dek: sessionData.encryptedDEK,
                session_key: sessionData.sessionKey // Keep this in client memory
            };
        },
        
        /**
         * Create a new safe with WebAuthn protection
         * @param {string} name - Safe name
         * @param {string} credentialId - Base64-encoded WebAuthn credential ID
         * @returns {Promise<Object>} - Safe creation data for server
         */
        async createSafeWithWebAuthn(name, credentialId) {
            // Generate new Safe DEK
            const safeDEK = await generateSafeDEK();
            
            // For WebAuthn, we need to encrypt DEK with a key derived from credential
            // The actual encryption happens on the server after WebAuthn auth
            // Here we just prepare the DEK for server-side encryption
            
            // Export DEK for server to encrypt
            const rawDEK = await exportKeyRaw(safeDEK);
            
            // Create session encryption
            const sessionData = await this.encryptDEKForSession(safeDEK);
            
            return {
                name,
                unlock_type: 'webauthn',
                credential_id: credentialId,
                dek_for_server: base64Encode(rawDEK),
                session_encrypted_dek: sessionData.encryptedDEK,
                session_key: sessionData.sessionKey
            };
        },
        
        /**
         * Unlock a safe with password
         * @param {string} safeId - Safe ID
         * @param {string} password - Safe password
         * @param {string} encryptedDEKBase64 - Server-stored encrypted DEK
         * @param {string} saltBase64 - Server-stored salt
         * @returns {Promise<Object>} - Unlock result with session data
         */
        async unlockWithPassword(safeId, password, encryptedDEKBase64, saltBase64) {
            // Validate inputs
            if (!encryptedDEKBase64) {
                throw new Error('Missing encrypted_dek from server');
            }
            if (!saltBase64) {
                throw new Error('Missing salt from server');
            }
            
            // Decode data
            const encryptedDEK = base64Decode(encryptedDEKBase64);
            const salt = base64Decode(saltBase64);
            
            // Derive key from password
            const keyFromPassword = await deriveKeyFromPassword(password, salt);
            
            // Decrypt Safe DEK
            let safeDEK;
            try {
                safeDEK = await decryptDEK(encryptedDEK, keyFromPassword);
            } catch (e) {
                throw new Error('Incorrect password');
            }
            
            // Store in memory
            safeDEKs.set(safeId, {
                dek: safeDEK,
                unlocked_at: Date.now()
            });
            
            // Create session encryption
            const sessionData = await this.encryptDEKForSession(safeDEK);
            
            return {
                success: true,
                session_encrypted_dek: sessionData.encryptedDEK,
                session_key: sessionData.sessionKey
            };
        },
        
        /**
         * Prepare unlock data for WebAuthn
         * @param {string} safeId - Safe ID
         * @returns {Promise<Object>} - Challenge data from server
         */
        async prepareWebAuthnUnlock(safeId) {
            const response = await fetch(`/api/safes/unlock`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': this._getCsrfToken()
                },
                body: JSON.stringify({ safe_id: safeId })
            });
            
            if (!response.ok) {
                const error = await response.text();
                throw new Error(`Unlock preparation failed: ${error}`);
            }
            
            return await response.json();
        },
        
        /**
         * Complete WebAuthn unlock
         * @param {string} safeId - Safe ID
         * @param {Object} credential - WebAuthn credential response
         * @param {string} challenge - Challenge from prepare
         * @param {string} encryptedDEKBase64 - Server-stored encrypted DEK
         * @returns {Promise<Object>} - Unlock result
         */
        async completeWebAuthnUnlock(safeId, credential, challenge, encryptedDEKBase64) {
            // For WebAuthn, we need to derive the key from the credential
            // This is done by the server after WebAuthn verification
            // The server returns the decrypted DEK which we then store
            
            // In a real implementation, we might use the credential to derive
            // a key locally, but for simplicity we rely on server to provide
            // the encrypted DEK after successful WebAuthn auth
            
            // For now, the server will handle the decryption and send us
            // the session-encrypted DEK
            
            const response = await fetch('/api/safes/unlock/complete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': this._getCsrfToken()
                },
                body: JSON.stringify({
                    safe_id: safeId,
                    credential: credential,
                    challenge: challenge,
                    session_encrypted_dek: '' // Will be filled by server flow
                })
            });
            
            if (!response.ok) {
                const error = await response.text();
                throw new Error(`Unlock completion failed: ${error}`);
            }
            
            const result = await response.json();
            
            // Store session key for this safe (provided by server in real flow)
            // For now, we need to handle this differently
            
            return result;
        },
        
        /**
         * Store a safe's DEK from server response
         * @param {string} safeId - Safe ID
         * @param {string} sessionEncryptedDEK - Session-encrypted DEK from server
         * @param {Uint8Array} sessionKey - Session key
         */
        async storeSafeDEKFromSession(safeId, sessionEncryptedDEK, sessionKey) {
            const safeDEK = await decryptDEKFromSession(sessionEncryptedDEK, sessionKey);
            
            safeDEKs.set(safeId, {
                dek: safeDEK,
                unlocked_at: Date.now(),
                session_key: sessionKey
            });
        },
        
        /**
         * Get safe DEK (if unlocked)
         * @param {string} safeId - Safe ID
         * @returns {CryptoKey|null} - Safe DEK or null if locked
         */
        getSafeDEK(safeId) {
            const entry = safeDEKs.get(safeId);
            return entry ? entry.dek : null;
        },
        
        /**
         * Check if safe is unlocked
         * @param {string} safeId - Safe ID
         * @returns {boolean}
         */
        isUnlocked(safeId) {
            return safeDEKs.has(safeId);
        },
        
        /**
         * Lock a safe (clear from memory)
         * @param {string} safeId - Safe ID
         */
        lockSafe(safeId) {
            safeDEKs.delete(safeId);
        },
        
        /**
         * Lock all safes
         */
        lockAll() {
            safeDEKs.clear();
        },
        
        /**
         * Get list of unlocked safe IDs
         * @returns {string[]}
         */
        getUnlockedSafeIds() {
            return Array.from(safeDEKs.keys());
        },
        
        /**
         * Encrypt a file for safe upload
         * @param {File} file - File to encrypt
         * @param {string} safeId - Safe ID
         * @returns {Promise<Object>} - Encrypted file data
         */
        async encryptFileForSafe(file, safeId) {
            const safeDEK = this.getSafeDEK(safeId);
            if (!safeDEK) {
                throw new Error('Safe is locked. Please unlock first.');
            }
            
            // Read file
            const fileData = new Uint8Array(await file.arrayBuffer());
            
            // Encrypt file directly with Safe DEK (simpler approach)
            const iv = crypto.getRandomValues(new Uint8Array(12));
            const encryptedData = await crypto.subtle.encrypt(
                { name: 'AES-GCM', iv: iv },
                safeDEK,
                fileData
            );
            
            // Combine IV + encrypted data
            const encryptedFile = new Uint8Array(iv.length + encryptedData.byteLength);
            encryptedFile.set(iv);
            encryptedFile.set(new Uint8Array(encryptedData), iv.length);
            
            return {
                encryptedFile: new Blob([encryptedFile]),
                originalName: file.name,
                mediaType: file.type.startsWith('video/') ? 'video' : 'image',
                mimeType: file.type
            };
        },
        
        /**
         * Decrypt a file from safe (using safe DEK directly)
         * @param {Blob} encryptedBlob - Encrypted file blob
         * @param {string} safeId - Safe ID
         * @param {string} mimeType - Original MIME type (optional)
         * @returns {Promise<Blob>} - Decrypted file
         */
        async decryptFileFromSafe(encryptedBlob, safeId, mimeType = 'image/jpeg') {
            const safeDEK = this.getSafeDEK(safeId);
            if (!safeDEK) {
                throw new Error('Safe is locked. Please unlock first.');
            }
            
            // Decrypt file directly with safe DEK
            const encryptedData = new Uint8Array(await encryptedBlob.arrayBuffer());
            const iv = encryptedData.slice(0, 12);
            const ciphertext = encryptedData.slice(12);
            
            const decrypted = await crypto.subtle.decrypt(
                { name: 'AES-GCM', iv: iv },
                safeDEK,
                ciphertext
            );
            
            return new Blob([decrypted], { type: mimeType });
        },
        
        /**
         * Helper: Encrypt DEK for session storage
         * @param {CryptoKey} dek - DEK to encrypt
         * @returns {Promise<Object>} - Encrypted DEK and session key
         */
        async encryptDEKForSession(dek) {
            const sessionKey = await generateSessionKey();
            const sessionKeyObj = await crypto.subtle.importKey(
                'raw',
                sessionKey,
                { name: 'AES-GCM', length: 256 },
                false,
                ['encrypt']
            );
            
            const encrypted = await encryptDEK(dek, sessionKeyObj);
            
            return {
                encryptedDEK: base64Encode(encrypted),
                sessionKey: sessionKey
            };
        },
        
        /**
         * Get CSRF token from meta tag or cookie
         */
        _getCsrfToken() {
            const meta = document.querySelector('meta[name="csrf-token"]');
            if (meta) return meta.getAttribute('content');
            
            const match = document.cookie.match(/synth_csrf=([^;]+)/);
            return match ? match[1] : '';
        }
    };
})();

// Auto-clear on page unload
window.addEventListener('beforeunload', () => {
    SafeCrypto.lockAll();
});

// Make global
window.SafeCrypto = SafeCrypto;

// Export
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { SafeCrypto };
}

console.log('safe-crypto.js loaded, SafeCrypto available:', SafeCrypto.isAvailable);
if (!SafeCrypto.isAvailable) {
    console.warn('SafeCrypto is not available. This is likely because the page is not served over HTTPS or localhost.');
}
