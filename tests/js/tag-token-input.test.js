/**
 * Unit tests for the pure helpers of tag-token-input.js: tag-name
 * normalization/validation (mirror of the backend grammar), token splitting
 * and chip state readback from the widget DOM.
 */
const {
    normalizeTagName,
    isValidTagName,
    splitTagTokens,
    endsWithSeparator,
    readChips,
    } = require('../../app/static/js/tag-token-input.js');

function placeCaretIn(textNode, offset) {
    const range = document.createRange();
    range.setStart(textNode, offset);
    range.setEnd(textNode, offset);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
}

describe('normalizeTagName', () => {
    test('mirrors backend normalization: lower, strip', () => {
        expect(normalizeTagName('Fox')).toBe('fox');
        expect(normalizeTagName('  Fox ')).toBe('fox');
        expect(normalizeTagName('Fox.Night_1')).toBe('fox.night_1');
    });

    test('commas stay part of the name', () => {
        expect(normalizeTagName('a,b')).toBe('a,b');
    });

    test('tolerates null/undefined input', () => {
        expect(normalizeTagName(null)).toBe('');
        expect(normalizeTagName(undefined)).toBe('');
    });
});

describe('isValidTagName', () => {
    test('accepts the backend tag-name grammar', () => {
        expect(isValidTagName('fox')).toBe(true);
        expect(isValidTagName('black_and_white')).toBe(true);
        expect(isValidTagName('tag(1)[a]{b}+!~&%=$#@^')).toBe(true);
        expect(isValidTagName('a.b-c')).toBe(true);
    });

    test('rejects invalid names', () => {
        expect(isValidTagName('')).toBe(false);
        expect(isValidTagName('foo bar')).toBe(false); // space is not in the grammar
        expect(isValidTagName('привет')).toBe(false); // cyrillic
        expect(isValidTagName(null)).toBe(false);
    });
});

describe('splitTagTokens', () => {
    test('splits on whitespace and trims', () => {
        expect(splitTagTokens('fox wolf  night')).toEqual(['fox', 'wolf', 'night']);
    });

    test('drops empty parts', () => {
        expect(splitTagTokens('   fox   ')).toEqual(['fox']);
        expect(splitTagTokens('   ')).toEqual([]);
        expect(splitTagTokens('')).toEqual([]);
        expect(splitTagTokens(null)).toEqual([]);
    });

    test('splits on newlines too (paste payloads)', () => {
        expect(splitTagTokens('fox\nwolf\n night')).toEqual(['fox', 'wolf', 'night']);
    });

    test('keeps commas inside names (comma is not a delimiter)', () => {
        expect(splitTagTokens('a,b c,d')).toEqual(['a,b', 'c,d']);
    });
});

describe('endsWithSeparator', () => {
    test('true for trailing whitespace (space or newline)', () => {
        expect(endsWithSeparator('fox ')).toBe(true);
        expect(endsWithSeparator('fox  ')).toBe(true);
        expect(endsWithSeparator('fox\n')).toBe(true);
    });

    test('false without a trailing separator', () => {
        expect(endsWithSeparator('fox')).toBe(false);
        expect(endsWithSeparator('fo x')).toBe(false);
        expect(endsWithSeparator('')).toBe(false);
    });
});

describe('readChips', () => {
    function makeContainer() {
        const container = document.createElement('div');
        const chip = (name, state, extra = '') => {
            const el = document.createElement('span');
            el.className = 'tag-input-chip';
            el.setAttribute('data-name', name);
            el.setAttribute('data-state', state);
            if (extra) el.setAttribute('data-id', extra);
            container.appendChild(el);
        };
        chip('fox', 'known', '7');
        container.appendChild(document.createTextNode('typed'));
        chip('newtag', 'unknown');
        chip('bad!', 'invalid');
        return container;
    }

    test('reads chips in DOM order with their state', () => {
        const chips = readChips(makeContainer());
        expect(chips.map(c => c.name)).toEqual(['fox', 'newtag', 'bad!']);
        expect(chips.map(c => c.state)).toEqual(['known', 'unknown', 'invalid']);
        expect(chips[0].id).toBe(7);
        expect(chips[1].id).toBeNull();
    });

    test('defaults to unknown state when the attribute is missing', () => {
        const container = document.createElement('div');
        const el = document.createElement('span');
        el.className = 'tag-input-chip';
        el.setAttribute('data-name', 'x');
        container.appendChild(el);
        expect(readChips(container)[0].state).toBe('unknown');
    });

    test('returns an empty list for a missing container', () => {
        expect(readChips(null)).toEqual([]);
    });
});

