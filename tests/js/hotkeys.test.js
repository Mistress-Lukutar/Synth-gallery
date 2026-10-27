/**
 * Unit tests for the global hotkey registry (hotkeys.js).
 */
const {
    normalizeCombo,
    comboFromEvent,
    isEditableTarget,
    register,
    unregister,
    handleKeydown,
    reset,
} = require('../../app/static/js/hotkeys.js');

// ---------------------------------------------------------------------------
// normalizeCombo
// ---------------------------------------------------------------------------

describe('normalizeCombo', () => {
    test('lower-cases single-char keys and upper-cases letter keys: "shift+d" -> "Shift+D"', () => {
        expect(normalizeCombo('shift+d')).toBe('Shift+D');
    });

    test('lower-cases single-char keys: "ctrl+k" -> "Ctrl+K"', () => {
        expect(normalizeCombo('ctrl+k')).toBe('Ctrl+K');
    });

    // Modifier alias mapping
    test('maps "Control+K" to canonical "Ctrl+K"', () => {
        expect(normalizeCombo('Control+K')).toBe('Ctrl+K');
    });

    test('maps "Option+X" to canonical "Alt+X"', () => {
        expect(normalizeCombo('Option+X')).toBe('Alt+X');
    });

    test('maps "Cmd+K" to canonical "Meta+K"', () => {
        expect(normalizeCombo('Cmd+K')).toBe('Meta+K');
    });

    test('maps "Command+K" to canonical "Meta+K"', () => {
        expect(normalizeCombo('Command+K')).toBe('Meta+K');
    });

    // Key alias
    test('maps "esc" key alias to "Escape"', () => {
        expect(normalizeCombo('esc')).toBe('Escape');
    });

    // Modifier reordering
    test('reorders modifiers to canonical order: "Shift+Ctrl+K" -> "Ctrl+Shift+K"', () => {
        expect(normalizeCombo('Shift+Ctrl+K')).toBe('Ctrl+Shift+K');
    });

    // Duplicate modifier deduplication
    test('dedupes duplicate modifiers: "Ctrl+Control+K" -> "Ctrl+K"', () => {
        expect(normalizeCombo('Ctrl+Control+K')).toBe('Ctrl+K');
    });

    // Already-canonical strings pass through unchanged
    test('leaves already-canonical "Shift+D" unchanged', () => {
        expect(normalizeCombo('Shift+D')).toBe('Shift+D');
    });

    test('leaves already-canonical "Escape" unchanged', () => {
        expect(normalizeCombo('Escape')).toBe('Escape');
    });

    test('leaves already-canonical "ArrowLeft" unchanged', () => {
        expect(normalizeCombo('ArrowLeft')).toBe('ArrowLeft');
    });

    // Throws on invalid input
    test('throws on empty string', () => {
        expect(() => normalizeCombo('')).toThrow('Invalid combo');
    });

    test('throws on whitespace-only string', () => {
        expect(() => normalizeCombo('   ')).toThrow('Invalid combo');
    });

    test('throws on dangling modifier "Ctrl+"', () => {
        expect(() => normalizeCombo('Ctrl+')).toThrow();
    });

    test('throws on null', () => {
        expect(() => normalizeCombo(null)).toThrow('Invalid combo');
    });

    test('throws on undefined', () => {
        expect(() => normalizeCombo(undefined)).toThrow('Invalid combo');
    });

    test('throws on a number', () => {
        expect(() => normalizeCombo(42)).toThrow('Invalid combo');
    });
});

// ---------------------------------------------------------------------------
// comboFromEvent
// ---------------------------------------------------------------------------

