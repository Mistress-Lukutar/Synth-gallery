/**
 * Settings page - AI Assistant section.
 *
 * Manages per-user AI providers (API keys stored DEK-encrypted server-side),
 * the per-provider model catalogue and the active provider/model pair used
 * by the AI chat panel.
 *
 * Endpoints (see app/routes/ai_provider_settings.py):
 *   GET    /api/user/ai/providers
 *   POST   /api/user/ai/providers
 *   PUT    /api/user/ai/providers/{id}       (empty api_key = keep existing)
 *   DELETE /api/user/ai/providers/{id}
 *   POST   /api/user/ai/providers/{id}/fetch-models
 *   PUT    /api/user/ai/providers/{id}/models/{model_id}/limits
 *   GET    /api/user/ai/settings
 *   PUT    /api/user/ai/settings
 */
(function () {
    'use strict';

    var BASE_URL = window.SYNTH_BASE_URL || '';

    var PROTOCOLS = [
        { value: 'openai_compatible', label: 'OpenAI-compatible' },
        { value: 'anthropic', label: 'Anthropic' },
        { value: 'google_gemini', label: 'Google Gemini' },
    ];

    var DEFAULT_BASE_URLS = {
        openai_compatible: 'https://api.openai.com/v1',
        anthropic: 'https://api.anthropic.com/v1',
        google_gemini: 'https://generativelanguage.googleapis.com/v1beta',
    };

    function defaultBaseUrl(protocol) {
        return DEFAULT_BASE_URLS[protocol] || DEFAULT_BASE_URLS.openai_compatible;
    }

    function protocolLabel(protocol) {
        for (var i = 0; i < PROTOCOLS.length; i++) {
            if (PROTOCOLS[i].value === protocol) return PROTOCOLS[i].label;
        }
        return protocol || 'Unknown';
    }

    function esc(text) {
        if (typeof window.escapeHtml === 'function') return window.escapeHtml(text);
        if (!text) return '';
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function toast(message, isError) {
        if (typeof window.showToast === 'function') window.showToast(message, isError);
    }

    /**
     * "10971520" -> "10.5M", "512000" -> "500K" (empty string for unknown).
     */
    function formatTokens(n) {
        var v = Number(n);
        if (!isFinite(v) || v <= 0) return '';
        function trim(x) { return String(Math.round(x * 10) / 10); }
        if (v >= 1e9) return trim(v / 1e9) + 'B';
        if (v >= 1e6) return trim(v / 1e6) + 'M';
        if (v >= 1e3) return trim(v / 1e3) + 'K';
        return String(v);
    }

    // Extract {detail} from an error response (detail may be a string or a
    // FastAPI validation list).
    async function errorDetail(resp, fallback) {
        try {
            var data = await resp.json();
            var detail = data ? data.detail : null;
            if (typeof detail === 'string' && detail) return detail;
            if (Array.isArray(detail) && detail.length) {
                var first = detail[0] || {};
                return first.msg || fallback;
            }
        } catch (e) { /* not JSON */ }
        return fallback;
    }

    function warnEncryptionKey(detail) {
        if (typeof detail === 'string' && /encryption key/i.test(detail)) {
            toast('Your encryption key is not available. Reload the page and log in again; if it persists, check Settings.', true);
        }
    }

    // ========================================================================
    // State
    // ========================================================================

    var providers = [];
    var settings = null;
    var modelsCache = {};      // providerId -> [model, ...] from fetch-models
    var editingProviderId = null; // provider id | 'new' | null
    var editingLimits = null;  // { providerId, modelId } while inline form is open
    var loading = false;

    var els = {};

    function getEl(id) {
        if (!els[id]) els[id] = document.getElementById(id);
        return els[id];
    }

    function providerById(id) {
        for (var i = 0; i < providers.length; i++) {
            if (String(providers[i].id) === String(id)) return providers[i];
        }
        return null;
    }

    // ========================================================================
    // Data loading
    // ========================================================================

    async function loadProviders() {
        var listEl = getEl('ai-providers-list');
        try {
            var resp = await fetch(BASE_URL + '/api/user/ai/providers', { credentials: 'include' });
            if (!resp.ok) {
                var detail = await errorDetail(resp, 'Failed to load providers');
                toast(detail, true);
                warnEncryptionKey(detail);
                listEl.innerHTML = '<p class="ai-no-providers">Failed to load providers</p>';
                return;
            }
            var data = await resp.json();
            providers = Array.isArray(data.providers) ? data.providers : [];
            renderProviders();
            renderActiveModel();
        } catch (err) {
            console.error('[settings-ai] Failed to load providers:', err);
            listEl.innerHTML = '<p class="ai-no-providers">Failed to load providers</p>';
        }
    }

    async function loadSettings() {
        try {
            var resp = await fetch(BASE_URL + '/api/user/ai/settings', { credentials: 'include' });
            if (resp.ok) {
                var data = await resp.json();
                settings = data && data.settings ? data.settings : null;
            }
        } catch (err) {
            console.error('[settings-ai] Failed to load AI settings:', err);
        }
        renderActiveModel();
    }

    function renderActiveModel() {
        var el = getEl('ai-active-model');
        if (!el) return;
        var s = settings || {};
        var text = null;
        if (s.provider_label && (s.model_display_name || s.active_model_id)) {
            text = s.provider_label + ' \u00b7 ' + (s.model_display_name || s.active_model_id);
        } else if (s.active_model_id) {
            text = s.active_model_id;
        }
        if (!text) {
            el.innerHTML = '<span class="ai-model-none">Not configured</span>';
            return;
        }
        var limits = [];
        if (s.context_tokens) limits.push(formatTokens(s.context_tokens) + ' ctx');
        if (s.max_output_tokens) limits.push(formatTokens(s.max_output_tokens) + ' out');
        if (limits.length) text += ' \u00b7 ' + limits.join(' \u00b7 ');
        el.textContent = text;
    }

    // ========================================================================
    // Providers list rendering
    // ========================================================================

    function renderProviders() {
        var listEl = getEl('ai-providers-list');
        if (!listEl) return;

        if (providers.length === 0 && editingProviderId !== 'new') {
            listEl.innerHTML = '<p class="ai-no-providers">No providers configured yet</p>';
            return;
        }

        var html = '';
        if (editingProviderId === 'new') {
            html += '<div class="ai-provider-card" data-new-form-slot></div>';
        }
        for (var i = 0; i < providers.length; i++) {
            var p = providers[i];
            var editing = editingProviderId != null && String(editingProviderId) === String(p.id);
            html += '<div class="ai-provider-card" data-provider-id="' + esc(p.id) + '">' +
                '<div class="ai-provider-row">' +
                '  <div class="ai-provider-info">' +
                '    <span class="ai-provider-label">' + esc(p.label || 'Provider') + '</span>' +
                '    <span class="ai-protocol-badge">' + esc(protocolLabel(p.protocol)) + '</span>' +
                '  </div>' +
                '  <div class="ai-provider-meta">' +
                '    <span title="' + esc(p.base_url || '') + '">' + esc(p.base_url || '') + '</span>' +
                '    <span>' + (p.api_key_masked ? 'Key ' + esc(p.api_key_masked) : 'No API key') +
                ' \u00b7 ' + (typeof p.model_count === 'number' ? p.model_count : 0) + ' models</span>' +
                '  </div>' +
                '  <div class="credential-actions">' +
                '    <button type="button" data-action="fetch-models" data-id="' + esc(p.id) + '">Fetch models</button>' +
                '    <button type="button" data-action="edit" data-id="' + esc(p.id) + '">Edit</button>' +
                '    <button type="button" class="btn-delete" data-action="delete" data-id="' + esc(p.id) + '">Delete</button>' +
                '  </div>' +
                '</div>' +
                (editing ? '<div class="ai-provider-form" data-form-slot></div>' : '') +
                '</div>';
        }
        listEl.innerHTML = html;

        if (editingProviderId === 'new') {
            var newSlot = listEl.querySelector('[data-new-form-slot]');
            if (newSlot) renderProviderForm(newSlot, null);
        }
        if (editingProviderId != null && editingProviderId !== 'new') {
            var slot = listEl.querySelector('.ai-provider-card[data-provider-id="' + CSS.escape(String(editingProviderId)) + '"] [data-form-slot]');
            if (slot) renderProviderForm(slot, providerById(editingProviderId));
        }
    }

    /**
     * Inline create/edit form. When `provider` is null the form creates a new
     * provider; otherwise it edits the existing one (blank key = keep).
     */
    function renderProviderForm(container, provider) {
        if (!container) return;
        var isEdit = !!provider;
        var protocol = (provider && provider.protocol) || PROTOCOLS[0].value;

        var options = '';
        for (var i = 0; i < PROTOCOLS.length; i++) {
            options += '<option value="' + PROTOCOLS[i].value + '"' +
                (PROTOCOLS[i].value === protocol ? ' selected' : '') + '>' +
                esc(PROTOCOLS[i].label) + '</option>';
        }

        container.innerHTML =
            '<div class="form-stack">' +
            '  <input type="text" data-field="label" placeholder="Label (e.g., OpenAI)" maxlength="100" aria-label="Provider label" value="' + esc(provider && provider.label || '') + '">' +
            '  <select data-field="protocol" aria-label="Protocol">' + options + '</select>' +
            '  <input type="text" data-field="base_url" placeholder="Base URL" aria-label="Base URL" value="' + esc(provider && provider.base_url || defaultBaseUrl(protocol)) + '">' +
            '  <input type="password" data-field="api_key" placeholder="' + (isEdit ? 'leave blank to keep current' : 'API key') + '" aria-label="API key" autocomplete="new-password">' +
            '  <div class="ai-provider-form-actions">' +
            '    <button type="button" class="btn btn-small" data-form-action="save">' + (isEdit ? 'Save' : 'Add provider') + '</button>' +
            '    <button type="button" class="btn btn-small btn-secondary" data-form-action="cancel">Cancel</button>' +
            '  </div>' +
            '</div>';

        var protocolSelect = container.querySelector('[data-field="protocol"]');
        var baseUrlInput = container.querySelector('[data-field="base_url"]');

        // Changing protocol refills the base URL only while the user has not
        // manually edited it.
        baseUrlInput.addEventListener('input', function () {
            baseUrlInput.dataset.manual = 'true';
        });
        protocolSelect.addEventListener('change', function () {
            if (baseUrlInput.dataset.manual !== 'true') {
                baseUrlInput.value = defaultBaseUrl(protocolSelect.value);
            }
        });

        container.querySelector('[data-form-action="cancel"]').addEventListener('click', function () {
            editingProviderId = null;
            renderProviders();
        });

        container.querySelector('[data-form-action="save"]').addEventListener('click', async function () {
            var label = container.querySelector('[data-field="label"]').value.trim();
            var chosenProtocol = protocolSelect.value;
            var chosenBaseUrl = baseUrlInput.value.trim();
            var apiKey = container.querySelector('[data-field="api_key"]').value;

            if (!label) { toast('Label is required', true); return; }
            if (!chosenBaseUrl) { toast('Base URL is required', true); return; }
            if (!isEdit && !apiKey) { toast('API key is required', true); return; }

            var payload = { label: label, protocol: chosenProtocol, base_url: chosenBaseUrl };
            if (apiKey) payload.api_key = apiKey;

            var url = isEdit
                ? BASE_URL + '/api/user/ai/providers/' + encodeURIComponent(provider.id)
                : BASE_URL + '/api/user/ai/providers';
            try {
                var resp = await csrfFetch(url, {
                    method: isEdit ? 'PUT' : 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                if (!resp.ok) {
                    var detail = await errorDetail(resp, 'Failed to save provider');
                    toast(detail, true);
                    warnEncryptionKey(detail);
                    return;
                }
                var data = await resp.json();
                if (data && data.provider && modelsCache[String(data.provider.id)]) {
                    delete modelsCache[String(data.provider.id)];
                }
                editingProviderId = null;
                toast(isEdit ? 'Provider updated' : 'Provider added', false);
                await loadProviders();
                await loadSettings();
            } catch (err) {
                console.error('[settings-ai] Failed to save provider:', err);
                toast('Failed to save provider', true);
            }
        });
    }

    // ========================================================================
    // Actions
    // ========================================================================

    async function fetchModels(providerId) {
        var btn = getEl('ai-providers-list').querySelector(
            '.ai-provider-card[data-provider-id="' + CSS.escape(String(providerId)) + '"] [data-action="fetch-models"]'
        );
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Fetching...';
        }
        try {
            var resp = await csrfFetch(BASE_URL + '/api/user/ai/providers/' + encodeURIComponent(providerId) + '/fetch-models', {
                method: 'POST',
            });
            if (!resp.ok) {
                var detail = await errorDetail(resp, 'Failed to fetch models (' + resp.status + ')');
                toast(detail, true);
                warnEncryptionKey(detail);
                renderProviders();
                return;
            }
            var data = await resp.json();
            var models = Array.isArray(data.models) ? data.models : [];
            modelsCache[String(providerId)] = models;
            toast((typeof data.fetched === 'number' ? data.fetched : models.length) + ' models fetched', false);
            renderProviders();
            renderActiveModel();
        } catch (err) {
            console.error('[settings-ai] Failed to fetch models:', err);
            toast('Failed to fetch models', true);
            renderProviders();
        }
    }

    async function deleteProvider(providerId) {
        var p = providerById(providerId);
        var label = p ? p.label : 'this provider';
        if (!window.confirm('Delete provider "' + label + '" and its cached models?')) return;
        try {
            var resp = await csrfFetch(BASE_URL + '/api/user/ai/providers/' + encodeURIComponent(providerId), {
                method: 'DELETE',
            });
            if (!resp.ok) {
                var detail = await errorDetail(resp, 'Failed to delete provider');
                toast(detail, true);
                return;
            }
            delete modelsCache[String(providerId)];
            toast('Provider deleted', false);
            if (editingProviderId != null && String(editingProviderId) === String(providerId)) {
                editingProviderId = null;
            }
            await loadProviders();
            await loadSettings();
        } catch (err) {
            console.error('[settings-ai] Failed to delete provider:', err);
            toast('Failed to delete provider', true);
        }
    }

    // ========================================================================
    // Model chooser modal
    // ========================================================================

    function openModelModal() {
        if (providers.length === 0) {
            toast('Add a provider first, then fetch its models', true);
            return;
        }
        renderModelModal();
        getEl('ai-model-modal').classList.remove('hidden');
    }

    function closeModelModal() {
        var modal = getEl('ai-model-modal');
        if (modal) modal.classList.add('hidden');
        editingLimits = null;
    }

    var PENCIL_SVG =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">' +
        '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>';

    /**
     * Inline context/output limits editor; replaces the model row while
     * open. Empty input = "unknown / provider-reported".
     */
    function renderLimitsForm(m, providerId) {
        var action = ' data-provider-id="' + esc(providerId) + '" data-model-id="' + esc(m.model_id || '') + '"';
        return '<div class="ai-limits-form" data-provider-id="' + esc(providerId) + '" data-model-id="' + esc(m.model_id || '') + '">' +
            '<div class="ai-limits-title">' + esc(m.display_name || m.model_id || 'Model') + '</div>' +
            '<div class="ai-limits-fields">' +
            '  <label>Context tokens' +
            '    <input type="number" min="1" step="1" inputmode="numeric" data-limit-field="context_tokens"' +
            '           placeholder="auto" aria-label="Context tokens" value="' + (m.context_tokens || '') + '">' +
            '  </label>' +
            '  <label>Max output tokens' +
            '    <input type="number" min="1" step="1" inputmode="numeric" data-limit-field="max_output_tokens"' +
            '           placeholder="auto" aria-label="Max output tokens" value="' + (m.max_output_tokens || '') + '">' +
            '  </label>' +
            '</div>' +
            '<div class="ai-limits-actions">' +
            '  <button type="button" class="btn btn-small" data-modal-action="limits-save"' + action + '>Save</button>' +
            '  <button type="button" class="btn btn-small btn-secondary" data-modal-action="limits-cancel">Cancel</button>' +
            '</div>' +
            '</div>';
    }

    function renderModelModal() {
        var body = getEl('ai-model-modal-body');
        if (!body) return;

        var html = '';
        for (var i = 0; i < providers.length; i++) {
            var p = providers[i];
            var models = modelsCache[String(p.id)];
            html += '<div class="ai-model-group">' +
                '<div class="ai-model-group-header">' +
                '  <span>' + esc(p.label || 'Provider') + '</span>' +
                (models
                    ? '<span>' + models.length + ' models</span>'
                    : '<button type="button" class="btn btn-small btn-secondary" data-modal-action="fetch" data-id="' + esc(p.id) + '">Fetch models</button>') +
                '</div>';

            if (models && models.length > 0) {
                for (var j = 0; j < models.length; j++) {
                    var m = models[j] || {};
                    if (editingLimits &&
                        String(editingLimits.providerId) === String(p.id) &&
                        editingLimits.modelId === (m.model_id || '')) {
                        html += renderLimitsForm(m, p.id);
                        continue;
                    }
                    var manual = m.limits_source === 'manual';
                    var manualTitle = manual ? ' title="manually set"' : '';
                    var ctx = m.context_tokens ? formatTokens(m.context_tokens) + ' ctx' : '';
                    var out = m.max_output_tokens ? formatTokens(m.max_output_tokens) + ' out' : '';
                    var supportsVision = !!m.supports_vision;
                    html += '<div class="ai-model-row" role="button" tabindex="0" data-modal-action="choose"' +
                        ' data-provider-id="' + esc(p.id) + '" data-model-id="' + esc(m.model_id || '') + '">' +
                        '  <span class="ai-model-name">' + esc(m.display_name || m.model_id || 'Model') + '</span>' +
                        '  <span class="ai-model-id">' + esc(m.model_id || '') + '</span>' +
                        (supportsVision ? '<span class="ai-model-badge vision">vision</span>' : '') +
                        (m.supports_tools ? '<span class="ai-model-badge tools">tools</span>' : '') +
                        (ctx ? '<span class="ai-model-ctx"' + manualTitle + '>' + esc(ctx) + '</span>' : '') +
                        (out ? '<span class="ai-model-ctx"' + manualTitle + '>' + esc(out) + '</span>' : '') +
                        (supportsVision
                            ? ''
                            : '<svg class="ai-model-warn" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="15" height="15" title="may not support images"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>') +
                        '<button type="button" class="ai-model-edit" data-modal-action="edit-limits"' +
                        ' data-provider-id="' + esc(p.id) + '" data-model-id="' + esc(m.model_id || '') + '"' +
                        ' title="Edit context / output limits" aria-label="Edit limits">' + PENCIL_SVG + '</button>' +
                        '</div>';
                }
            } else {
                html += '<p class="ai-model-empty">No models cached for this provider yet' +
                    (models ? '' : ' \u2014 use "Fetch models" above') + '</p>';
            }
            html += '</div>';
        }

        // Manual model entry (validated server-side against the catalogue).
        var providerOptions = '';
        for (var k = 0; k < providers.length; k++) {
            providerOptions += '<option value="' + esc(providers[k].id) + '">' + esc(providers[k].label || providers[k].id) + '</option>';
        }
        html += '<div class="ai-manual-form">' +
            '<h3>Add manually</h3>' +
            '<div class="form-stack">' +
            '  <select id="ai-manual-provider" aria-label="Provider">' + providerOptions + '</select>' +
            '  <input type="text" id="ai-manual-model-id" placeholder="Model ID (e.g., gpt-4o)" aria-label="Model ID">' +
            '  <input type="text" id="ai-manual-display-name" placeholder="Display name (optional)" aria-label="Display name">' +
            '  <button type="button" class="btn btn-small" data-modal-action="manual-save">Use model</button>' +
            '</div>' +
            '</div>';

        body.innerHTML = html;
    }

    async function chooseModel(providerId, modelId) {
        if (!providerId || !modelId) return;
        try {
            var resp = await csrfFetch(BASE_URL + '/api/user/ai/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    active_provider_id: Number(providerId),
                    active_model_id: modelId,
                }),
            });
            if (!resp.ok) {
                var detail = await errorDetail(resp, 'Failed to set default model');
                toast(detail, true);
                return;
            }
            var data = await resp.json();
            settings = data && data.settings ? data.settings : settings;
            toast('Default model updated', false);
            renderActiveModel();
            closeModelModal();
        } catch (err) {
            console.error('[settings-ai] Failed to set default model:', err);
            toast('Failed to set default model', true);
        }
    }

    async function saveManualModel() {
        var providerId = getEl('ai-manual-provider') ? getEl('ai-manual-provider').value : '';
        var modelId = getEl('ai-manual-model-id') ? getEl('ai-manual-model-id').value.trim() : '';
        var displayName = getEl('ai-manual-display-name') ? getEl('ai-manual-display-name').value.trim() : '';
        if (!modelId) {
            toast('Model ID is required', true);
            return;
        }
        // The backend only accepts models present in the provider catalogue;
        // keep the typed display name for the toast, the API returns the
        // enriched settings itself.
        if (displayName) {
            toast('Using model "' + displayName + '" (' + modelId + ')', false);
        }
        await chooseModel(providerId, modelId);
    }

    // ========================================================================
    // Manual context/output limits
    // ========================================================================

    async function saveModelLimits(providerId, modelId) {
        var form = getEl('ai-model-modal-body').querySelector(
            '.ai-limits-form[data-provider-id="' + CSS.escape(String(providerId)) + '"]' +
            '[data-model-id="' + CSS.escape(modelId) + '"]'
        );
        if (!form) return;

        function readLimit(name) {
            var raw = form.querySelector('[data-limit-field="' + name + '"]').value.trim();
            if (!raw) return null; // empty = unknown / provider-reported
            var n = Number(raw);
            return (isFinite(n) && n >= 1) ? Math.round(n) : NaN;
        }
        var contextTokens = readLimit('context_tokens');
        var maxOutputTokens = readLimit('max_output_tokens');
        if (Number.isNaN(contextTokens) || Number.isNaN(maxOutputTokens)) {
            toast('Limits must be positive integers', true);
            return;
        }

        try {
            var resp = await csrfFetch(
                BASE_URL + '/api/user/ai/providers/' + encodeURIComponent(providerId) +
                '/models/' + encodeURIComponent(modelId) + '/limits',
                {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        context_tokens: contextTokens,
                        max_output_tokens: maxOutputTokens,
                    }),
                }
            );
            if (!resp.ok) {
                var detail = await errorDetail(resp, 'Failed to save limits');
                toast(detail, true);
                warnEncryptionKey(detail);
                return;
            }
            var data = await resp.json();
            var models = modelsCache[String(providerId)] || [];
            for (var i = 0; i < models.length; i++) {
                if (models[i].model_id === modelId) {
                    models[i].context_tokens = data.model.context_tokens;
                    models[i].max_output_tokens = data.model.max_output_tokens;
                    models[i].limits_source = data.model.limits_source;
                }
            }
            editingLimits = null;
            toast('Model limits updated', false);
            renderModelModal();
        } catch (err) {
            console.error('[settings-ai] Failed to save model limits:', err);
            toast('Failed to save limits', true);
        }
    }

    // ========================================================================
    // Wiring
    // ========================================================================

    function init() {
        if (!getEl('ai-providers-list')) return; // not on the settings page

        getEl('ai-providers-list').addEventListener('click', function (e) {
            var btn = e.target.closest('[data-action]');
            if (!btn) return;
            var id = btn.dataset.id;
            switch (btn.dataset.action) {
                case 'fetch-models':
                    fetchModels(id);
                    break;
                case 'edit':
                    editingProviderId = id;
                    renderProviders();
                    break;
                case 'delete':
                    deleteProvider(id);
                    break;
            }
        });

        getEl('ai-add-provider-btn').addEventListener('click', function () {
            if (editingProviderId === 'new') return; // form already open
            editingProviderId = 'new';
            renderProviders(); // renders the create form as the first card
        });

        getEl('ai-choose-model-btn').addEventListener('click', openModelModal);
        getEl('ai-model-modal-close').addEventListener('click', closeModelModal);
        getEl('ai-model-modal').addEventListener('click', function (e) {
            if (e.target === getEl('ai-model-modal')) closeModelModal();
        });

        getEl('ai-model-modal-body').addEventListener('click', function (e) {
            var btn = e.target.closest('[data-modal-action]');
            if (!btn) return;
            switch (btn.dataset.modalAction) {
                case 'fetch':
                    fetchModels(btn.dataset.id).then(renderModelModal);
                    break;
                case 'choose':
                    chooseModel(btn.dataset.providerId, btn.dataset.modelId);
                    break;
                case 'manual-save':
                    saveManualModel();
                    break;
                case 'edit-limits':
                    e.stopPropagation();
                    editingLimits = {
                        providerId: btn.dataset.providerId,
                        modelId: btn.dataset.modelId,
                    };
                    renderModelModal();
                    break;
                case 'limits-save':
                    saveModelLimits(btn.dataset.providerId, btn.dataset.modelId);
                    break;
                case 'limits-cancel':
                    editingLimits = null;
                    renderModelModal();
                    break;
            }
        });

        // Model rows are div[role=button]; keep Enter/Space selecting.
        getEl('ai-model-modal-body').addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            var row = e.target.closest('.ai-model-row[role="button"]');
            if (!row || e.target !== row) return;
            e.preventDefault();
            chooseModel(row.dataset.providerId, row.dataset.modelId);
        });

        loadProviders();
        loadSettings();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
