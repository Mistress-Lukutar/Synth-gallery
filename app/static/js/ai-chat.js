/**
 * AI Chat panel - floating chat against the user's library.
 *
 * Talks to the conversation/chat REST + SSE endpoints:
 *   POST   /api/ai/chat/conversations
 *   GET    /api/ai/chat/conversations
 *   DELETE /api/ai/chat/conversations/{id}
 *   GET    /api/ai/chat/conversations/{id}/messages
 *   POST   /api/ai/chat/conversations/{id}/messages            (SSE stream)
 *   POST   /api/ai/chat/conversations/{id}/vision/{req}/decision (SSE stream)
 *
 * Global API: window.AiChat = { open, close, toggle, isOpen, openWithSelection }
 *
 * Pure helpers (parseSSEFrames / applyChatEvent / defaultBaseUrl) are exported
 * through a CommonJS guard for Jest tests.
 */
(function () {
    'use strict';

    // ========================================================================
    // Constants + pure helpers (Jest-tested)
    // ========================================================================

    var DEFAULT_BASE_URLS = {
        openai_compatible: 'https://api.openai.com/v1',
        anthropic: 'https://api.anthropic.com/v1',
        google_gemini: 'https://generativelanguage.googleapis.com/v1beta',
    };

    var PROTOCOL_LABELS = {
        openai_compatible: 'OpenAI-compatible',
        anthropic: 'Anthropic',
        google_gemini: 'Google Gemini',
    };

    function defaultBaseUrl(protocol) {
        return DEFAULT_BASE_URLS[protocol] || DEFAULT_BASE_URLS.openai_compatible;
    }

    /**
     * Parse complete SSE frames out of a (possibly partial) buffer.
     *
     * Returns { frames: [{event, data}], rest } where `rest` holds the
     * trailing incomplete frame (no blank-line terminator yet). Handles
     * "\n\n" and "\r\n\r\n" separators and CR/LF line endings. `event`
     * defaults to 'message'; `data` is JSON-parsed when possible, otherwise
     * the raw string is kept.
     */
    function parseSSEFrames(bufferText) {
        var text = String(bufferText == null ? '' : bufferText);
        var frames = [];

        // Find the end of the LAST complete frame (blank line separator).
        var separator = /\r?\n\r?\n/g;
        var lastEnd = -1;
        var match;
        while ((match = separator.exec(text)) !== null) {
            lastEnd = match.index + match[0].length;
        }
        if (lastEnd === -1) {
            return { frames: frames, rest: text };
        }

        var processable = text.slice(0, lastEnd);
        var rest = text.slice(lastEnd);

        var rawFrames = processable.split(/\r?\n\r?\n/);
        for (var i = 0; i < rawFrames.length; i++) {
            var frame = parseSSEFrameBody(rawFrames[i]);
            if (frame) frames.push(frame);
        }
        return { frames: frames, rest: rest };
    }

    function parseSSEFrameBody(raw) {
        if (!raw) return null;
        var lines = raw.split(/\r?\n/);
        var event = 'message';
        var dataLines = [];
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (line === '' || line.charAt(0) === ':') continue; // comment / keep-alive
            if (line.indexOf('event:') === 0) {
                event = line.slice(6).trim();
            } else if (line.indexOf('data:') === 0) {
                dataLines.push(line.slice(5).replace(/^ /, ''));
            }
            // Other SSE fields (id:, retry:) are ignored.
        }
        if (dataLines.length === 0) return null;
        var dataStr = dataLines.join('\n');
        var data = dataStr;
        try {
            data = JSON.parse(dataStr);
        } catch (e) {
            // Keep the raw string when the payload is not JSON.
        }
        return { event: event, data: data };
    }

    // ========================================================================
    // Chat state (pure transitions)
    // ========================================================================

    /**
     * State shape:
     * {
     *   messages: [ { role: 'user'|'assistant'|'tool', parts: [...] } ],
     *   streaming: bool,
     *   pendingVision: null | { request_id, items: [{id, title}] },
     *   stopReason: null | string
     * }
     *
     * Part shapes:
     *   { type: 'text', text }
     *   { type: 'reasoning', text }
     *   { type: 'context', item_ids: [...] }
     *   { type: 'tool', call_id, name, args, status: 'running'|'done'|'error'|'expired',
     *     summary, result, images }
     *   { type: 'error', text }
     */
    function newChatState() {
        return { messages: [], streaming: false, pendingVision: null, stopReason: null };
    }

    function cloneState(state) {
        var safe = state || newChatState();
        var messages = [];
        for (var i = 0; i < safe.messages.length; i++) {
            var m = safe.messages[i];
            messages.push({ role: m.role, parts: (m.parts || []).map(function (p) {
                return Object.assign({}, p);
            }) });
        }
        return {
            messages: messages,
            streaming: !!safe.streaming,
            pendingVision: safe.pendingVision
                ? { request_id: safe.pendingVision.request_id, items: (safe.pendingVision.items || []).slice() }
                : null,
            stopReason: safe.stopReason == null ? null : safe.stopReason,
        };
    }

    function lastMessageOf(messages, role) {
        var last = messages[messages.length - 1];
        if (!role) return last || null;
        return last && last.role === role ? last : null;
    }

    function ensureAssistantMessage(messages) {
        var last = lastMessageOf(messages, 'assistant');
        if (!last) {
            last = { role: 'assistant', parts: [] };
            messages.push(last);
        }
        return last;
    }

    /**
     * Append text to the trailing text part of the current assistant message
     * (creating message/part when missing).
     */
    function appendToTypedPart(message, partType, text) {
        var lastPart = message.parts[message.parts.length - 1];
        if (!lastPart || lastPart.type !== partType) {
            lastPart = { type: partType, text: '' };
            message.parts.push(lastPart);
        }
        lastPart.text = (lastPart.text || '') + (text || '');
    }

    function parseToolArgs(args) {
        if (args == null) return null;
        if (typeof args === 'object') return args;
        try {
            return JSON.parse(args);
        } catch (e) {
            return args; // keep raw string
        }
    }

    function findToolCard(messages, callId) {
        for (var i = 0; i < messages.length; i++) {
            var parts = messages[i].parts || [];
            for (var j = 0; j < parts.length; j++) {
                if (parts[j].type === 'tool' && parts[j].call_id === callId) {
                    return parts[j];
                }
            }
        }
        return null;
    }

    /**
     * Pure event reducer. Returns a NEW state; never mutates the input.
     * Unknown event types are ignored (returns an equal state).
     */
    function applyChatEvent(state, event) {
        var ev = event || {};
        var next;

        switch (ev.type) {
            case 'token': {
                next = cloneState(state);
                var msg = ensureAssistantMessage(next.messages);
                appendToTypedPart(msg, 'text', ev.text);
                next.streaming = true;
                return next;
            }

            case 'reasoning': {
                next = cloneState(state);
                var rmsg = ensureAssistantMessage(next.messages);
                appendToTypedPart(rmsg, 'reasoning', ev.text);
                next.streaming = true;
                return next;
            }

            case 'tool_start': {
                next = cloneState(state);
                var tmsg = ensureAssistantMessage(next.messages);
                tmsg.parts.push({
                    type: 'tool',
                    call_id: ev.call_id,
                    name: ev.name,
                    args: parseToolArgs(ev.arguments),
                    status: 'running',
                    summary: null,
                    result: null,
                    images: [],
                });
                next.streaming = true;
                return next;
            }

            case 'tool_result': {
                next = cloneState(state);
                var card = ev.call_id ? findToolCard(next.messages, ev.call_id) : null;
                var status = ev.is_error ? 'error' : 'done';
                if (card) {
                    card.status = status;
                    if (ev.summary != null) card.summary = ev.summary;
                } else {
                    // Result without a visible start (e.g. reconnect): keep it.
                    var umsg = ensureAssistantMessage(next.messages);
                    umsg.parts.push({
                        type: 'tool',
                        call_id: ev.call_id,
                        name: ev.name,
                        args: null,
                        status: status,
                        summary: ev.summary != null ? ev.summary : null,
                        result: null,
                        images: [],
                    });
                }
                return next;
            }

            case 'vision_request': {
                next = cloneState(state);
                next.pendingVision = {
                    request_id: ev.request_id,
                    items: Array.isArray(ev.items) ? ev.items.slice() : [],
                };
                return next;
            }

            // Internal pseudo-event fired after the user clicks Allow/Deny.
            case 'vision_decided': {
                next = cloneState(state);
                next.pendingVision = null;
                return next;
            }

            case 'turn_done': {
                next = cloneState(state);
                next.streaming = false;
                next.stopReason = ev.stop_reason == null ? null : ev.stop_reason;
                return next;
            }

            case 'error': {
                next = cloneState(state);
                var emsg = lastMessageOf(next.messages, 'assistant');
                var part = { type: 'error', text: ev.message != null ? String(ev.message) : 'Unknown error' };
                if (emsg) {
                    emsg.parts.push(part);
                } else {
                    next.messages.push({ role: 'assistant', parts: [part] });
                }
                next.streaming = false;
                return next;
            }

            default:
                return cloneState(state);
        }
    }

    /**
     * Append a locally-echoed user message (pure).
     */
    function appendUserMessage(state, text, itemIds) {
        var next = cloneState(state);
        var parts = [{ type: 'text', text: text || '' }];
        if (Array.isArray(itemIds) && itemIds.length > 0) {
            parts.push({ type: 'context', item_ids: itemIds.slice() });
        }
        next.messages.push({ role: 'user', parts: parts });
        return next;
    }

    function normalizeHistoryPart(part) {
        var p = Object.assign({}, part || {});
        if (p.type === 'tool_call') {
            p.type = 'tool';
            p.args = parseToolArgs(p.arguments != null ? p.arguments : p.args);
            delete p.arguments;
            if (!p.status) p.status = 'running';
            if (p.images == null) p.images = [];
        }
        if (p.type === 'tool') {
            if (p.args === undefined) p.args = parseToolArgs(p.arguments);
            if (p.result === undefined) p.result = p.content != null ? p.content : null;
            if (p.images == null || !Array.isArray(p.images)) p.images = [];
        }
        return p;
    }

    /**
     * Build chat state from GET .../messages history.
     * Tool_call parts without any matching tool_result become 'expired'.
     */
    function stateFromMessages(list) {
        var messages = [];
        var items = Array.isArray(list) ? list : [];
        var resultCallIds = {};

        var i, j, m, part;
        for (i = 0; i < items.length; i++) {
            m = items[i];
            var parts = (m.parts || []).map(normalizeHistoryPart);
            for (j = 0; j < parts.length; j++) {
                if (parts[j].type === 'tool_result' && parts[j].call_id) {
                    resultCallIds[parts[j].call_id] = true;
                }
            }
            messages.push({ role: m.role, parts: parts });
        }

        // Merge tool_result parts (role 'tool' messages) into their tool cards.
        var merged = [];
        for (i = 0; i < messages.length; i++) {
            m = messages[i];
            if (m.role === 'tool') {
                for (j = 0; j < m.parts.length; j++) {
                    part = m.parts[j];
                    if (part.type === 'tool_result') {
                        var card = part.call_id ? findToolCard(merged, part.call_id) : null;
                        if (card) {
                            card.status = part.is_error ? 'error' : 'done';
                            if (part.summary != null) card.summary = part.summary;
                            if (part.content != null) card.result = part.content;
                            if (Array.isArray(part.images) && part.images.length) {
                                card.images = part.images.slice();
                            }
                        } else {
                            merged.push({
                                role: 'assistant',
                                parts: [{
                                    type: 'tool',
                                    call_id: part.call_id,
                                    name: part.name,
                                    args: null,
                                    status: part.is_error ? 'error' : 'done',
                                    summary: part.summary != null ? part.summary : null,
                                    result: part.content != null ? part.content : null,
                                    images: Array.isArray(part.images) ? part.images.slice() : [],
                                }],
                            });
                        }
                    } else {
                        merged.push({ role: 'tool', parts: [part] });
                    }
                }
            } else {
                for (j = 0; j < m.parts.length; j++) {
                    part = m.parts[j];
                    if (part.type === 'tool' && !(part.call_id && resultCallIds[part.call_id])) {
                        part.status = 'expired';
                    }
                }
                merged.push(m);
            }
        }

        return { messages: merged, streaming: false, pendingVision: null, stopReason: null };
    }

    // ========================================================================
    // Small runtime helpers
    // ========================================================================

    function esc(text) {
        var fn = typeof window !== 'undefined' && window.escapeHtml ? window.escapeHtml : null;
        if (fn) return fn(text);
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function toast(message, isError) {
        if (typeof window !== 'undefined' && typeof window.showToast === 'function') {
            window.showToast(message, isError);
        }
    }

    function baseUrl() {
        return typeof window !== 'undefined' && window.SYNTH_BASE_URL != null
            ? window.SYNTH_BASE_URL
            : '';
    }

    function protocolLabel(protocol) {
        return PROTOCOL_LABELS[protocol] || protocol || '';
    }

    function renderMarkdownHtml(text) {
        if (typeof window !== 'undefined' && typeof window.marked !== 'undefined' && window.marked.parse) {
            try {
                var html = window.marked.parse(text);
                if (window.DOMPurify) {
                    html = window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
                }
                return html;
            } catch (e) {
                // fall through to plain text
            }
        }
        return '<pre class="ai-chat-plain">' + esc(text) + '</pre>';
    }

    // ========================================================================
    // Module state
    // ========================================================================

    var panel = null;
    var els = {};
    var state = newChatState();
    var conversationId = null;
    var conversations = [];
    var chatSettings = null;
    var abortController = null;
    var attachedSelection = false;
    var stickToBottom = true;
    var listVisible = false;
    var renderScheduled = false;
    var initialized = false;
    var sending = false;

    // ========================================================================
    // Panel shell (rendered once from a template string)
    // ========================================================================

    var PANEL_TEMPLATE = [
        '<div class="ai-chat-inner">',
        '  <aside class="ai-chat-list" id="ai-chat-list">',
        '    <div class="ai-chat-list-header">',
        '      <span class="ai-chat-list-title">Chats</span>',
        '      <button type="button" id="ai-chat-new-btn" class="btn btn-small">New chat</button>',
        '    </div>',
        '    <div class="ai-chat-conversations" id="ai-chat-conversations"></div>',
        '  </aside>',
        '  <div class="ai-chat-main">',
        '    <header class="ai-chat-header">',
        '      <button type="button" id="ai-chat-list-toggle" class="ai-chat-icon-btn" title="Toggle chat list" aria-label="Toggle chat list">',
        '        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
        '      </button>',
        '      <span class="ai-chat-title" id="ai-chat-title">New chat</span>',
        '      <span class="ai-chat-model-badge" id="ai-chat-model-badge" title="Active model"></span>',
        '      <button type="button" id="ai-chat-close-btn" class="ai-chat-icon-btn" title="Close" aria-label="Close AI chat">',
        '        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
        '      </button>',
        '    </header>',
        '    <div class="ai-chat-messages" id="ai-chat-messages"></div>',
        '    <div class="ai-chat-vision-slot" id="ai-chat-vision-slot"></div>',
        '    <footer class="ai-chat-composer">',
        '      <button type="button" class="ai-chat-attach-chip hidden" id="ai-chat-attach-chip" title="Attach the current gallery selection to the next message"></button>',
        '      <div class="ai-chat-input-row">',
        '        <textarea id="ai-chat-input" class="ai-chat-input" rows="1" placeholder="Ask about your library..."></textarea>',
        '        <button type="button" id="ai-chat-send-btn" class="btn btn-small ai-chat-send-btn">Send</button>',
        '      </div>',
        '    </footer>',
        '  </div>',
        '</div>',
    ].join('\n');

    function buildPanel() {
        if (!panel || panel.dataset.built) return;
        panel.innerHTML = PANEL_TEMPLATE;
        panel.dataset.built = 'true';

        els.list = document.getElementById('ai-chat-list');
        els.listToggle = document.getElementById('ai-chat-list-toggle');
        els.newBtn = document.getElementById('ai-chat-new-btn');
        els.conversations = document.getElementById('ai-chat-conversations');
        els.title = document.getElementById('ai-chat-title');
        els.modelBadge = document.getElementById('ai-chat-model-badge');
        els.closeBtn = document.getElementById('ai-chat-close-btn');
        els.messages = document.getElementById('ai-chat-messages');
        els.visionSlot = document.getElementById('ai-chat-vision-slot');
        els.attachChip = document.getElementById('ai-chat-attach-chip');
        els.input = document.getElementById('ai-chat-input');
        els.sendBtn = document.getElementById('ai-chat-send-btn');

        els.listToggle.addEventListener('click', toggleList);
        els.newBtn.addEventListener('click', resetToDraft);
        els.closeBtn.addEventListener('click', close);
        els.sendBtn.addEventListener('click', onSendButton);
        els.attachChip.addEventListener('click', function () {
            setAttached(!attachedSelection);
            renderComposer();
        });
        els.input.addEventListener('input', function () {
            autoResizeInput();
            renderComposer();
        });
        els.input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
            }
        });
        els.messages.addEventListener('scroll', function () {
            stickToBottom = isNearBottom();
        });

        // Tool card expand/collapse + vision decision (event delegation).
        els.messages.addEventListener('click', function (e) {
            var head = e.target.closest('.ai-tool-card-head');
            if (head) {
                var body = head.parentElement.querySelector('.ai-tool-card-body');
                if (body) body.classList.toggle('hidden');
                return;
            }
            var reasoning = e.target.closest('.ai-reasoning');
            if (reasoning) {
                var rbody = reasoning.querySelector('.ai-reasoning-body');
                if (rbody) rbody.classList.toggle('hidden');
            }
        });
        els.visionSlot.addEventListener('click', function (e) {
            var btn = e.target.closest('[data-vision-action]');
            if (!btn) return;
            decideVision(btn.dataset.visionAction === 'allow');
        });

        // Selection can change while the panel is open (click-driven).
        document.addEventListener('click', function () {
            if (isOpen()) renderComposer();
        });
    }

    function isNearBottom() {
        if (!els.messages) return true;
        var el = els.messages;
        return el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    }

    function scrollToBottom() {
        if (!els.messages) return;
        els.messages.scrollTop = els.messages.scrollHeight;
    }

    function autoResizeInput() {
        if (!els.input) return;
        els.input.style.height = 'auto';
        els.input.style.height = Math.min(els.input.scrollHeight, 120) + 'px';
    }

    function toggleList() {
        listVisible = !listVisible;
        panel.classList.toggle('with-list', listVisible);
        if (listVisible) loadConversations();
    }

    function setAttached(value) {
        attachedSelection = !!value && selectionIds().length > 0;
    }

    function selectionIds() {
        if (typeof window === 'undefined' || !window.selectedPhotos) return [];
        return Array.from(window.selectedPhotos || []);
    }

    // ========================================================================
    // Rendering
    // ========================================================================

    function scheduleRender() {
        if (renderScheduled) return;
        renderScheduled = true;
        (typeof window !== 'undefined' && window.requestAnimationFrame
            ? window.requestAnimationFrame
            : function (fn) { setTimeout(fn, 16); }
        )(function () {
            renderScheduled = false;
            render();
        });
    }

    function render() {
        if (!panel || !panel.dataset.built) return;
        renderHeader();
        renderMessages();
        renderVision();
        renderComposer();
    }

    function renderHeader() {
        var title = 'New chat';
        if (conversationId) {
            for (var i = 0; i < conversations.length; i++) {
                if (conversations[i].id === conversationId) {
                    title = conversations[i].title || title;
                    break;
                }
            }
        }
        els.title.textContent = title;

        var s = chatSettings && chatSettings.settings ? chatSettings.settings : null;
        if (s && (s.model_display_name || s.active_model_id)) {
            var label = (s.model_display_name || s.active_model_id) +
                ' \u00b7 ' + (s.provider_label || protocolLabel(s.protocol) || 'unknown');
            els.modelBadge.textContent = label;
            els.modelBadge.classList.remove('ai-chat-badge-empty');
            els.modelBadge.title = s.supports_vision === false
                ? 'This model may not support images'
                : 'Active model';
        } else {
            els.modelBadge.textContent = 'No model configured';
            els.modelBadge.classList.add('ai-chat-badge-empty');
            els.modelBadge.title = 'Set up a provider in Settings \u2192 AI Assistant';
        }
    }

    function renderMessages() {
        var container = els.messages;
        var wasNearBottom = stickToBottom;
        var html = [];

        if (state.messages.length === 0) {
            html.push(
                '<div class="ai-chat-welcome">',
                '  <div class="ai-chat-welcome-icon">' + SPARKLE_SVG + '</div>',
                '  <p class="ai-chat-welcome-title">Ask about your library</p>',
                '  <p class="ai-chat-welcome-hint">Tagging, albums, search \u2014 or anything about your media.</p>',
                '</div>'
            );
        }

        for (var i = 0; i < state.messages.length; i++) {
            html.push(renderMessage(state.messages[i]));
        }
        if (state.streaming && state.messages.length > 0) {
            html.push('<div class="ai-chat-typing"><span></span><span></span><span></span></div>');
        }

        container.innerHTML = html.join('');
        if (wasNearBottom) scrollToBottom();
    }

    function renderMessage(message) {
        var parts = message.parts || [];
        var html = '';
        for (var i = 0; i < parts.length; i++) {
            html += renderPart(message, parts[i]);
        }
        if (message.role === 'user') {
            return '<div class="ai-msg-user">' + (html || '&nbsp;') + '</div>';
        }
        if (message.role === 'assistant') {
            return '<div class="ai-msg-assistant">' + (html || '') + '</div>';
        }
        return '<div class="ai-msg-tool">' + html + '</div>';
    }

    function renderPart(message, part) {
        switch (part.type) {
            case 'text':
                if (message.role === 'user') {
                    return '<div class="ai-msg-text">' + esc(part.text) + '</div>';
                }
                return '<div class="ai-chat-md markdown-preview">' + renderMarkdownHtml(part.text || '') + '</div>';
            case 'reasoning':
                return '<div class="ai-reasoning">' +
                    '<span class="ai-reasoning-label">Thinking</span>' +
                    '<div class="ai-reasoning-body hidden">' + esc(part.text) + '</div>' +
                    '</div>';
            case 'context':
                return '<div class="ai-chat-chips">' + renderContextChip(part.item_ids) + '</div>';
            case 'tool':
                return renderToolCard(part);
            case 'error':
                return '<div class="ai-msg-error">' + esc(part.text) + '</div>';
            default:
                return '';
        }
    }

    function renderContextChip(itemIds) {
        var ids = Array.isArray(itemIds) ? itemIds : [];
        if (ids.length === 0) return '';
        return '<span class="ai-chip" title="' + esc(ids.join(', ')) + '">' +
            CONTEXT_SVG + ' ' + ids.length + (ids.length === 1 ? ' item' : ' items') +
            '</span>';
    }

    function toolStatusClass(status) {
        return 'ai-tool-' + (status || 'running');
    }

    function toolStatusText(part) {
        switch (part.status) {
            case 'done': return part.summary || 'Done';
            case 'error': return part.summary || 'Failed';
            case 'expired': return 'Expired';
            default: return 'Running...';
        }
    }

    function renderToolCard(part) {
        var argsText = '';
        if (part.args != null) {
            argsText = typeof part.args === 'string' ? part.args : JSON.stringify(part.args, null, 2);
        }
        var resultText = part.result == null ? '' : String(part.result);

        var body = '';
        if (argsText) body += '<div class="ai-tool-section"><span class="ai-tool-section-label">Arguments</span><pre>' + esc(argsText) + '</pre></div>';
        if (resultText) body += '<div class="ai-tool-section"><span class="ai-tool-section-label">Result</span><pre>' + esc(resultText) + '</pre></div>';
        var images = Array.isArray(part.images) ? part.images : [];
        if (images.length) {
            body += '<div class="ai-tool-section"><span class="ai-tool-section-label">Images</span><div class="ai-vision-grid">';
            for (var i = 0; i < images.length; i++) {
                body += '<img src="' + esc(baseUrl()) + '/files/' + esc(images[i]) + '/thumbnail" loading="lazy" alt="tool result image" title="' + esc(images[i]) + '">';
            }
            body += '</div></div>';
        }

        return '<div class="ai-tool-card ' + toolStatusClass(part.status) + '">' +
            '<button type="button" class="ai-tool-card-head">' +
            '  <span class="ai-tool-dot" aria-hidden="true"></span>' +
            '  <span class="ai-tool-name">' + esc(part.name || 'tool') + '</span>' +
            '  <span class="ai-tool-summary">' + esc(toolStatusText(part)) + '</span>' +
            '  <svg class="ai-tool-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14"><polyline points="6 9 12 15 18 9"/></svg>' +
            '</button>' +
            '<div class="ai-tool-card-body hidden">' + body + '</div>' +
            '</div>';
    }

    function renderVision() {
        var pending = state.pendingVision;
        if (!pending) {
            els.visionSlot.innerHTML = '';
            els.visionSlot.classList.add('hidden');
            return;
        }
        var items = Array.isArray(pending.items) ? pending.items : [];
        var grid = '';
        for (var i = 0; i < items.length; i++) {
            var it = items[i] || {};
            grid += '<img src="' + esc(baseUrl()) + '/files/' + esc(it.id) + '/thumbnail" loading="lazy" alt="" title="' + esc(it.title || it.id || '') + '">';
        }
        els.visionSlot.innerHTML =
            '<div class="ai-vision-card">' +
            '  <div class="ai-vision-title">Allow viewing the next ' + items.length +
            (items.length === 1 ? ' image?' : ' images?') + '</div>' +
            '  <div class="ai-vision-grid">' + grid + '</div>' +
            '  <div class="ai-vision-actions">' +
            '    <button type="button" class="btn btn-small" data-vision-action="allow">Allow</button>' +
            '    <button type="button" class="btn btn-small btn-secondary" data-vision-action="deny">Deny</button>' +
            '  </div>' +
            '</div>';
        els.visionSlot.classList.remove('hidden');
    }

    function renderComposer() {
        var pending = !!state.pendingVision;
        var streaming = !!state.streaming;

        els.input.disabled = pending;
        els.input.placeholder = pending
            ? 'Decide on the image request first...'
            : 'Ask about your library...';

        if (streaming) {
            els.sendBtn.textContent = 'Stop';
            els.sendBtn.classList.add('ai-chat-stop');
            els.sendBtn.disabled = false;
            els.sendBtn.title = 'Stop generating';
        } else {
            els.sendBtn.textContent = 'Send';
            els.sendBtn.classList.remove('ai-chat-stop');
            els.sendBtn.disabled = pending || !els.input.value.trim();
            els.sendBtn.title = 'Send message';
        }

        var ids = selectionIds();
        if (ids.length > 0) {
            els.attachChip.classList.remove('hidden');
            els.attachChip.innerHTML = CONTEXT_SVG + ' Attach selection (' + ids.length + ')' + (attachedSelection ? ' \u2713' : '');
            els.attachChip.classList.toggle('ai-chat-attach-on', attachedSelection);
        } else {
            attachedSelection = false;
            els.attachChip.classList.add('hidden');
        }
    }

    // ========================================================================
    // Conversation list
    // ========================================================================

    function renderConversations() {
        if (!els.conversations) return;
        if (conversations.length === 0) {
            els.conversations.innerHTML = '<p class="ai-chat-list-empty">No chats yet</p>';
            return;
        }
        var html = '';
        for (var i = 0; i < conversations.length; i++) {
            var c = conversations[i];
            var active = c.id === conversationId;
            html += '<div class="ai-chat-conversation-item' + (active ? ' active' : '') + '" data-conversation-id="' + esc(c.id) + '">' +
                '<span class="ai-chat-conversation-title" title="' + esc(c.title || 'Untitled chat') + '">' + esc(c.title || 'Untitled chat') + '</span>' +
                '<button type="button" class="ai-chat-conversation-delete" data-delete-id="' + esc(c.id) + '" title="Delete chat" aria-label="Delete chat">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' +
                '</button>' +
                '</div>';
        }
        els.conversations.innerHTML = html;
    }

    function onConversationListClick(e) {
        var del = e.target.closest('.ai-chat-conversation-delete');
        if (del) {
            deleteConversation(del.dataset.deleteId);
            return;
        }
        var item = e.target.closest('.ai-chat-conversation-item');
        if (item) selectConversation(item.dataset.conversationId);
    }

    async function loadConversations() {
        try {
            var resp = await fetch(baseUrl() + '/api/ai/chat/conversations', { credentials: 'include' });
            if (!resp.ok) return;
            var data = await resp.json();
            conversations = Array.isArray(data.conversations) ? data.conversations : [];
            renderConversations();
            renderHeader();
        } catch (err) {
            console.warn('[ai-chat] Failed to load conversations:', err);
        }
    }

    async function selectConversation(id) {
        if (!id) return;
        if (state.streaming && abortController) abortController.abort();
        conversationId = id;
        try {
            var resp = await fetch(baseUrl() + '/api/ai/chat/conversations/' + encodeURIComponent(id) + '/messages', { credentials: 'include' });
            if (!resp.ok) {
                toast('Failed to load chat (' + resp.status + ')', true);
                return;
            }
            var data = await resp.json();
            state = stateFromMessages(data.messages);
            renderConversations();
            render();
        } catch (err) {
            console.error('[ai-chat] Failed to load messages:', err);
            toast('Failed to load chat', true);
        }
    }

    async function deleteConversation(id) {
        if (!id) return;
        if (!window.confirm('Delete this chat?')) return;
        try {
            var resp = await csrfFetch(baseUrl() + '/api/ai/chat/conversations/' + encodeURIComponent(id), { method: 'DELETE' });
            if (!resp.ok) {
                var detail = await resp.json().then(function (d) { return d && d.detail; }).catch(function () { return null; });
                toast(detail || 'Failed to delete chat', true);
                return;
            }
            if (id === conversationId) resetToDraft();
            await loadConversations();
        } catch (err) {
            console.error('[ai-chat] Failed to delete conversation:', err);
            toast('Failed to delete chat', true);
        }
    }

    function resetToDraft() {
        if (state.streaming && abortController) abortController.abort();
        conversationId = null;
        state = newChatState();
        renderConversations();
        render();
        if (els.input) els.input.focus();
    }

    // ========================================================================
    // Settings (model badge)
    // ========================================================================

    async function loadSettings() {
        try {
            var resp = await fetch(baseUrl() + '/api/user/ai/settings', { credentials: 'include' });
            chatSettings = resp.ok ? await resp.json() : null;
        } catch (err) {
            console.warn('[ai-chat] Failed to load AI settings:', err);
            chatSettings = null;
        }
        renderHeader();
    }

    // ========================================================================
    // SSE streaming
    // ========================================================================

    function handleFrame(frame) {
        if (!frame || typeof frame.data !== 'object' || frame.data === null) return;
        var event = Object.assign({}, frame.data, { type: frame.event });
        state = applyChatEvent(state, event);
        scheduleRender();
        if (frame.event === 'turn_done') loadConversations();
    }

    async function handleStreamError(resp) {
        var detail = null;
        try {
            var data = await resp.json();
            detail = data ? data.detail : null;
        } catch (e) {
            detail = null;
        }
        if (typeof detail !== 'string' || !detail) {
            detail = 'Chat request failed (' + resp.status + ')';
        }
        toast(detail, true);
        if (resp.status === 403 && /encryption key/i.test(detail)) {
            toast('Your encryption key is not available. Reload the page and log in again; if it persists, check Settings.', true);
        }
    }

    /**
     * POST a chat endpoint and consume its SSE stream into state.
     */
    async function streamChat(path, body) {
        var controller = new AbortController();
        abortController = controller;
        renderComposer();
        try {
            var resp = await csrfFetch(baseUrl() + path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
                body: JSON.stringify(body || {}),
                signal: controller.signal,
                credentials: 'include',
            });
            if (!resp.ok) {
                await handleStreamError(resp);
                return;
            }
            if (!resp.body || typeof resp.body.getReader !== 'function') {
                toast('Streaming is not supported by this browser', true);
                return;
            }
            var reader = resp.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';
            for (;;) {
                var chunk = await reader.read();
                if (chunk.done) break;
                buffer += decoder.decode(chunk.value, { stream: true });
                var parsed = parseSSEFrames(buffer);
                buffer = parsed.rest;
                for (var i = 0; i < parsed.frames.length; i++) {
                    handleFrame(parsed.frames[i]);
                }
            }
        } catch (err) {
            if (!err || err.name !== 'AbortError') {
                console.error('[ai-chat] Stream failed:', err);
                toast('Chat request failed', true);
            }
        } finally {
            abortController = null;
            if (state.streaming) {
                state = applyChatEvent(state, { type: 'turn_done', stop_reason: 'stopped' });
            }
            render();
            renderComposer();
            loadConversations();
        }
    }

    // ========================================================================
    // Sending
    // ========================================================================

    function onSendButton() {
        if (state.streaming && abortController) {
            abortController.abort();
            return;
        }
        sendMessage();
    }

    async function ensureConversation() {
        if (conversationId) return conversationId;
        var resp = await csrfFetch(baseUrl() + '/api/ai/chat/conversations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
        });
        if (!resp.ok) {
            var detail = null;
            try {
                var data = await resp.json();
                detail = data ? data.detail : null;
            } catch (e) { /* ignore */ }
            toast(typeof detail === 'string' && detail ? detail : 'Failed to create chat', true);
            throw new Error('conversation create failed');
        }
        var payload = await resp.json();
        conversationId = payload.conversation && payload.conversation.id ? payload.conversation.id : null;
        if (!conversationId) throw new Error('conversation id missing');
        renderConversations();
        return conversationId;
    }

    async function sendMessage() {
        if (!panel || !panel.dataset.built) return;
        if (state.pendingVision) return;
        if (state.streaming || sending) return;

        var text = els.input.value.trim();
        if (!text) return;

        sending = true;
        try {
            var convId;
            try {
                convId = await ensureConversation();
            } catch (e) {
                return;
            }

            var itemIds = attachedSelection ? selectionIds() : [];
            var body = { text: text };
            if (itemIds.length > 0) body.item_ids = itemIds;

            state = appendUserMessage(state, text, itemIds);
            els.input.value = '';
            autoResizeInput();
            setAttached(false);
            stickToBottom = true;
            render();

            await streamChat('/api/ai/chat/conversations/' + encodeURIComponent(convId) + '/messages', body);
        } finally {
            sending = false;
        }
    }

    function decideVision(approved) {
        var pending = state.pendingVision;
        if (!pending || !conversationId) return;
        // The confirmation stream (if still open) is replaced by the
        // decision's continuation stream.
        if (state.streaming && abortController) abortController.abort();
        state = applyChatEvent(state, { type: 'vision_decided' });
        render();
        streamChat(
            '/api/ai/chat/conversations/' + encodeURIComponent(conversationId) +
            '/vision/' + encodeURIComponent(pending.request_id) + '/decision',
            { approved: !!approved }
        );
    }

    // ========================================================================
    // Open / close
    // ========================================================================

    function isOpen() {
        return !!panel && !panel.classList.contains('hidden');
    }

    function open() {
        if (!panel) return;
        if (isOpen()) return;
        buildPanel();
        panel.classList.remove('hidden');
        syncSidebarButton();
        if (typeof window !== 'undefined' && window.BackButtonManager) {
            window.BackButtonManager.register('ai-chat-panel', close);
        }
        loadSettings();
        loadConversations();
        render();
        renderComposer();
        els.input.focus();
    }

    function close() {
        if (!panel || !isOpen()) return;
        if (state.streaming && abortController) abortController.abort();
        panel.classList.add('hidden');
        syncSidebarButton();
        if (typeof window !== 'undefined' && window.BackButtonManager) {
            if (window.BackButtonManager.isRegistered('ai-chat-panel')) {
                window.BackButtonManager.unregister('ai-chat-panel');
            }
        }
    }

    function toggle() {
        if (isOpen()) close();
        else open();
    }

    function openWithSelection() {
        setAttached(true);
        open();
        renderComposer();
    }

    function syncSidebarButton() {
        var btn = document.getElementById('ai-chat-toggle');
        if (btn) btn.classList.toggle('active', isOpen());
    }

    // ========================================================================
    // Icons
    // ========================================================================

    var SPARKLE_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="28" height="28">' +
        '<path d="M12 3l1.9 5.7a2 2 0 0 0 1.3 1.3L21 12l-5.8 1.9a2 2 0 0 0-1.3 1.3L12 21l-1.9-5.8a2 2 0 0 0-1.3-1.3L3 12l5.8-2a2 2 0 0 0 1.3-1.3z"/>' +
        '<path d="M19 3v4"/><path d="M17 5h4"/>' +
        '</svg>';

    var CONTEXT_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="13" height="13">' +
        '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/>' +
        '</svg>';

    // ========================================================================
    // Init
    // ========================================================================

    function init() {
        if (initialized) return;
        panel = document.getElementById('ai-chat-panel');
        if (!panel) return; // standalone pages (settings, login) have no panel
        initialized = true;
        buildPanel();

        var toggleBtn = document.getElementById('ai-chat-toggle');
        if (toggleBtn) toggleBtn.addEventListener('click', toggle);

        var askBtn = document.getElementById('selection-ask-ai-btn');
        if (askBtn) askBtn.addEventListener('click', openWithSelection);

        els.conversations.addEventListener('click', onConversationListClick);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // ========================================================================
    // Public API
    // ========================================================================

    window.AiChat = {
        open: open,
        close: close,
        toggle: toggle,
        isOpen: isOpen,
        openWithSelection: openWithSelection,
    };

    // CommonJS guard for Jest
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            parseSSEFrames: parseSSEFrames,
            applyChatEvent: applyChatEvent,
            appendUserMessage: appendUserMessage,
            stateFromMessages: stateFromMessages,
            newChatState: newChatState,
            defaultBaseUrl: defaultBaseUrl,
        };
    }
})();
