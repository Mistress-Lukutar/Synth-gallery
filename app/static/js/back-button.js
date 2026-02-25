/**
 * Back Button Manager - Centralized handler for Escape key and mobile back button
 * 
 * Manages a stack of open modals/panels/lightboxes and handles:
 * - Escape key: closes the topmost modal in the stack
 * - Mobile back button (popstate): same behavior as Escape
 * 
 * Usage:
 *   BackButtonManager.register('modal-id', closeCallback, { backState: {...} })
 *   BackButtonManager.unregister('modal-id')  // call when closing normally
 *   BackButtonManager.forceCloseAll()         // close everything (e.g., on page unload)
 */

(function() {
    'use strict';

    // Stack of registered modals/panels (LIFO)
    const stack = [];
    
    // Track if we're currently handling a popstate to avoid double-close
    let isHandlingPopstate = false;
    
    // Track if Escape was just pressed (to coordinate with other handlers)
    let escapePressed = false;

    /**
     * Register a new modal/panel/lightbox with the manager
     * @param {string} id - Unique identifier for this modal
     * @param {Function} closeCallback - Function to call when closing (via Escape or back button)
     * @param {Object} options - Optional settings
     * @param {Object} options.backState - State object to push to history (default: { modalId: id })
     * @param {boolean} options.preventDoublePush - If true, won't push state if same id is already top
     * @param {boolean} options.skipHistoryPush - If true, won't push state (use when caller already did pushState)
     */
    function register(id, closeCallback, options = {}) {
        // Remove any existing registration with same id (shouldn't happen, but safety first)
        const existingIndex = stack.findIndex(item => item.id === id);
        if (existingIndex !== -1) {
            stack.splice(existingIndex, 1);
        }

        // Add to stack
        const item = {
            id: id,
            closeCallback: closeCallback,
            backState: options.backState || { modalId: id },
            timestamp: Date.now()
        };
        stack.push(item);
        
        console.log('[BackButtonManager] Registered:', id, 'Stack:', stack.map(i => i.id));

        // Push history state for back button support
        // This allows mobile users to use the back button to close modals
        if (!options.skipHistoryPush && (!options.preventDoublePush || existingIndex === -1)) {
            try {
                history.pushState({ ...item.backState, _backButtonManaged: true }, '', location.href);
            } catch (e) {
                console.warn('[BackButtonManager] Failed to push history state:', e);
            }
        }

        console.log('[BackButtonManager] Registered:', id, 'Stack size:', stack.length);
    }

    /**
     * Unregister a modal/panel when it's closed normally (not via Escape/back button)
     * @param {string} id - The modal id to unregister
     * @param {boolean} skipHistoryBack - If true, won't call history.back() (use when user already navigated)
     */
    function unregister(id, skipHistoryBack = false) {
        const index = stack.findIndex(item => item.id === id);
        if (index === -1) return;

        // Remove from stack
        stack.splice(index, 1);
        console.log('[BackButtonManager] Unregistered:', id, 'Stack:', stack.map(i => i.id));

        // If this was the top item and we're not handling a popstate, 
        // we need to pop the history entry we created
        if (!skipHistoryBack && !isHandlingPopstate && index === stack.length) {
            try {
                // Only go back if our state is on top
                const currentState = history.state;
                if (currentState && currentState._backButtonManaged && currentState.modalId === id) {
                    history.back();
                }
            } catch (e) {
                console.warn('[BackButtonManager] Failed to go back in history:', e);
            }
        }
    }

    /**
     * Close the topmost modal/panel (called by Escape or back button)
     * @returns {boolean} - True if something was closed
     */
    function closeTopmost() {
        if (stack.length === 0) return false;

        const item = stack[stack.length - 1];
        console.log('[BackButtonManager] Closing topmost:', item.id);

        // Mark that we're handling this close to prevent unregister from calling history.back()
        isHandlingPopstate = true;

        // Call the close callback
        // Note: callback will call unregister() which removes item from stack
        try {
            item.closeCallback();
        } catch (e) {
            console.error('[BackButtonManager] Error in close callback:', e);
        }

        // Note: We do NOT do stack.pop() here because the callback's unregister() 
        // already removes the item from the stack. Doing pop() here would remove
        // the wrong item (the next one in stack) after unregister already ran.

        // Reset flag after a brief delay
        setTimeout(() => {
            isHandlingPopstate = false;
        }, 0);

        return true;
    }

    /**
     * Force close all registered modals/panels
     * Useful for page unload or emergency cleanup
     */
    function forceCloseAll() {
        console.log('[BackButtonManager] Force closing all, count:', stack.length);
        
        // Close in reverse order (top to bottom)
        while (stack.length > 0) {
            const item = stack.pop();
            try {
                item.closeCallback();
            } catch (e) {
                console.error('[BackButtonManager] Error force closing:', item.id, e);
            }
        }
    }

    /**
     * Get current stack info (for debugging)
     */
    function getStackInfo() {
        return {
            size: stack.length,
            items: stack.map(i => i.id)
        };
    }

    /**
     * Debug: log current stack to console
     */
    function debugStack() {
        console.log('[BackButtonManager] Stack:', stack.length, 'items:', stack.map(i => i.id));
    }

    /**
     * Check if a specific modal is currently registered
     */
    function isRegistered(id) {
        return stack.some(item => item.id === id);
    }

    /**
     * Check if any modal is currently open
     */
    function hasOpenModals() {
        return stack.length > 0;
    }

    // ===== Event Handlers =====

    /**
     * Handle Escape key press
     */
    function handleEscape(e) {
        if (e.key !== 'Escape') return;
        
        // Set flag so other handlers can check
        escapePressed = true;
        
        // If we have registered modals, close the topmost one
        if (stack.length > 0) {
            e.preventDefault();
            e.stopPropagation();
            closeTopmost();
        }

        // Reset flag after processing
        setTimeout(() => {
            escapePressed = false;
        }, 0);
    }

    /**
     * Handle browser back button (popstate)
     */
    function handlePopstate(e) {
        const state = e.state;
        console.log('[BackButtonManager] Popstate, state:', state, 'stack:', stack.length);
        
        // If we have registered modals, close the topmost one instead of navigating
        if (stack.length > 0) {
            // Prevent default navigation by closing the modal
            const wasClosed = closeTopmost();
            
            if (wasClosed) {
                // Push state to replace the one we just popped
                // This keeps user on the same page
                try {
                    history.pushState({ _backButtonTrap: true, afterClose: true }, '', location.href);
                } catch (err) {
                    console.warn('[BackButtonManager] Failed to push trap state:', err);
                }
            }
        } else {
            // No modals open - this is a "guard" state pop
            // Re-push the guard state to prevent exiting the app
            if (state && state._backButtonTrap) {
                console.log('[BackButtonManager] Guard state popped, re-trapping');
                try {
                    history.pushState({ _backButtonTrap: true, guard: true }, '', location.href);
                } catch (err) {
                    console.warn('[BackButtonManager] Failed to re-push guard:', err);
                }
            }
        }
    }

    // ===== Setup Event Listeners =====

    // Use capture phase to ensure we get Escape before other handlers
    document.addEventListener('keydown', handleEscape, true);

    // Listen for popstate (back/forward buttons)
    window.addEventListener('popstate', handlePopstate);

    // Cleanup on page unload
    window.addEventListener('beforeunload', () => {
        forceCloseAll();
    });

    // Mobile back button trap: push initial state to prevent exiting app
    // This creates a "trap" so back button doesn't exit the app immediately
    function setupMobileBackTrap() {
        // Only setup once
        if (window._backButtonTrapSetup) return;
        window._backButtonTrapSetup = true;
        
        // Push a state so we have something to pop
        // This prevents the back button from exiting the app
        if (!history.state || !history.state._backButtonTrap) {
            try {
                history.replaceState({ _backButtonTrap: true, initial: true }, '', location.href);
                history.pushState({ _backButtonTrap: true, guard: true }, '', location.href);
                console.log('[BackButtonManager] Mobile back trap installed');
            } catch (e) {
                console.warn('[BackButtonManager] Failed to setup back trap:', e);
            }
        }
    }
    
    // Setup trap after page loads
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setupMobileBackTrap);
    } else {
        setupMobileBackTrap();
    }

    // ===== Export Public API =====
    window.BackButtonManager = {
        register,
        unregister,
        closeTopmost,
        forceCloseAll,
        getStackInfo,
        debugStack,
        isRegistered,
        hasOpenModals,
        
        // Internal flag for coordination with legacy handlers
        get escapePressed() { return escapePressed; }
    };

    console.log('[back-button.js] Loaded and initialized');
})();