describe('comboFromEvent', () => {
    test('shift+d event produces "Shift+D"', () => {
        const e = { key: 'd', shiftKey: true };
        expect(comboFromEvent(e)).toBe('Shift+D');
    });

    test('uppercase event key gives the same result as lowercase', () => {
        const lower = comboFromEvent({ key: 'd', shiftKey: true });
        const upper = comboFromEvent({ key: 'D', shiftKey: true });
        expect(upper).toBe(lower);
    });

    test('ctrl+alt+k produces "Ctrl+Alt+K" (canonical order)', () => {
        const e = { key: 'k', ctrlKey: true, altKey: true };
        expect(comboFromEvent(e)).toBe('Ctrl+Alt+K');
    });

    test('Escape key alone produces "Escape"', () => {
        expect(comboFromEvent({ key: 'Escape' })).toBe('Escape');
    });

    test('ArrowLeft key alone produces "ArrowLeft"', () => {
        expect(comboFromEvent({ key: 'ArrowLeft' })).toBe('ArrowLeft');
    });

    test('no-modifier single key "x" produces "X"', () => {
        expect(comboFromEvent({ key: 'x' })).toBe('X');
    });

    // Invariant: normalizeCombo(comboFromEvent(e)) === comboFromEvent(e)
    test('invariant: normalizeCombo(comboFromEvent(e)) === comboFromEvent(e) for representative events', () => {
        const events = [
            { key: 'd' },
            { key: 'd', shiftKey: true },
            { key: 'k', ctrlKey: true },
            { key: 'k', altKey: true },
            { key: 'k', metaKey: true },
            { key: 'k', ctrlKey: true, altKey: true },
            { key: 'k', ctrlKey: true, shiftKey: true },
            { key: 'k', ctrlKey: true, altKey: true, shiftKey: true },
            { key: 'k', ctrlKey: true, metaKey: true },
            { key: 'Escape' },
            { key: 'ArrowLeft' },
            { key: 'Enter' },
        ];
        for (const e of events) {
            const raw = comboFromEvent(e);
            expect(normalizeCombo(raw)).toBe(raw);
        }
    });
});

// ---------------------------------------------------------------------------
// isEditableTarget
// ---------------------------------------------------------------------------

describe('isEditableTarget', () => {
    test('returns true for INPUT tag (case-insensitive)', () => {
        expect(isEditableTarget({ tagName: 'input' })).toBe(true);
    });

    test('returns true for TEXTAREA', () => {
        expect(isEditableTarget({ tagName: 'TEXTAREA' })).toBe(true);
    });

    test('returns true for SELECT', () => {
        expect(isEditableTarget({ tagName: 'SELECT' })).toBe(true);
    });

    test('returns true for contentEditable div', () => {
        expect(isEditableTarget({ tagName: 'DIV', isContentEditable: true })).toBe(true);
    });

    test('returns false for non-editable DIV', () => {
        expect(isEditableTarget({ tagName: 'DIV' })).toBe(false);
    });

    test('returns false for null', () => {
        expect(isEditableTarget(null)).toBe(false);
    });

    test('returns false for undefined', () => {
        expect(isEditableTarget(undefined)).toBe(false);
    });
});

// ---------------------------------------------------------------------------
// Registry dispatch via handleKeydown
// ---------------------------------------------------------------------------

