/**
 * Sidebar highlight + stale-content hiding for the in-gallery tags view.
 *
 * 1. The Tags sidebar link (#tags-view-toggle) must light up with .active
 *    while the tags view is open (same pattern as the AI Chat toggle) and
 *    folder items must lose their .active highlight while it is open.
 * 2. Regression: navigating tags -> folder used to restore the stale
 *    folder content immediately (hideTagsView) and only replaced it after
 *    the fetch resolved, flashing the previous folder for one frame.
 *    navigateToFolder must keep gallery + folder-header hidden until
 *    renderFolderContent runs.
 *
 * navigation.js is loaded through window.eval, which mimics a classic
 * <script> tag (top-level declarations land on the jsdom window).
 */
const fs = require('fs');
const path = require('path');

const JS_DIR = path.join(__dirname, '..', '..', 'app', 'static', 'js');

function loadScript(name) {
    window.eval(fs.readFileSync(path.join(JS_DIR, name), 'utf8'));
}

function setupDom() {
    document.body.innerHTML = [
        '<aside id="sidebar">',
        '  <button id="ai-chat-toggle" class="sidebar-ai-btn"></button>',
        '  <a id="tags-view-toggle" class="sidebar-ai-btn" href="/?view=tags"></a>',
        '  <div id="folder-tree">',
        '    <div class="folder-item active" data-folder-id="f1"></div>',
        '    <div class="folder-item" data-folder-id="f2"></div>',
        '  </div>',
        '</aside>',
        '<div class="folder-header"><h2>Folder 1</h2></div>',
        '<div id="subfolders-section" style="display: none;"></div>',
        '<div id="gallery"><div class="gallery-item">stale folder 1 item</div></div>',
        '<div id="tags-view" style="display: none;"></div>',
    ].join('');
}

beforeAll(() => {
    setupDom();
    // jsdom has no layout: offsetParent is always null, which would make
    // isTagsViewVisible() false forever. Mimic "element is rendered".
    Object.defineProperty(window.HTMLElement.prototype, 'offsetParent', {
        configurable: true,
        get() { return document.body; },
    });
    loadScript('core.js');
    // navigation.js wraps the pre-existing window.renderFolderContent;
    // on the real page another module defines it before navigation.js.
    window.renderFolderContent = window.renderFolderContent || jest.fn();
    window.SYNTH_USER_ID = 1;
    loadScript('navigation.js');
});

afterEach(() => {
    window.hideTagsView();
    document.querySelectorAll('.folder-item').forEach(item => item.classList.remove('active'));
    document.querySelector('.folder-item[data-folder-id="f1"]').classList.add('active');
});

describe('tags view sidebar highlight', () => {
    test('showTagsView lights the Tags link and unlights folder items', () => {
        expect(document.getElementById('tags-view-toggle').classList.contains('active')).toBe(false);
        expect(document.querySelector('.folder-item[data-folder-id="f1"]').classList.contains('active')).toBe(true);

        window.showTagsView();

        expect(document.getElementById('tags-view-toggle').classList.contains('active')).toBe(true);
        expect(document.querySelector('.folder-item[data-folder-id="f1"]').classList.contains('active')).toBe(false);
    });

    test('hideTagsView unlights the Tags link', () => {
        window.showTagsView();
        expect(document.getElementById('tags-view-toggle').classList.contains('active')).toBe(true);

        window.hideTagsView();

        expect(document.getElementById('tags-view-toggle').classList.contains('active')).toBe(false);
    });
});

describe('navigating from the tags view to a folder', () => {
    test('stale folder stays hidden until the new folder renders (no flash of folder 1)', async () => {
        window.showTagsView();
        const gallery = document.getElementById('gallery');
        const folderHeader = document.querySelector('.folder-header');
        expect(gallery.style.display).toBe('none');

        window.fetch = jest.fn(() => Promise.resolve({
            ok: true,
            json: () => Promise.resolve({
                sort: 'uploaded',
                folder: { id: 'f2', name: 'Folder 2', permission: 'owner', user_id: 1 },
                subfolders: [],
                items: [{
                    type: 'item', item_type: 'media', id: 'm1',
                    media_type: 'image', original_name: 'x.jpg',
                }],
            }),
        }));
        const updateActive = jest.fn();
        window.updateSidebarActiveState = updateActive;

        const navigation = window.navigateToFolder('f2');

        // Before the fetch resolves the stale folder 1 must stay hidden.
        expect(gallery.style.display).toBe('none');
        expect(folderHeader.style.display).toBe('none');

        await navigation;

        // New folder rendered: content visible again, header restored.
        expect(gallery.style.display).toBe('');
        expect(folderHeader.style.display).toBe('');
        expect(gallery.querySelector('[data-item-id="m1"]')).not.toBeNull();
        expect(folderHeader.querySelector('h2').textContent).toBe('Folder 2');
        // Sidebar: folder navigation took over from the tags highlight.
        expect(updateActive).toHaveBeenCalledWith('f2');
        expect(document.getElementById('tags-view-toggle').classList.contains('active')).toBe(false);
    });
});
