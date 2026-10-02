/**
 * Gallery Notes module
 * Reader / editor view for text notes (txt/md/json/csv/yaml) inside the
 * lightbox, plus the cover-image picker modal.
 *
 * Markdown notes are rendered (marked + DOMPurify + highlight.js + KaTeX);
 * other formats are shown as highlighted code. Users with edit rights can
 * switch to an editor (EasyMDE for markdown, plain textarea otherwise) —
 * changes are saved when leaving the editor.
 */

(function() {
    let currentNote = null;   // { id, title, original_name, content_type, canEdit }
    let currentText = '';
    let editMode = false;
    let editorDirty = false;
    let easyMde = null;
    let saving = false;

    // ========================================================================
    // Pure helpers (exported for Jest)
    // ========================================================================

    // Map a filename / content type to a highlight.js language id.
    function noteLanguageFromName(name) {
        const langMap = {
            md: 'markdown', markdown: 'markdown',
            json: 'json',
            csv: 'csv',
            yaml: 'yaml', yml: 'yaml',
            txt: 'plaintext',
        };
        const n = (name || '');
        const ext = n.includes('.') ? n.split('.').pop().toLowerCase() : 'txt';
        return langMap[ext] || 'plaintext';
    }

    function noteExtension(photo) {
        const name = photo.original_name || photo.title || '';
        if (name.includes('.')) {
            return name.split('.').pop().toLowerCase().slice(0, 8);
        }
        const ct = photo.content_type || 'text/plain';
        return ct.split('/').pop();
    }

    function isMarkdownNote(photo) {
        const lang = noteLanguageFromName(photo.original_name || photo.title || '');
        return photo.content_type === 'text/markdown' || lang === 'markdown';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = { noteLanguageFromName, isMarkdownNote };
    }

    // ========================================================================
    // Markdown rendering
    // ========================================================================

    function renderMarkdownHtml(text) {
        let html;
        try {
            html = window.marked.parse(text);
        } catch (e) {
            console.warn('[notes] marked failed, falling back to escaped text:', e);
            return `<p>${escapeHtml(text)}</p>`;
        }
        if (window.DOMPurify) {
            html = window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
        }
        return html;
    }

    function enhanceMarkdown(container) {
        // Syntax highlighting for fenced code blocks
        if (window.hljs) {
            container.querySelectorAll('pre code').forEach((codeEl) => {
                try {
                    window.hljs.highlightElement(codeEl);
                } catch (e) {
                    // Unknown language in this highlight.js build - keep plain.
                }
            });
        }

        // KaTeX math (vendored on the gallery page but usually unused)
        if (window.renderMathInElement) {
            try {
                window.renderMathInElement(container, {
                    delimiters: [
                        { left: '$$', right: '$$', display: true },
                        { left: '$', right: '$', display: false },
                    ],
                    throwOnError: false,
                });
            } catch (e) {
                // Math rendering is best-effort only.
            }
        }

        addCopyButtons(container);
    }

    // Copy buttons on code blocks (same pattern as the details panel).
    function addCopyButtons(container) {
        container.querySelectorAll('pre code').forEach((codeBlock) => {
            const pre = codeBlock.parentElement;
            if (pre.querySelector('.code-copy-btn')) return;

            const btn = document.createElement('button');
            btn.className = 'code-copy-btn';
            btn.title = 'Copy code';
            btn.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
            btn.addEventListener('click', async () => {
                const code = codeBlock.innerText;
                const ok = window.copyToClipboard
                    ? await window.copyToClipboard(code)
                    : await navigator.clipboard?.writeText(code);
                btn.classList.toggle('copied', !!ok);
                setTimeout(() => btn.classList.remove('copied'), 1500);
            });

            pre.style.position = 'relative';
            pre.appendChild(btn);
        });
    }

    // ========================================================================
    // Reader
    // ========================================================================

    const ICONS = {
        image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>',
        copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
        edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
        close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18"/><path d="M6 6l12 12"/></svg>',
    };

    window.NoteViewer = {

        isEditing() {
            return editMode;
        },

        renderReader(container, text, photo, canEdit) {
            currentNote = {
                id: photo.id,
                title: photo.title || '',
                original_name: photo.original_name || '',
                content_type: photo.content_type || 'text/plain',
                canEdit: canEdit !== false,
            };
            currentText = text;
            editMode = false;
            editorDirty = false;

            const ext = noteExtension(photo);
            const title = escapeHtml(photo.title || photo.original_name || 'Note');

            container.innerHTML = `
                <div class="note-reader" id="note-reader">
                    <div class="note-reader-toolbar">
                        <div class="note-reader-title">
                            <span class="note-reader-name" title="${title}">${title}</span>
                            <span class="note-format-badge">${escapeHtml(ext)}</span>
                        </div>
                        <div class="note-reader-actions">
                            <button class="note-tool-btn" data-action="cover" title="Set cover image" aria-label="Set cover image">${ICONS.image}</button>
                            <button class="note-tool-btn" data-action="copy" title="Copy text" aria-label="Copy text">${ICONS.copy}</button>
                            <button class="note-tool-btn" data-action="edit" title="Edit" aria-label="Edit"${currentNote.canEdit ? '' : ' style="display:none"'}>${ICONS.edit}</button>
                            <button class="note-tool-btn note-tool-close" data-action="close" title="Close (Esc)" aria-label="Close">${ICONS.close}</button>
                        </div>
                    </div>
                    <div class="note-reader-body" id="note-reader-body">
                        <div class="note-reader-content" id="note-reader-content"></div>
                    </div>
                </div>
            `;

            this.renderReadingView();

            container.querySelector('.note-reader-toolbar').addEventListener('click', (e) => {
                const btn = e.target.closest('.note-tool-btn');
                if (!btn) return;
                switch (btn.dataset.action) {
                    case 'close':
                        if (window.closeLightbox) window.closeLightbox();
                        break;
                    case 'edit':
                        this.enterEditor();
                        break;
                    case 'copy':
                        this.copyText();
                        break;
                    case 'cover':
                        this.openCoverPicker();
                        break;
                }
            });
        },

        renderReadingView() {
            const content = document.getElementById('note-reader-content');
            if (!content || !currentNote) return;

            if (isMarkdownNote(currentNote)) {
                // markdown-preview reuses the details-panel typography;
                // note-markdown-wrap overrides the panel chrome for reading.
                content.classList.add('markdown-preview', 'note-markdown-wrap');
                content.innerHTML = renderMarkdownHtml(currentText);
                enhanceMarkdown(content);
            } else {
                const lang = noteLanguageFromName(currentNote.original_name || currentNote.title);
                content.classList.remove('markdown-preview', 'note-markdown-wrap');
                content.innerHTML =
                    `<pre class="note-code"><code class="language-${lang}">${escapeHtml(currentText)}</code></pre>`;
                const codeEl = content.querySelector('code');
                if (codeEl && window.hljs) {
                    try {
                        window.hljs.highlightElement(codeEl);
                    } catch (e) {
                        // Keep plain text for unknown languages.
                    }
                }
            }
        },

        copyText() {
            const text = currentText || '';
            (window.copyToClipboard
                ? window.copyToClipboard(text)
                : navigator.clipboard?.writeText(text)
            ).then((ok) => {
                if (window.showToast) {
                    window.showToast('Text copied', !ok);
                }
            }).catch(() => {});
        },

        // ====================================================================
        // Editor
        // ====================================================================

        enterEditor() {
            if (!currentNote || !currentNote.canEdit || editMode) return;
            editMode = true;
            editorDirty = false;

            const body = document.getElementById('note-reader-body');
            if (!body) return;

            const editor = document.createElement('div');
            editor.className = 'note-editor';
            editor.innerHTML = `
                <div class="note-editor-header">
                    <span class="note-editor-hint">Changes are saved when you leave the editor</span>
                    <div class="note-editor-buttons">
                        <button type="button" class="btn btn-secondary" data-role="cancel">Cancel</button>
                        <button type="button" class="btn" data-role="done">Done</button>
                    </div>
                </div>
                <div class="note-editor-body"></div>
            `;
            body.innerHTML = '';
            body.appendChild(editor);

            const editorBody = editor.querySelector('.note-editor-body');
            if (isMarkdownNote(currentNote)) {
                const textarea = document.createElement('textarea');
                textarea.id = 'note-md-editor';
                editorBody.appendChild(textarea);
                easyMde = new EasyMDE({
                    element: textarea,
                    autofocus: true,
                    spellChecker: false,
                    autoDownloadFontAwesome: false,
                    toolbar: [
                        'bold', 'italic', 'heading', '|',
                        'code', 'quote', 'unordered-list', 'ordered-list', '|',
                        'link', 'image', '|',
                        'preview', 'side-by-side', 'fullscreen',
                    ],
                    status: false,
                    renderingConfig: {
                        sanitizerFunction: (html) => (
                            window.DOMPurify
                                ? window.DOMPurify.sanitize(html)
                                : html
                        ),
                    },
                    initialValue: currentText,
                });
                easyMde.codemirror.on('change', () => { editorDirty = true; });
            } else {
                const textarea = document.createElement('textarea');
                textarea.className = 'note-code-editor';
                textarea.value = currentText;
                textarea.spellcheck = false;
                // Tab inserts spaces instead of leaving the editor
                textarea.addEventListener('keydown', (e) => {
                    if (e.key === 'Tab') {
                        e.preventDefault();
                        const start = textarea.selectionStart;
                        const end = textarea.selectionEnd;
                        textarea.value = textarea.value.slice(0, start) + '    '
                            + textarea.value.slice(end);
                        textarea.selectionStart = textarea.selectionEnd = start + 4;
                        editorDirty = true;
                    }
                });
                textarea.addEventListener('input', () => { editorDirty = true; });
                editorBody.appendChild(textarea);
            }

            editor.querySelector('[data-role="done"]').addEventListener('click', () => {
                this.exitEditor(true);
            });
            editor.querySelector('[data-role="cancel"]').addEventListener('click', () => {
                if (editorDirty && !window.confirm('Discard unsaved changes?')) return;
                editMode = false;
                editorDirty = false;
                this.restoreReader();
            });

            // Hide the reader Edit button while editing
            const editBtn = document.querySelector('#note-reader [data-action="edit"]');
            if (editBtn) editBtn.style.display = 'none';
        },

        getEditorValue() {
            if (easyMde) return easyMde.value();
            const ta = document.querySelector('.note-code-editor');
            return ta ? ta.value : currentText;
        },

        destroyEditor() {
            if (easyMde) {
                try {
                    easyMde.toTextArea();
                } catch (e) {
                    // Instance already detached
                }
                easyMde = null;
            }
        },

        restoreReader() {
            this.destroyEditor();
            const body = document.getElementById('note-reader-body');
            if (body) {
                body.innerHTML = '<div class="note-reader-content" id="note-reader-content"></div>';
            }
            this.renderReadingView();
            const editBtn = document.querySelector('#note-reader [data-action="edit"]');
            if (editBtn && currentNote && currentNote.canEdit) {
                editBtn.style.display = '';
            }
            body?.scrollTo(0, 0);
        },

        async exitEditor(save = true) {
            if (!editMode) return;
            const value = this.getEditorValue();
            const changed = save && value !== currentText;

            editMode = false;
            editorDirty = false;

            if (changed) {
                currentText = value;
                await this.saveContent(value);
            }
            this.restoreReader();
        },

        async saveContent(text) {
            if (!currentNote || saving) return false;
            saving = true;
            try {
                const resp = await csrfFetch(
                    `${getBaseUrl()}/api/items/${currentNote.id}/content`,
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: text }),
                    }
                );
                if (!resp.ok) {
                    const detail = await resp.json().catch(() => ({}));
                    if (window.showToast) {
                        window.showToast(
                            `Save failed: ${detail.detail || resp.status}`,
                            true
                        );
                    }
                    return false;
                }
                if (window.showToast) window.showToast('Note saved', false);
                return true;
            } catch (err) {
                console.error('[notes] Failed to save note:', err);
                if (window.showToast) window.showToast('Save failed', true);
                return false;
            } finally {
                saving = false;
            }
        },

        // Called by the lightbox before closing: leaving the editor saves.
        saveIfEditing() {
            if (!editMode) return;
            const value = this.getEditorValue();
            const changed = value !== currentText;
            editMode = false;
            editorDirty = false;
            this.destroyEditor();
            if (changed) {
                currentText = value;
                // Fire-and-forget: the lightbox closes without waiting.
                this.saveContent(value);
            }
        },

        // ====================================================================
        // Cover picker
        // ====================================================================

        async openCoverPicker() {
            if (!currentNote) return;
            const modal = document.getElementById('note-cover-modal');
            const grid = document.getElementById('note-cover-grid');
            if (!modal || !grid) return;

            grid.innerHTML = '<p class="no-photos">Loading images…</p>';
            modal.classList.remove('hidden');

            try {
                // Live item record: current cover + the note's folder
                const itemResp = await fetch(`${getBaseUrl()}/api/items/${currentNote.id}`);
                if (!itemResp.ok) throw new Error(`HTTP ${itemResp.status}`);
                const item = await itemResp.json();
                const currentCover = item.cover_item_id || null;
                const folderId = item.folder_id;

                // Cover candidates: images from the note's folder
                // (same source as the album add-photos modal). Covers stay
                // hidden from the regular folder listing, so opt back in —
                // otherwise the current cover can't be highlighted or restored.
                const resp = await fetch(
                    `${getBaseUrl()}/api/folders/${folderId}/content?include_covers=true`
                );
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                const data = await resp.json();

                const images = (data.items || []).filter((it) =>
                    it.type === 'item' && it.item_type === 'media' && it.media_type === 'image'
                );
                if (images.length === 0) {
                    grid.innerHTML = '<p class="no-photos">No images in this folder</p>';
                } else {
                    grid.innerHTML = images.map((img) => `
                        <div class="photo-grid-item note-cover-item${img.id === currentCover ? ' is-cover' : ''}"
                             data-item-id="${img.id}"
                             title="${img.id === currentCover ? 'Current cover' : 'Click to set as cover'}">
                            <img src="${getBaseUrl()}/files/${img.id}/thumbnail"
                                 alt="${escapeHtml(img.title || img.original_name || '')}"
                                 loading="lazy">
                        </div>
                    `).join('');
                }

                grid.querySelectorAll('.note-cover-item').forEach((el) => {
                    el.addEventListener('click', () => {
                        this.setCover(el.dataset.itemId);
                    });
                });

                const removeBtn = document.getElementById('note-cover-remove');
                if (removeBtn) removeBtn.style.display = currentCover ? '' : 'none';
            } catch (err) {
                console.error('[notes] Failed to load cover candidates:', err);
                grid.innerHTML = '<p class="no-photos">Failed to load images</p>';
            }
        },

        closeCoverPicker() {
            const modal = document.getElementById('note-cover-modal');
            if (modal) modal.classList.add('hidden');
        },

        // Update the note's grid card in place after a cover change — the
        // aspect ratio follows the cover's thumbnail dimensions (like media
        // cards), so masonry gets a rebuild after the data attributes change.
        updateNoteCard(noteId, hasCover, item) {
            const card = document.querySelector(`.gallery-item[data-item-id="${noteId}"]`);
            const link = card?.querySelector('.gallery-link');
            if (!card || !link) return;

            const ext = card.dataset.noteExt
                || noteExtension(currentNote || {});
            card.dataset.noteExt = ext;

            const rawWidth = (hasCover && item && item.thumb_width) ? item.thumb_width : 280;
            const rawHeight = (hasCover && item && item.thumb_height) ? item.thumb_height : 210;
            const clamped = window.clampGalleryAspect
                ? window.clampGalleryAspect(rawWidth, rawHeight)
                : { width: rawWidth, height: rawHeight };
            const finalWidth = Math.round(clamped.width);
            const finalHeight = Math.round(clamped.height);
            card.dataset.thumbWidth = String(finalWidth);
            card.dataset.thumbHeight = String(finalHeight);
            link.style.aspectRatio = `${finalWidth} / ${finalHeight}`;

            const placeholderSvg = `
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="40" height="40">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                            <polyline points="14 2 14 8 20 8"></polyline>
                            <line x1="16" y1="13" x2="8" y2="13"></line>
                            <line x1="16" y1="17" x2="8" y2="17"></line>
                        </svg>
                        <span class="note-ext">${escapeHtml(ext)}</span>`;

            if (hasCover) {
                link.innerHTML = `
                    <div class="note-placeholder note-cover-fallback hidden">${placeholderSvg}</div>
                    <img class="note-cover-img" src="${getBaseUrl()}/files/${noteId}/thumbnail"
                         alt="${escapeHtml(currentNote ? currentNote.title : 'Note')}"
                         onerror="this.style.display='none'; this.previousElementSibling.classList.remove('hidden'); this.nextElementSibling.style.display='none';">
                    <span class="note-cover-badge">${escapeHtml(ext)}</span>
                `;
            } else {
                link.innerHTML = `
                    <div class="note-placeholder">${placeholderSvg}</div>
                `;
            }

            if (window.rebuildMasonry) window.rebuildMasonry(true);
        },

        async setCover(coverItemId) {
            if (!currentNote) return;
            try {
                const resp = await csrfFetch(
                    `${getBaseUrl()}/api/items/${currentNote.id}/cover`,
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ cover_item_id: coverItemId }),
                    }
                );
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                if (window.showToast) window.showToast('Cover updated', false);
                // Re-fetch the item for the fresh cover dimensions
                const itemResp = await fetch(`${getBaseUrl()}/api/items/${currentNote.id}`);
                const item = itemResp.ok ? await itemResp.json() : null;
                this.updateNoteCard(currentNote.id, true, item);
                this.closeCoverPicker();
            } catch (err) {
                console.error('[notes] Failed to set cover:', err);
                if (window.showToast) window.showToast('Failed to set cover', true);
            }
        },

        async removeCover() {
            if (!currentNote) return;
            try {
                const resp = await csrfFetch(
                    `${getBaseUrl()}/api/items/${currentNote.id}/cover`,
                    {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ cover_item_id: null }),
                    }
                );
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                if (window.showToast) window.showToast('Cover removed', false);
                this.updateNoteCard(currentNote.id, false, null);
                this.closeCoverPicker();
            } catch (err) {
                console.error('[notes] Failed to remove cover:', err);
                if (window.showToast) window.showToast('Failed to remove cover', true);
            }
        },
    };
})();
