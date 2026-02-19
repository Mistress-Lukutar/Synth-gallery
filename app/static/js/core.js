/**
 * Core utilities - CSRF protection, fetch helpers, common functions
 * Phase 1: Infrastructure Setup
 */

// CSRF protection helper
function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    if (meta) return meta.getAttribute('content');
    const match = document.cookie.match(/synth_csrf=([^;]+)/);
    return match ? match[1] : '';
}

// Enhanced fetch with CSRF token
async function csrfFetch(url, options = {}) {
    const csrfToken = getCsrfToken();
    const headers = options.headers || {};

    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes((options.method || 'GET').toUpperCase())) {
        headers['X-CSRF-Token'] = csrfToken;
    }

    return fetch(url, { ...options, headers });
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

console.log('[core.js] Loaded');
