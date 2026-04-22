/**
 * Core utilities - CSRF protection, fetch helpers, common functions
 * Phase 1: Infrastructure Setup
 */

// CSRF protection helper
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) {
        const content = meta.getAttribute('content');
        return content;
    }
    const match = document.cookie.match(/synth_csrf=([^;]+)/);
    return match ? match[1] : '';
}

// Enhanced fetch with CSRF token and credentials
async function csrfFetch(url, options = {}) {
    const csrfToken = getCsrfToken();
    
    // Merge headers: options.headers first, then CSRF token
    const headers = {
        ...options.headers,
    };

    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes((options.method || 'GET').toUpperCase())) {
        headers['X-CSRF-Token'] = csrfToken;
    }

    return fetch(url, { 
        ...options, 
        credentials: 'include',  // Important: send session cookies
        headers
    });
}

// Escape HTML to prevent XSS
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Get base URL from global or compute
function getBaseUrl() {
    return window.SYNTH_BASE_URL || '';
}

// Export to window
window.getCsrfToken = getCsrfToken;
window.csrfFetch = csrfFetch;
window.escapeHtml = escapeHtml;
window.getBaseUrl = getBaseUrl;

// Handle image load errors (403, etc.) - show placeholder with lock icon
window.handleImageError = function(img, errorType = 'access', context = {}) {
    // Prevent infinite loop if placeholder itself fails
    if (img.dataset.errorHandled) return;
    img.dataset.errorHandled = 'true';
    
    // Hide the broken image
    img.style.display = 'none';
    
    // Find or create error placeholder
    const container = img.closest('.gallery-link');
    if (!container) return;
    
    // Get gallery item to mark access state
    const galleryItem = img.closest('.gallery-item');
    
    // Remove loading placeholder if exists
    const loadingPlaceholder = container.querySelector('.gallery-placeholder');
    if (loadingPlaceholder) loadingPlaceholder.style.display = 'none';
    
    // Check if error placeholder already exists
    let errorPlaceholder = container.querySelector('.gallery-placeholder-error');
    if (errorPlaceholder) return; // Already showing error
    
    // Create error placeholder
    errorPlaceholder = document.createElement('div');
    errorPlaceholder.className = 'gallery-placeholder-error';
    
    // Determine error type from context
    const safeId = galleryItem?.dataset.safeId || img.dataset.safeId;
    const isLockedSafe = safeId && errorType === 'locked';
    const isSharedDenied = !safeId && errorType === 'access';
    
    const messages = {
        'access': { icon: 'shield', text: 'Access denied' },
        'locked': { icon: 'lock', text: 'Safe locked' },
        'shared': { icon: 'shield', text: 'Shared content unavailable' }
    };
    
    const msg = messages[errorType] || messages['access'];
    
    // Mark gallery item with access state for click handlers
    if (galleryItem) {
        galleryItem.classList.add('has-error');
        if (isLockedSafe) {
            galleryItem.dataset.access = 'locked';
            galleryItem.dataset.safeId = safeId;
        } else if (isSharedDenied) {
            galleryItem.dataset.access = 'denied';
        }
    }
    
    // Use lock or shield icon
    const iconSvg = msg.icon === 'lock' 
        ? '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>'
        : '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>';
    
    errorPlaceholder.innerHTML = `
        <svg class="error-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
            ${iconSvg}
        </svg>
        <span class="error-text">${msg.text}</span>
    `;
    
    container.appendChild(errorPlaceholder);
};

/**
 * Bind Enter key on input to trigger button click
 * Usage: bindEnterKey('input-id', 'button-id')
 */
function bindEnterKey(inputId, buttonId) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(buttonId);
    if (!input || !button) return;

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !button.disabled) {
            e.preventDefault();
            button.click();
        }
    });
}

// Auto-bind via data attribute: data-submit-on-enter="#button-id"
// or data-submit-on-enter="button-id" (without #)
document.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;

    const input = e.target;
    if (!input.matches('input[data-submit-on-enter]')) return;

    const btnRef = input.getAttribute('data-submit-on-enter');
    const btnId = btnRef.startsWith('#') ? btnRef.slice(1) : btnRef;
    const button = document.getElementById(btnId);

    if (button && !button.disabled) {
        e.preventDefault();
        button.click();
    }
});

/**
 * Copy text to clipboard with fallbacks for mobile and legacy browsers.
 * Returns true if copied successfully, false otherwise.
 */
async function copyToClipboard(text) {
    // 1. Try modern Clipboard API (requires secure context)
    if (navigator.clipboard && window.isSecureContext) {
        try {
            await navigator.clipboard.writeText(text);
            console.log('[clipboard] Copied using navigator.clipboard API');
            return true;
        } catch (err) {
            console.warn('[clipboard] navigator.clipboard failed:', err);
        }
    } else {
        console.log('[clipboard] navigator.clipboard not available (non-secure context or unsupported)');
    }

    // 2. Fallback: execCommand('copy')
    try {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        // Position off-screen but keep focusable
        textArea.style.position = 'fixed';
        textArea.style.left = '-9999px';
        textArea.style.top = '0';
        textArea.setAttribute('readonly', '');
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();

        const successful = document.execCommand('copy');
        document.body.removeChild(textArea);

        if (successful) {
            console.log('[clipboard] Copied using execCommand fallback');
            return true;
        }
        console.warn('[clipboard] execCommand returned false');
    } catch (err) {
        console.warn('[clipboard] execCommand fallback failed:', err);
    }

    // 3. All automated methods failed
    console.warn('[clipboard] All automated clipboard methods failed');
    return false;
}

// Export to window
window.bindEnterKey = bindEnterKey;
window.copyToClipboard = copyToClipboard;

