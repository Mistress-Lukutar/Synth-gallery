/**
 * Tag Token Input
 *
 * Multi-tag entry widget used by the item-details panel: a contenteditable
 * field mixing tag chips with free text. Tokens are committed by space,
 * suggestion click or Enter; Enter on an empty field confirms the whole
 * input (options.onConfirm). Tags unknown to the DB render with a red
 * outline and a "?" count until the host resolves or creates them.
 *
 * Whitespace is ALWAYS a delimiter in this widget: the stored tag-name
 * grammar forbids spaces, so typed whitespace always separates two tags.
 * Commas are NOT delimiters — the backend grammar allows commas in names.
 */
(function() {
    // --- Pure helpers (exported for unit tests) ---------------------------

    // Mirror of the backend tag-name grammar (tag_service.TAG_NAME_PATTERN).
    const TAG_NAME_RE = /^[a-z0-9_\-\.\(\)\[\]\{\}\+\!\~\&\%\=\$\#\@\^\,]+$/;

    // Mirror of backend normalization: lower, strip. Spaces cannot occur:
    // the token splitter consumes them before this runs.
    function normalizeTagName(raw) {
        return String(raw == null ? '' : raw).toLowerCase().trim();
    }

    function isValidTagName(name) {
        return typeof name === 'string' && name.length > 0 && TAG_NAME_RE.test(name);
    }

    // Split raw text into tag tokens on any whitespace (spaces and
    // newlines). A space can never be part of a stored tag name, so it
    // always means "two tags".
    function splitTagTokens(text) {
        return String(text == null ? '' : text)
            .split(/\s+/)
            .map(t => t.trim())
            .filter(t => t.length > 0);
    }

    // True when raw ends with a separator (trailing whitespace) meaning
    // the last token is finished too.
    function endsWithSeparator(text) {
        return /\s$/.test(String(text == null ? '' : text));
    }

    // Read ordered chip state from a token-input container element.
    function readChips(container) {
        const chips = [];
        if (!container) return chips;
        container.querySelectorAll('.tag-input-chip').forEach(el => {
            chips.push({
                name: el.getAttribute('data-name') || '',
                state: el.getAttribute('data-state') || 'unknown',
                id: el.getAttribute('data-id') ? Number(el.getAttribute('data-id')) : null,
                count: el.getAttribute('data-count'),
                element: el,
            });
        });
        return chips;
    }

    // --- Widget factory -----------------------------------------------------

    /**
     * Create a token input on a contenteditable container.
     *
     * options:
     *   fetchSuggestions(q) -> Promise<[{id, name, count, category_color}]>
     *   renderSuggestions(results, selectedIndex, onPick) — draw the list
     *     into an external container; empty results clear it
     *   resolveNames(names) -> Promise<[{name, valid, exists, tag}]> —
     *     lookup usage counts for text-committed chips
     *   onConfirm() — Enter on an empty field (apply the staged chips)
     */
    window.createTagTokenInput = function(container, options) {
        const opts = options || {};
        let suggestionResults = [];
        let selectedIndex = -1;
        // Text node the current suggestions were queried from. Picking a
        // suggestion with a mouse click clears the DOM selection, so the
        // caret can no longer be found at pick time; remember it instead.
        let activeTextNode = null;
        let suggestTimer = null;
        let resolveTimer = null;
        let fetchSeq = 0;

        function isChipEl(n) {
            return !!(n && n.nodeType === Node.ELEMENT_NODE
                && n.classList && n.classList.contains('tag-input-chip'));
        }

        // -- chip DOM --------------------------------------------------------

        function applyChipInfo(chip, info) {
            chip.setAttribute('data-state', info.state);
            if (info.state === 'known') {
                if (info.id != null) {
                    chip.setAttribute('data-id', String(info.id));
                } else {
                    chip.removeAttribute('data-id');
                }
                chip.setAttribute('data-count', String(info.count != null ? info.count : 0));
                chip.style.setProperty('--tag-color', info.color || '#6b7280');
                chip.classList.remove('invalid');
            } else {
                chip.removeAttribute('data-id');
                chip.setAttribute('data-count', '?');
                chip.classList.add('invalid');
            }
            chip.textContent = '';
            chip.appendChild(document.createTextNode(chip.getAttribute('data-name') || ''));
            const count = document.createElement('span');
            count.className = 'chip-count';
            count.textContent = info.state === 'known'
                ? String(info.count != null ? info.count : 0)
                : '?';
            chip.appendChild(count);
        }

        function createChip(name, info) {
            const chip = document.createElement('span');
            chip.className = 'tag-chip tag-chip-editable tag-input-chip';
            chip.setAttribute('contenteditable', 'false');
            chip.setAttribute('data-name', name);
            applyChipInfo(chip, info || {
                state: isValidTagName(name) ? 'unknown' : 'invalid',
            });
            chip.addEventListener('click', onChipClick);
            return chip;
        }

        function isDuplicateChip(name) {
            return readChips(container).some(c => c.name === name);
        }

        // -- caret utilities ---------------------------------------------------

        function getSelectionRange() {
            const sel = window.getSelection();
            if (!sel || sel.rangeCount === 0) return null;
            const range = sel.getRangeAt(0);
            if (!container.contains(range.startContainer)) return null;
            return range;
        }

        function caretTextNode() {
            const range = getSelectionRange();
            if (!range || !range.collapsed) return null;
            return range.startContainer.nodeType === Node.TEXT_NODE ? range.startContainer : null;
        }

        function placeCaret(node, offset) {
            const sel = window.getSelection();
            const range = document.createRange();
            if (node.nodeType === Node.TEXT_NODE) {
                range.setStart(node, Math.min(offset, node.textContent.length));
            } else {
                range.setStart(node, Math.min(offset, node.childNodes.length));
            }
            range.collapse(true);
            sel.removeAllRanges();
            sel.addRange(range);
        }

        function placeCaretEnd() {
            const last = container.lastChild;
            if (last && last.nodeType === Node.TEXT_NODE) {
                placeCaret(last, last.textContent.length);
            } else {
                placeCaret(container, container.childNodes.length);
            }
        }

        // The chip element directly before the caret, if the caret sits at a
        // segment boundary (start of a text node or between children).
        function chipBeforeCaret(range) {
            const sc = range.startContainer;
            if (sc.nodeType === Node.TEXT_NODE) {
                if (range.startOffset > 0) return null;
                let n = sc.previousSibling;
                while (n && n.nodeType === Node.TEXT_NODE && n.textContent === '') {
                    n = n.previousSibling;
                }
                return isChipEl(n) ? n : null;
            }
            if (sc === container) {
                return isChipEl(container.childNodes[range.startOffset - 1])
                    ? container.childNodes[range.startOffset - 1]
                    : null;
            }
            return null;
        }

        // -- committing tokens -------------------------------------------------

        // Split the caret's text node on whitespace: everything before the
        // last (still being typed) token becomes chips.
        function commitTokensFromTextNode(textNode) {
            const raw = textNode.textContent;
            if (!/\s/.test(raw)) return false;
            const tokens = splitTagTokens(raw);
            const all = endsWithSeparator(raw);
            const toCommit = all ? tokens : tokens.slice(0, -1);
            const remainder = all ? '' : (tokens[tokens.length - 1] || '');
            const parent = textNode.parentNode || container;
            for (const token of toCommit) {
                const name = normalizeTagName(token);
                if (!name || isDuplicateChip(name)) continue;
                parent.insertBefore(createChip(name), textNode);
            }
            textNode.textContent = remainder;
            if (remainder === '') {
                const idx = Array.prototype.indexOf.call(parent.childNodes, textNode);
                textNode.remove();
                placeCaret(parent, idx);
            } else {
                placeCaret(textNode, remainder.length);
            }
            return true;
        }

        // Commit the caret's whole text node (Enter with no suggestions).
        function commitWholeTextNode(textNode) {
            const tokens = splitTagTokens(textNode.textContent);
            if (tokens.length === 0) return false;
            const parent = textNode.parentNode || container;
            for (const token of tokens) {
                const name = normalizeTagName(token);
                if (!name || isDuplicateChip(name)) continue;
                parent.insertBefore(createChip(name), textNode);
            }
            const idx = Array.prototype.indexOf.call(parent.childNodes, textNode);
            textNode.remove();
            placeCaret(parent, idx);
            return true;
        }

        function removeTextNodeKeepingCaret(textNode) {
            const parent = textNode.parentNode || container;
            const idx = Array.prototype.indexOf.call(parent.childNodes, textNode);
            textNode.remove();
            placeCaret(parent, idx);
        }

        // -- suggestions ---------------------------------------------------------

        function renderSuggestions() {
            if (typeof opts.renderSuggestions === 'function') {
                opts.renderSuggestions(suggestionResults, selectedIndex, pickSuggestion);
            }
            updateEscapeHold();
        }

        function hideSuggestions() {
            suggestionResults = [];
            selectedIndex = -1;
            renderSuggestions();
        }

        function scheduleSuggestions() {
            clearTimeout(suggestTimer);
            const node = caretTextNode();
            activeTextNode = node;
            const query = node ? node.textContent.trim() : '';
            if (!query) {
                hideSuggestions();
                return;
            }
            if (typeof opts.fetchSuggestions !== 'function') return;
            suggestTimer = setTimeout(async () => {
                const seq = ++fetchSeq;
                try {
                    const results = await opts.fetchSuggestions(query);
                    if (seq !== fetchSeq) return; // stale response
                    suggestionResults = Array.isArray(results) ? results : [];
                    selectedIndex = -1;
                    renderSuggestions();
                } catch (err) {
                    // network failure: leave the previous (hidden) state
                }
            }, 200);
        }

        function pickSuggestion(index) {
            const sugg = suggestionResults[index];
            if (!sugg) return;
            const name = normalizeTagName(sugg.name);
            const info = {
                state: 'known',
                id: sugg.id,
                count: sugg.count != null ? sugg.count : (sugg.usage_count || 0),
                color: sugg.category_color,
            };
            const node = (activeTextNode && container.contains(activeTextNode))
                ? activeTextNode
                : caretTextNode();
            if (node) {
                if (!isDuplicateChip(name)) {
                    node.parentNode.insertBefore(createChip(name, info), node);
                }
                removeTextNodeKeepingCaret(node);
            } else if (!isDuplicateChip(name)) {
                container.appendChild(createChip(name, info));
                placeCaretEnd();
            }
            hideSuggestions();
            updateEmptyState();
            container.focus();
        }

        // -- chip resolution (usage counts) ---------------------------------------

        function applyResolveResults(results) {
            const byName = {};
            (results || []).forEach(r => { byName[r.name] = r; });
            readChips(container).forEach(chip => {
                const r = byName[chip.name];
                if (!r) return;
                if (r.tag) {
                    applyChipInfo(chip.element, {
                        state: 'known',
                        id: r.tag.id,
                        count: r.tag.usage_count != null ? r.tag.usage_count : (r.tag.count || 0),
                        color: r.tag.category_color,
                    });
                } else if (r.valid === false) {
                    applyChipInfo(chip.element, { state: 'invalid' });
                }
                // exists=false but valid: stays "unknown" (red "?")
            });
            updateEmptyState();
        }

        function scheduleResolve() {
            if (typeof opts.resolveNames !== 'function') return;
            const names = readChips(container)
                .filter(c => c.state === 'unknown')
                .map(c => c.name);
            if (!names.length) return;
            clearTimeout(resolveTimer);
            resolveTimer = setTimeout(async () => {
                try {
                    applyResolveResults(await opts.resolveNames(names));
                } catch (err) {
                    // keep "?" state; the next commit retries
                    console.warn('Tag resolve failed:', err);
                }
            }, 150);
        }

        // -- armed (selected) chip state -------------------------------------------

        function selectedChip() {
            return container.querySelector('.tag-input-chip.selected');
        }

        function clearSelectedChip() {
            const el = selectedChip();
            if (el) {
                el.classList.remove('selected');
                updateEscapeHold();
            }
        }

        // -- chip editing -------------------------------------------------------------

        function onChipClick(e) {
            e.preventDefault();
            e.stopPropagation();
            const chip = e.currentTarget;
            clearSelectedChip();
            const textNode = document.createTextNode(chip.getAttribute('data-name') || '');
            chip.replaceWith(textNode);
            container.focus();
            // select the whole token so retyping replaces it
            const range = document.createRange();
            range.selectNodeContents(textNode);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
            updateEmptyState();
            scheduleSuggestions();
        }

        // -- keyboard ------------------------------------------------------------------

        function onKeyDown(e) {
            if (e.key !== 'Backspace') clearSelectedChip();

            if (e.key === 'Enter') {
                e.preventDefault();
                if (suggestionResults.length > 0) {
                    pickSuggestion(selectedIndex >= 0 ? selectedIndex : 0);
                    return;
                }
                const node = caretTextNode();
                if (node && commitWholeTextNode(node)) {
                    hideSuggestions();
                    updateEmptyState();
                    scheduleResolve();
                    return;
                }
                if (typeof opts.onConfirm === 'function') opts.onConfirm();
                return;
            }

            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                if (suggestionResults.length > 0) {
                    e.preventDefault();
                    const dir = e.key === 'ArrowDown' ? 1 : -1;
                    selectedIndex = Math.min(Math.max(selectedIndex + dir, 0), suggestionResults.length - 1);
                    renderSuggestions();
                }
                return; // otherwise let the caret move inside the field
            }

            if (e.key === 'Escape') {
                // Layered Escape: dismiss suggestions, then the typed text;
                // only a clean field lets the key bubble up so the app-wide
                // BackButtonManager can close the details panel
                if (suggestionResults.length > 0) {
                    hideSuggestions();
                    e.stopPropagation();
                    return;
                }
                if (selectedChip()) return; // already disarmed above
                const node = caretTextNode();
                if (node && node.textContent.trim()) {
                    removeTextNodeKeepingCaret(node);
                    updateEmptyState();
                    e.stopPropagation();
                    return;
                }
                return;
            }

            if (e.key === 'Backspace') {
                const range = getSelectionRange();
                if (!range || !range.collapsed) return; // selection delete: default
                const prev = chipBeforeCaret(range);
                if (prev) {
                    e.preventDefault();
                    if (prev.classList.contains('selected')) {
                        prev.remove();
                        updateEmptyState();
                    } else {
                        prev.classList.add('selected');
                        updateEscapeHold();
                    }
                }
                return;
            }
        }

        // -- input / paste -----------------------------------------------------------------

        function onInput() {
            clearSelectedChip();
            const node = caretTextNode();
            if (node) {
                commitTokensFromTextNode(node);
            }
            updateEmptyState();
            scheduleSuggestions();
            scheduleResolve();
        }

        function onPaste(e) {
            e.preventDefault();
            const text = (e.clipboardData || window.clipboardData).getData('text') || '';
            // plain-text insert keeps the payload in one text node so the
            // whitespace splitter in onInput sees all of it
            document.execCommand('insertText', false, text);
        }

        // -- state ---------------------------------------------------------------------------

        function updateEmptyState() {
            const empty = !container.querySelector('.tag-input-chip')
                && container.textContent.trim() === '';
            container.setAttribute('data-empty', empty ? 'true' : 'false');
            updateEscapeHold();
        }

        // Mark the field as wanting to consume the next Escape itself
        // (suggestions shown, chip armed or text being typed) so the
        // app-wide BackButtonManager does not close the details panel.
        function updateEscapeHold() {
            const hasFreeText = Array.from(container.childNodes).some(
                n => n.nodeType === Node.TEXT_NODE && n.textContent.trim() !== '');
            const hold = suggestionResults.length > 0 || !!selectedChip() || hasFreeText;
            container.setAttribute('data-escape-hold', hold ? 'true' : 'false');
        }

        // -- wiring ---------------------------------------------------------------------------

        container.setAttribute('data-empty', 'true');
        container.addEventListener('keydown', onKeyDown);
        container.addEventListener('input', onInput);
        container.addEventListener('paste', onPaste);
        container.addEventListener('click', (e) => {
            if (!e.target.closest || !e.target.closest('.tag-input-chip')) {
                clearSelectedChip();
            }
        });

        return {
            getChips() {
                return readChips(container).map(c => ({
                    name: c.name,
                    state: c.state,
                    id: c.id,
                    count: c.count,
                }));
            },
            clear() {
                clearTimeout(suggestTimer);
                clearTimeout(resolveTimer);
                fetchSeq++;
                container.innerHTML = '';
                suggestionResults = [];
                selectedIndex = -1;
                activeTextNode = null;
                renderSuggestions();
                updateEmptyState();
            },
            removeChipsWhere(fn) {
                readChips(container).forEach(c => {
                    if (fn({ name: c.name, state: c.state, id: c.id, count: c.count })) {
                        c.element.remove();
                    }
                });
                updateEmptyState();
            },
            focus() {
                container.focus();
                placeCaretEnd();
            },
            applyResolveResults,
            destroy() {
                clearTimeout(suggestTimer);
                clearTimeout(resolveTimer);
                fetchSeq++;
                activeTextNode = null;
                container.removeEventListener('keydown', onKeyDown);
                container.removeEventListener('input', onInput);
                container.removeEventListener('paste', onPaste);
            },
        };
    };

    // CommonJS export for Jest unit tests (no-op in the browser).
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            normalizeTagName,
            isValidTagName,
            splitTagTokens,
            endsWithSeparator,
            readChips,
        };
    }
})();
