/**
 * Regression test: tags-page.js must stay encapsulated in an IIFE.
 *
 * It used to be a bare classic script, so its top-level `csrfFetch`,
 * `getCsrfToken` and `escapeHtml` overwrote the core.js globals. The
 * shadowing csrfFetch prepended the page BASE_URL to URLs that gallery
 * modules already build as `${getBaseUrl()}/api/...`, so every mutating
 * call (delete / move / copy of items) went to `/synth/synth/api/...`
 * and 404'd — deletion silently did nothing (2026-10-25).
 *
 * Both sources are loaded through window.eval, which mimics classic
 * <script> tags: top-level function declarations land on the jsdom
 * window, in load order.
 */
const fs = require('fs');
const path = require('path');

const JS_DIR = path.join(__dirname, '..', '..', 'app', 'static', 'js');
const TEMPLATE = path.join(__dirname, '..', '..', 'app', 'templates', 'gallery.html');

function loadScript(name) {
    window.eval(fs.readFileSync(path.join(JS_DIR, name), 'utf8'));
}

// Collect every function name referenced by inline onclick/onchange="fn(...)"
// attributes in a chunk of markup. Bare statements (event.stopPropagation())
// and window.* refs are ignored — only plain identifiers matter here.
function extractHandlerNames(source) {
    const names = new Set();
    const re = /on(?:click|change|input|submit)\s*=\s*"([a-zA-Z_$][\w$]*)\s*\(/g;
    let match;
    while ((match = re.exec(source)) !== null) {
        names.add(match[1]);
    }
    return [...names];
}

function setupDom() {
    document.head.innerHTML = [
        '<link rel="stylesheet" href="/synth/static/style.css?v=test">',
        '<meta name="csrf-token" content="test-token">',
    ].join('');
    document.body.innerHTML = [
        '<div id="tags-view">',
        '  <input id="tag-search">',
        '  <div id="detail-panel"></div>',
        '</div>',
    ].join('');
}

let coreCsrfFetch;
let coreGetCsrfToken;

beforeAll(() => {
    setupDom();
    loadScript('core.js');
    coreCsrfFetch = window.csrfFetch;
    coreGetCsrfToken = window.getCsrfToken;
    // #tags-view present → tags-page skips auto-init (no fetches on load).
    loadScript('tags-page.js');
});

describe('tags-page.js must not clobber core.js globals', () => {
    test('window.csrfFetch stays the core.js implementation', () => {
        expect(window.csrfFetch).toBe(coreCsrfFetch);
    });

    test('window.getCsrfToken stays the core.js implementation', () => {
        expect(window.getCsrfToken).toBe(coreGetCsrfToken);
    });

    test('global csrfFetch passes already-absolute URLs through unmodified', async () => {
        // gallery-selection.js builds URLs as `${getBaseUrl()}/api/items/${id}`;
        // the shadowed csrfFetch double-prefixed them to /synth/synth/... → 404.
        const calls = [];
        window.fetch = jest.fn((url, opts) => {
            calls.push({ url, opts });
            return Promise.resolve({ ok: true });
        });

        await window.csrfFetch('/synth/api/items/some-id', { method: 'DELETE' });

        expect(calls).toHaveLength(1);
        expect(calls[0].url).toBe('/synth/api/items/some-id');
        expect(calls[0].opts.method).toBe('DELETE');
    });
});

describe('tags-page.js public surface', () => {
    test('exports the SPA entry points for navigation.js / init.js', () => {
        expect(typeof window.initTagsPage).toBe('function');
        expect(typeof window.navigateToTagByName).toBe('function');
    });

    test('exports the functions used by generated inline onclick handlers', () => {
        for (const name of [
            'selectTag',
            'loadCategoryTags',
            'cancelEdit',
            'saveEdit',
            'toggleTagDescMode',
            'saveTagDescription',
            'addImplication',
            'removeImplication',
            'pickImplication',
            'openRemapModal',
            'selectRemapTarget',
            'deleteTag',
            'deleteCategory',
            // Static-template modal buttons (gallery.html) — these were
            // missed when the IIFE wrapper was introduced (2026-11-04):
            // every tags-modal button silently threw a ReferenceError.
            'createTag',
            'closeModal',
            'createCategory',
            'closeCategoriesModal',
            'updateCategory',
            'closeRemapModal',
            'confirmRemap',
        ]) {
            expect(typeof window[name]).toBe('function');
        }
    });
});

describe('every inline handler resolves on window', () => {
    test('handlers referenced by tags-page.js generated markup are exported', () => {
        const source = fs.readFileSync(path.join(JS_DIR, 'tags-page.js'), 'utf8');
        for (const name of extractHandlerNames(source)) {
            expect(typeof window[name]).toBe('function');
        }
    });

    test('handlers referenced by the gallery.html tags section are exported', () => {
        // The tags view + its three modals sit at the end of gallery.html;
        // slicing from id="tags-view" keeps other modules' handlers out.
        const template = fs.readFileSync(TEMPLATE, 'utf8');
        const tagsSection = template.slice(template.indexOf('id="tags-view"'));
        for (const name of extractHandlerNames(tagsSection)) {
            expect(typeof window[name]).toBe('function');
        }
    });
});
