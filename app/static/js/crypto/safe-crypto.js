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

var SafeCrypto = (function() {
    'use strict';
    

    
    // Get crypto from window (for browsers that don't expose it globally in strict mode)
    const crypto = window.crypto || window.msCrypto;
    

    
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
    
    // Check dependencies (lazy check - DEKManager might load after SafeCrypto)

    
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
         * Create a new safe with WebAuthn protection using PRF extension
         * @param {string} name - Safe name
         * @param {string} credentialId - Base64-encoded WebAuthn credential ID
         * @param {Object} credential - WebAuthn credential object with PRF results
         * @returns {Promise<Object>} - Safe creation data for server
         */
        async createSafeWithWebAuthn(name, credentialId, credential) {
            // Get PRF results from the credential
            const prfResults = credential?.clientExtensionResults?.prf?.results?.first ||
                              credential?.clientExtensionResults?.prf?.results;
            
            if (!prfResults) {
                throw new Error('PRF results not available. Your hardware key may not support PRF extension.');
            }
            
            // Generate new Safe DEK
            const safeDEK = await generateSafeDEK();
            
            // Use PRF output as encryption key
            const encryptionKey = new Uint8Array(prfResults);
            
            // Export and encrypt DEK
            const rawDEK = await exportKeyRaw(safeDEK);
            
            // Encrypt DEK using PRF output (XOR for simplicity, in production use AES)
            const encryptedDEK = new Uint8Array(rawDEK.length);
            for (let i = 0; i < rawDEK.length; i++) {
                encryptedDEK[i] = rawDEK[i] ^ encryptionKey[i % encryptionKey.length];
            }
            
            // Create session encryption for server-side session
            const sessionData = await this.encryptDEKForSession(safeDEK);
            
            return {
                name,
                unlock_type: 'webauthn',
                credential_id: credentialId,
                encrypted_dek: base64Encode(encryptedDEK),
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
                console.error(`[SafeCrypto.unlockWithPassword] Failed to decrypt DEK:`, e);
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
         * Unlock a safe with WebAuthn using PRF extension (zero-trust)
         * @param {string} safeId - Safe ID
         * @param {string} credentialId - Base64-encoded credential ID
         * @param {string} encryptedDEKBase64 - Server-stored encrypted DEK (encrypted with PRF)
         * @returns {Promise<Object>} - Unlock result
         */
        async unlockWithWebAuthn(safeId, credentialId, encryptedDEKBase64) {
            
            // Get PRF output to decrypt DEK
            const prfInput = new Uint8Array(32);
            prfInput.fill(0x01); // Same input as used for encryption
            
            const credential = await navigator.credentials.get({
                publicKey: {
                    challenge: crypto.getRandomValues(new Uint8Array(32)),
                    allowCredentials: [{ id: base64Decode(credentialId), type: 'public-key' }],
                    userVerification: 'required',
                    extensions: {
                        prf: { eval: { first: prfInput } }
                    }
                }
            });
            
            if (!credential?.clientExtensionResults?.prf?.results?.first) {
                throw new Error('Failed to get PRF output from hardware key');
            }
            
            // Use PRF output as decryption key
            const encryptionKey = new Uint8Array(credential.clientExtensionResults.prf.results.first);
            
            // Decrypt DEK
            const encryptedDEK = base64Decode(encryptedDEKBase64);
            const rawDEK = new Uint8Array(encryptedDEK.length);
            for (let i = 0; i < encryptedDEK.length; i++) {
                rawDEK[i] = encryptedDEK[i] ^ encryptionKey[i % encryptionKey.length];
            }
            
            // Import DEK
            const safeDEK = await crypto.subtle.importKey(
                'raw',
                rawDEK,
                { name: 'AES-GCM', length: 256 },
                false,
                ['encrypt', 'decrypt']
            );
            
            // Store in memory
            safeDEKs.set(safeId, {
                dek: safeDEK,
                unlocked_at: Date.now()
            });
            
            // Create session encryption for server
            const sessionData = await this.encryptDEKForSession(safeDEK);
            
            return {
                success: true,
                session_encrypted_dek: sessionData.encryptedDEK,
                session_key: sessionData.sessionKey
            };
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
         * Store a safe's DEK directly (for WebAuthn unlock)
         * @param {string} safeId - Safe ID
         * @param {CryptoKey} safeDEK - The safe's DEK CryptoKey
         */
        storeSafeDEKDirect(safeId, safeDEK) {
            safeDEKs.set(safeId, {
                dek: safeDEK,
                unlocked_at: Date.now()
            });
        },
        
        /**
         * Get safe DEK (if unlocked)
         * @param {string} safeId - Safe ID
         * @returns {CryptoKey|null} - Safe DEK or null if locked
         */
        getSafeDEK(safeId) {
            const entry = safeDEKs.get(safeId);
            if (entry) {
            }
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
         * Lock a safe (clear from memory and notify server)
         * @param {string} safeId - Safe ID
         * @param {boolean} notifyServer - Whether to notify server (default true)
         */
        lockSafe(safeId, notifyServer = true) {
            safeDEKs.delete(safeId);
            
            // Notify server to invalidate session
            if (notifyServer && typeof navigator !== 'undefined' && navigator.sendBeacon) {
                const csrfToken = this._getCsrfToken();
                const baseUrl = (typeof getBaseUrl === 'function') ? getBaseUrl() : '';
                const url = `${baseUrl}/api/safes/${safeId}/lock`;
                const headers = { 'X-CSRF-Token': csrfToken };
                navigator.sendBeacon(url, new Blob([JSON.stringify({})], { 
                    type: 'application/json' 
                }));
            }
        },
        
        /**
         * Lock all safes
         * @param {boolean} notifyServer - Whether to notify server (default true)
         */
        lockAll(notifyServer = true) {
            if (notifyServer && safeDEKs.size > 0) {
                const safeIds = Array.from(safeDEKs.keys());
                const csrfToken = this._getCsrfToken();
                const baseUrl = (typeof getBaseUrl === 'function') ? getBaseUrl() : '';
                
                // Notify server for each safe
                if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
                    for (const safeId of safeIds) {
                        const url = `${baseUrl}/api/safes/${safeId}/lock`;
                        navigator.sendBeacon(url, new Blob([JSON.stringify({})], { 
                            type: 'application/json' 
                        }));
                    }
                }
            }
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
         * Generate thumbnail from image/video file using Canvas
         * @param {File} file - The media file
         * @param {number} maxSize - Max thumbnail dimension
         * @returns {Promise<Blob>} - JPEG thumbnail blob
         */
        async _generateThumbnail(file, maxSize = 400) {
            return new Promise((resolve, reject) => {
                const isVideo = file.type.startsWith('video/');
                const isImage = file.type.startsWith('image/');
                
                if (isVideo) {
                    this._generateVideoThumbnail(file, maxSize)
                        .then(blob => { resolve(blob); })
                        .catch(err => { console.error(`[_generateThumbnail] Video thumbnail failed:`, err); reject(err); });
                } else if (isImage) {
                    this._generateImageThumbnail(file, maxSize)
                        .then(blob => { resolve(blob); })
                        .catch(err => { console.error(`[_generateThumbnail] Image thumbnail failed:`, err); reject(err); });
                } else {
                    console.error(`[_generateThumbnail] Unsupported file type: ${file.type}`);
                    reject(new Error('Unsupported file type for thumbnail'));
                }
            });
        },
        
        /**
         * Generate thumbnail from image
         * @private
         */
        async _generateImageThumbnail(file, maxSize) {
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
                
                img.onerror = (err) => {
                    console.error(`[_generateImageThumbnail] Image load error:`, err);
                    URL.revokeObjectURL(url);
                    reject(new Error('Failed to load image'));
                };
                
                img.src = url;
            });
        },
        
        /**
         * Generate thumbnail from video
         * @private
         */
        async _generateVideoThumbnail(file, maxSize) {
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
            
            // Generate thumbnail first (before encryption)
            let thumbnailBlob = null;
            let thumbnailDimensions = { width: 0, height: 0 };
            
            try {
                thumbnailBlob = await this._generateThumbnail(file, 400);
                if (thumbnailBlob) {
                    // Get dimensions from the blob
                    const img = await this._loadImageFromBlob(thumbnailBlob);
                    thumbnailDimensions = { width: img.width, height: img.height };
                }
            } catch (e) {
                console.warn(`[SafeCrypto.encryptFileForSafe] Thumbnail generation failed:`, e);
                thumbnailBlob = null;
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
            
            // Encrypt thumbnail if generated
            let encryptedThumbnail = null;
            if (thumbnailBlob) {
                try {
                    const thumbData = new Uint8Array(await thumbnailBlob.arrayBuffer());
                    const thumbIv = crypto.getRandomValues(new Uint8Array(12));
                    const encryptedThumbData = await crypto.subtle.encrypt(
                        { name: 'AES-GCM', iv: thumbIv },
                        safeDEK,
                        thumbData
                    );
                    
                    // Combine IV + encrypted thumbnail data
                    encryptedThumbnail = new Uint8Array(thumbIv.length + encryptedThumbData.byteLength);
                    encryptedThumbnail.set(thumbIv);
                    encryptedThumbnail.set(new Uint8Array(encryptedThumbData), thumbIv.length);
                } catch (e) {
                    console.warn(`[SafeCrypto.encryptFileForSafe] Thumbnail encryption failed:`, e);
                }
            }
            
            const result = {
                encryptedFile: new Blob([encryptedFile]),
                encryptedThumbnail: encryptedThumbnail ? new Blob([encryptedThumbnail]) : null,
                originalName: file.name,
                mediaType: file.type.startsWith('video/') ? 'video' : 'image',
                mimeType: file.type,
                thumbWidth: thumbnailDimensions.width,
                thumbHeight: thumbnailDimensions.height
            };
            return result;
        },
        
        /**
         * Load image from blob to get dimensions
         * @private
         */
        _loadImageFromBlob(blob) {
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
                console.error(`[SafeCrypto.decryptFileFromSafe] Safe ${safeId} is locked`);
                throw new Error('Safe is locked. Please unlock first.');
            }
            
            // Decrypt file directly with safe DEK
            const encryptedData = new Uint8Array(await encryptedBlob.arrayBuffer());
            
            const iv = encryptedData.slice(0, 12);
            const ciphertext = encryptedData.slice(12);
            
            try {
                const decrypted = await crypto.subtle.decrypt(
                    { name: 'AES-GCM', iv: iv },
                    safeDEK,
                    ciphertext
                );
                return new Blob([decrypted], { type: mimeType });
            } catch (e) {
                console.error(`[SafeCrypto.decryptFileFromSafe] Decryption failed:`, e);
                throw e;
            }
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

if (!SafeCrypto.isAvailable) {
    console.warn('SafeCrypto is not available. This is likely because the page is not served over HTTPS or localhost.');
}
