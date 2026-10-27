/**
 * Global hotkey registry.
 *
 * Design: a single document-level keydown listener dispatches to handlers
 * registered per normalised combo string.  Callers register shortcuts via
 * `Hotkeys.register(combo, handler, opts)` where `combo` is a human-readable
 * key combination and `opts.when` is an optional context predicate.
 *
 * Canonical combo format: modifiers in the fixed order Ctrl+Alt+Shift+Meta
 * followed by the key name, joined with '+'.  Examples: `Shift+D`,
 * `Ctrl+Alt+T`, `Escape`, `ArrowLeft`.  Single-character keys are uppercased;
 * named keys keep their KeyboardEvent spelling (`Escape`, `Enter`,
 * `ArrowLeft`, `F2`, …).
 *
 * `preventDefault` fires only when a binding actually runs (after the `when`
 * gate passes).
 */

(function() {
    'use strict';

    // ---------------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------------

    const bindings = new Map();

    // Modifier alias map (input → canonical name)
    var MODIFIER_ALIASES = {
        control: 'Ctrl',
        ctrl: 'Ctrl',
        option: 'Alt',
        alt: 'Alt',
        shift: 'Shift',
        meta: 'Meta',
        command: 'Meta',
        cmd: 'Meta'
    };

    // Named-key aliases (input → canonical)
    var KEY_ALIASES = {
        esc: 'Escape'
    };

    // Canonical modifier order
    var MODIFIER_ORDER = ['Ctrl', 'Alt', 'Shift', 'Meta'];

    // ---------------------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------------------

    /**
     * Normalize a single key token to its canonical form.
     */
    function normalizeKey(key) {
        if (key.length === 1) {
            return key.toUpperCase();
        }
        return KEY_ALIASES[key.toLowerCase()] || key;
    }

    // ---------------------------------------------------------------------------
    // Public API
    // ---------------------------------------------------------------------------

    /**
     * Convert a combo string to canonical form.
     * Split on '+', trim parts, map modifier aliases, normalize the key part.
     * Reorder modifiers to Ctrl,Alt,Shift,Meta order.
     * Throws Error on empty/whitespace combo or empty key part.
     */
    function normalizeCombo(combo) {
        if (!combo || typeof combo !== 'string') {
            throw new Error('Invalid combo: must be a non-empty string');
        }
        var trimmed = combo.trim();
        if (trimmed.length === 0) {
            throw new Error('Invalid combo: empty/whitespace');
        }

        var parts = trimmed.split('+').map(function(p) { return p.trim(); });
        if (parts.length === 0) {
            throw new Error('Invalid combo: no parts');
        }

        var modifiers = [];
        var keyPart = null;

        for (var i = 0; i < parts.length; i++) {
            var part = parts[i];
            if (i === parts.length - 1) {
                // Last part is always the key
                if (!part || part.trim().length === 0) {
                    throw new Error('Invalid combo: empty key part');
                }
                keyPart = normalizeKey(part);
            } else {
                var mod = MODIFIER_ALIASES[part.toLowerCase()];
                if (mod) {
                    modifiers.push(mod);
                } else {
                    // Treat unknown modifier as-is (shouldn't normally happen)
                    modifiers.push(normalizeKey(part));
                }
            }
        }

        if (!keyPart) {
            throw new Error('Invalid combo: no key specified');
        }

        // Deduplicate modifiers while preserving canonical order
        var seen = {};
        var ordered = [];
        for (var i = 0; i < MODIFIER_ORDER.length; i++) {
            var m = MODIFIER_ORDER[i];
            if (modifiers.indexOf(m) !== -1 && !seen[m]) {
                seen[m] = true;
                ordered.push(m);
            }
        }
        // Any modifiers not in the canonical list (shouldn't happen)
        for (var i = 0; i < modifiers.length; i++) {
            var m2 = modifiers[i];
            if (!seen[m2]) {
                seen[m2] = true;
                ordered.push(m2);
            }
        }

        if (ordered.length > 0) {
            return ordered.join('+') + '+' + keyPart;
        }
        return keyPart;
    }

    /**
     * Build canonical combo from a KeyboardEvent-like object.
     * Must satisfy: normalizeCombo(comboFromEvent(e)) === comboFromEvent(e).
     */
    function comboFromEvent(e) {
        var modifiers = [];
        if (e.ctrlKey) modifiers.push('Ctrl');
        if (e.altKey) modifiers.push('Alt');
        if (e.shiftKey) modifiers.push('Shift');
        if (e.metaKey) modifiers.push('Meta');

        var keyPart = normalizeKey(e.key);
        if (modifiers.length > 0) {
            return modifiers.join('+') + '+' + keyPart;
        }
        return keyPart;
    }

    /**
     * Check whether a target element is "editable" (input, textarea, select,
     * or contentEditable). Works with plain mock objects — uses String(tagName)
     * and isContentEditable fields only.
     */
    function isEditableTarget(target) {
        if (!target || !target.tagName) return false;
        var tag = String(target.tagName).toUpperCase();
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
        if (target.isContentEditable === true) return true;
        return false;
    }

    /**
     * Register a hotkey binding.
     * @param {string} combo - key combination string
     * @param {Function} handler - callback(e)
     * @param {Object} [opts] - optional { when: () => boolean } context predicate
     * @returns {Function} unregister function
     */
    function register(combo, handler, opts) {
        var canonical = normalizeCombo(combo);
        if (!bindings.has(canonical)) {
            bindings.set(canonical, []);
        }
        var entries = bindings.get(canonical);
        entries.push({ handler: handler, when: (opts && opts.when) || null });
        // Closure over the ORIGINAL combo argument and handler reference
        var origCombo = combo;
        var origHandler = handler;
        return function () {
            unregister(origCombo, origHandler);
        };
    }

    /**
     * Unregister a hotkey binding.
     * @param {string} combo - key combination string (normalized)
     * @param {Function} [handler] - specific handler to remove; omit to clear all
     */
    function unregister(combo, handler) {
        var canonical = normalizeCombo(combo);
        if (!bindings.has(canonical)) return;

        if (handler === undefined) {
            // Remove all entries for this combo
            bindings.delete(canonical);
            return;
        }

        var entries = bindings.get(canonical);
        for (var i = entries.length - 1; i >= 0; i--) {
            if (entries[i].handler === handler) {
                entries.splice(i, 1);
            }
        }
        if (entries.length === 0) {
            bindings.delete(canonical);
        }
    }

    /**
     * Dispatch logic — callable directly (tests call it with mock events).
     */
    function handleKeydown(e) {
        // Skip editable targets
        if (isEditableTarget(e.target)) return;

        var canonical = comboFromEvent(e);
        var entries = bindings.get(canonical);
        if (!entries || entries.length === 0) return;

        var active = [];
        for (var i = 0; i < entries.length; i++) {
            if (!entries[i].when || entries[i].when()) {
                active.push(entries[i]);
            }
        }
        if (active.length === 0) return;

        e.preventDefault();
        for (var i = 0; i < active.length; i++) {
            active[i].handler(e);
        }
    }

    /** Clear the whole map (test isolation). */
    function reset() {
        bindings.clear();
    }

    // ---------------------------------------------------------------------------
    // IIFE bottom: attach global listener, then export
    // ---------------------------------------------------------------------------

    document.addEventListener('keydown', handleKeydown);

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            normalizeCombo: normalizeCombo,
            comboFromEvent: comboFromEvent,
            isEditableTarget: isEditableTarget,
            register: register,
            unregister: unregister,
            handleKeydown: handleKeydown,
            reset: reset
        };
    }

    window.Hotkeys = {
        normalizeCombo: normalizeCombo,
        comboFromEvent: comboFromEvent,
        isEditableTarget: isEditableTarget,
        register: register,
        unregister: unregister,
        handleKeydown: handleKeydown,
        reset: reset
    };
})();
