/**
 * Core utilities - CSRF protection, fetch helpers, common functions
 * Phase 1: Infrastructure Setup
 */

// CSRF protection helper
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    console.log('[CSRF] Meta tag:', meta);
    if (meta) {
        const content = meta.getAttribute('content');
        console.log('[CSRF] Meta content:', content);
        return content;
    }
    const match = document.cookie.match(/synth_csrf=([^;]+)/);
    console.log('[CSRF] Cookie match:', match);
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

console.log('[core.js] Loaded');