describe('widget: picking a suggestion', () => {
    function setupWidget(container, suggestions) {
        let onPickCb = null;
        window.createTagTokenInput(container, {
            fetchSuggestions: async () => suggestions,
            renderSuggestions: (results, _idx, onPick) => {
                if (results.length > 0) onPickCb = onPick;
            },
        });
        return () => onPickCb;
    }

    test('replaces the typed text with the chip (selection preserved)', async () => {
        jest.useFakeTimers();
        const container = document.createElement('div');
        document.body.appendChild(container);
        const getOnPick = setupWidget(container, [
            { id: 5, name: 'wolf', count: 3, category_color: '#ff0000' },
        ]);

        const textNode = document.createTextNode('wol');
        container.appendChild(textNode);
        placeCaretIn(textNode, 3);
        container.dispatchEvent(new Event('input', { bubbles: true }));
        await jest.advanceTimersByTimeAsync(250);

        getOnPick()(0);
        const chip = container.querySelector('.tag-input-chip');
        expect(chip).not.toBeNull();
        expect(chip.getAttribute('data-name')).toBe('wolf');
        expect(chip.getAttribute('data-state')).toBe('known');
        // no free text left behind
        expect([...container.childNodes].some(n => n.nodeType === Node.TEXT_NODE
            && n.textContent.trim() !== '')).toBe(false);

        jest.useRealTimers();
    });

    test('replaces the typed text with the chip (selection lost to the click)', async () => {
        jest.useFakeTimers();
        const container = document.createElement('div');
        document.body.appendChild(container);
        const getOnPick = setupWidget(container, [
            { id: 5, name: 'wolf', count: 3, category_color: '#ff0000' },
        ]);

        const textNode = document.createTextNode('wol');
        container.appendChild(textNode);
        placeCaretIn(textNode, 3);
        container.dispatchEvent(new Event('input', { bubbles: true }));
        await jest.advanceTimersByTimeAsync(250);

        // mousedown on the suggestion list clears the selection before the
        // click handler runs
        window.getSelection().removeAllRanges();
        getOnPick()(0);
        const chip = container.querySelector('.tag-input-chip');
        expect(chip).not.toBeNull();
        expect(chip.getAttribute('data-name')).toBe('wolf');
        expect([...container.childNodes].some(n => n.nodeType === Node.TEXT_NODE
            && n.textContent.trim() !== '')).toBe(false);

        jest.useRealTimers();
    });
});

describe('widget: space-committed chips resolve to known', () => {
    test('typing "female forest grass" commits clean names and resolves them', async () => {
        jest.useFakeTimers();
        const container = document.createElement('div');
        document.body.appendChild(container);
        const resolvedWith = [];
        window.createTagTokenInput(container, {
            fetchSuggestions: async () => [],
            renderSuggestions: () => {},
            resolveNames: async (names) => {
                resolvedWith.push([...names]);
                return names.map(name => ({
                    name,
                    valid: true,
                    exists: true,
                    tag: { id: 42, name, usage_count: 7, category_color: '#00ff00' },
                }));
            },
        });

        const fullText = 'female forest grass';
        // type "female forest grass" one keystroke at a time; each space
        // segment accumulates in its own text node like the browser does
        const segments = fullText.split(' ');
        segments.forEach((seg, idx) => {
            const typed = idx < segments.length - 1 ? seg + ' ' : seg;
            const textNode = document.createTextNode('');
            container.appendChild(textNode);
            for (let i = 1; i <= typed.length; i++) {
                textNode.textContent = typed.slice(0, i);
                placeCaretIn(textNode, i);
                container.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        await jest.advanceTimersByTimeAsync(200);

        const chips = [...container.querySelectorAll('.tag-input-chip')];
        // the last segment has no trailing space, so it stays as typed text
        // until Enter commits it
        expect(chips.map(c => c.getAttribute('data-name'))).toEqual(['female', 'forest']);
        chips.forEach(c => {
            expect(c.getAttribute('data-name')).not.toContain(' ');
            expect(c.getAttribute('data-state')).toBe('known');
        });
        expect(container.textContent).toContain('grass');
        // the resolve request received the split names, not one string
        expect(resolvedWith[resolvedWith.length - 1]).toEqual(['female', 'forest']);

        jest.useRealTimers();
    });
});