describe('handleKeydown dispatch', () => {
    beforeEach(() => {
        reset();
    });

    function mockEvent(combo, extra = {}) {
        const e = Object.assign(
            {
                key: 'd',
                ctrlKey: false,
                altKey: false,
                shiftKey: false,
                metaKey: false,
                target: {},
                preventDefault: jest.fn(),
            },
            extra
        );
        // Derive flags from combo string for convenience
        if (combo.includes('Ctrl')) e.ctrlKey = true;
        if (combo.includes('Alt')) e.altKey = true;
        if (combo.includes('Shift')) e.shiftKey = true;
        if (combo.includes('Meta')) e.metaKey = true;
        // Extract key letter after last '+'
        const parts = combo.split('+');
        e.key = parts[parts.length - 1].replace(/^[A-Z]/, (c) => c.toLowerCase());
        return e;
    }

    test('registered handler runs when combo matches and preventDefault is called', () => {
        const handler = jest.fn();
        register('Shift+D', handler);
        handleKeydown(mockEvent('Shift+D'));
        expect(handler).toHaveBeenCalledTimes(1);
        expect(handler.mock.calls[0][0].preventDefault).toBeDefined();
        expect(handler.mock.calls[0][0].preventDefault).toHaveBeenCalled();
    });

    test('when predicate returning false prevents handler from running and preventDefault', () => {
        const handler = jest.fn();
        register('Shift+D', handler, { when: () => false });
        handleKeydown(mockEvent('Shift+D'));
        expect(handler).not.toHaveBeenCalled();
    });

    test('when predicate returning true allows handler to run', () => {
        const handler = jest.fn();
        register('Shift+D', handler, { when: () => true });
        handleKeydown(mockEvent('Shift+D'));
        expect(handler).toHaveBeenCalledTimes(1);
    });

    test('editable target (INPUT) prevents handler from being called', () => {
        const handler = jest.fn();
        register('Shift+D', handler);
        handleKeydown(mockEvent('Shift+D', { target: { tagName: 'INPUT' } }));
        expect(handler).not.toHaveBeenCalled();
    });

    test('unregistered combo calls no handler (preventDefault not called)', () => {
        const pd = jest.fn();
        handleKeydown(Object.assign(mockEvent('Shift+Z'), { preventDefault: pd }));
        expect(pd).not.toHaveBeenCalled();
    });

    test('two handlers on the same combo both run in registration order', () => {
        const h1 = jest.fn();
        const h2 = jest.fn();
        register('Shift+D', h1);
        register('Shift+D', h2);
        handleKeydown(mockEvent('Shift+D'));
        expect(h1).toHaveBeenCalledTimes(1);
        expect(h2).toHaveBeenCalledTimes(1);
    });

    test('preventDefault called once when two handlers on same combo fire', () => {
        const h1 = jest.fn();
        const h2 = jest.fn();
        register('Shift+D', h1);
        register('Shift+D', h2);
        handleKeydown(mockEvent('Shift+D'));
        expect(h1.mock.calls[0][0].preventDefault).toHaveBeenCalledTimes(1);
    });

    test('unregister closure stops future dispatch for that handler', () => {
        const handler = jest.fn();
        const unregisterFn = register('Shift+D', handler);
        handleKeydown(mockEvent('Shift+D'));
        expect(handler).toHaveBeenCalledTimes(1);
        handler.mockClear();
        unregisterFn();
        handleKeydown(mockEvent('Shift+D'));
        expect(handler).not.toHaveBeenCalled();
    });

    test('closure unregistration does not affect other handlers on same combo', () => {
        const h1 = jest.fn();
        const h2 = jest.fn();
        const u1 = register('Shift+D', h1);
        register('Shift+D', h2);
        u1();
        handleKeydown(mockEvent('Shift+D'));
        expect(h1).not.toHaveBeenCalled();
        expect(h2).toHaveBeenCalledTimes(1);
    });

    test('unregister(combo, handler) removes exactly that handler', () => {
        const h1 = jest.fn();
        const h2 = jest.fn();
        register('Shift+D', h1);
        register('Shift+D', h2);
        unregister('Shift+D', h1);
        handleKeydown(mockEvent('Shift+D'));
        expect(h1).not.toHaveBeenCalled();
        expect(h2).toHaveBeenCalledTimes(1);
    });

    test('unregister(combo) without handler clears all handlers for the combo', () => {
        const h1 = jest.fn();
        const h2 = jest.fn();
        register('Shift+D', h1);
        register('Shift+D', h2);
        unregister('Shift+D');
        handleKeydown(mockEvent('Shift+D'));
        expect(h1).not.toHaveBeenCalled();
        expect(h2).not.toHaveBeenCalled();
    });

    test('after all handlers removed, re-registering the combo works again', () => {
        const h1 = jest.fn();
        const h2 = jest.fn();
        register('Shift+D', h1);
        unregister('Shift+D');
        register('Shift+D', h2);
        handleKeydown(mockEvent('Shift+D'));
        expect(h1).not.toHaveBeenCalled();
        expect(h2).toHaveBeenCalledTimes(1);
    });
});
