"""AI provider service - per-user LLM provider configuration.

Business logic for managing AI chat providers (API keys encrypted with the
owner's DEK), the per-provider model catalogue and the per-user active
endpoint selection consumed by the chat orchestrator.

The LLM client package (``app.infrastructure.services.llm``) is imported
lazily inside :meth:`AiProviderService._llm` so this module stays importable
independent of that package's lifecycle.
"""
from typing import Optional

from fastapi import HTTPException

from ...infrastructure.services.encryption import EncryptionError, EncryptionService


class AiProviderService:
    """Service for AI provider/model/settings management."""

    VALID_PROTOCOLS = ("openai_compatible", "anthropic", "google_gemini")

    # Sanity cap for manually entered token limits: accepts 1M-class
    # (and larger) context windows but rejects obvious typos.
    MAX_MANUAL_TOKEN_LIMIT = 10_000_000

    def __init__(self, provider_repo, model_repo, settings_repo):
        """Create the service with its repositories.

        Args:
            provider_repo: AiProviderRepository instance.
            model_repo: AiModelRepository instance.
            settings_repo: AiChatSettingsRepository instance.
        """
        self.provider_repo = provider_repo
        self.model_repo = model_repo
        self.settings_repo = settings_repo

    # =====================================================================
    # LLM client access (lazy - the client package is built separately)
    # =====================================================================

    @staticmethod
    def _llm():
        """Lazily import the LLM client package.

        Returns:
            Tuple ``(AiProtocol, get_llm_client, LLMError)`` from
            ``app.infrastructure.services.llm``.

        Raises:
            ImportError: If the LLM client package is not available.
        """
        from app.infrastructure.services.llm import AiProtocol, LLMError, get_llm_client

        return AiProtocol, get_llm_client, LLMError

    # =====================================================================
    # Providers
    # =====================================================================

    def list_providers(self, user_id: int) -> list[dict]:
        """List the user's providers with masked keys and model counts.

        Args:
            user_id: Owner's user ID.

        Returns:
            List of provider view dicts. The encrypted key blob is never
            included; ``api_key_masked`` is ``"•••"`` when a key is stored
            and ``None`` otherwise.
        """
        providers = []
        for row in self.provider_repo.list_for_user(user_id):
            providers.append(
                self._provider_view(
                    row,
                    masked="•••" if row["api_key_encrypted"] else None,
                    model_count=self.provider_repo.count_models(row["id"]),
                )
            )
        return providers

    def create_provider(
        self,
        user_id: int,
        label: str,
        protocol: str,
        base_url: str,
        api_key: str,
        dek: bytes,
    ) -> dict:
        """Create a provider with a DEK-encrypted API key.

        Args:
            user_id: Owner's user ID.
            label: Display label.
            protocol: One of :data:`VALID_PROTOCOLS`.
            base_url: API base URL.
            api_key: Plaintext API key (encrypted before storage).
            dek: Owner's Data Encryption Key.

        Returns:
            Provider view dict with ``api_key_masked``.

        Raises:
            HTTPException: 400 on invalid protocol or empty fields.
        """
        self._validate_provider_fields(label, protocol, base_url, api_key)

        encrypted = EncryptionService.encrypt_bytes(api_key.encode("utf-8"), dek)
        provider_id = self.provider_repo.create(
            user_id=user_id,
            label=label.strip(),
            protocol=protocol,
            base_url=base_url.strip(),
            api_key_encrypted=encrypted,
        )
        row = self.provider_repo.get_by_id(provider_id, user_id)
        return self._provider_view(
            row, masked=self.mask_key(api_key), model_count=0
        )

    def update_provider(
        self,
        user_id: int,
        provider_id: int,
        *,
        label: Optional[str] = None,
        protocol: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        dek: bytes,
    ) -> dict:
        """Update a provider. ``api_key`` ``None``/"" keeps the stored key.

        Args:
            user_id: Owner's user ID.
            provider_id: Provider ID.
            label: New label, or None to keep.
            protocol: New protocol, or None to keep.
            base_url: New base URL, or None to keep.
            api_key: New plaintext API key; None or "" keeps the existing key.
            dek: Owner's Data Encryption Key.

        Returns:
            Provider view dict.

        Raises:
            HTTPException: 404 when the provider does not exist; 400 on
                invalid protocol or empty field values.
        """
        row = self.provider_repo.get_by_id(provider_id, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Provider not found")

        if label is None and protocol is None and base_url is None and api_key is None:
            return self._provider_view(
                row,
                masked="•••" if row["api_key_encrypted"] else None,
                model_count=self.provider_repo.count_models(provider_id),
            )

        self._validate_provider_fields(
            label if label is not None else row["label"],
            protocol if protocol is not None else row["protocol"],
            base_url if base_url is not None else row["base_url"],
            api_key if api_key else "placeholder",
        )

        fields: dict = {}
        masked: Optional[str]
        if label is not None:
            fields["label"] = label.strip()
        if protocol is not None:
            fields["protocol"] = protocol
        if base_url is not None:
            fields["base_url"] = base_url.strip()
        if api_key:  # None or "" keep the existing key
            fields["api_key_encrypted"] = EncryptionService.encrypt_bytes(
                api_key.encode("utf-8"), dek
            )
            masked = self.mask_key(api_key)
        else:
            masked = self._mask_stored(row, dek)

        if fields:
            self.provider_repo.update(provider_id, user_id, fields)
        row = self.provider_repo.get_by_id(provider_id, user_id)
        return self._provider_view(
            row,
            masked=masked,
            model_count=self.provider_repo.count_models(provider_id),
        )

    def delete_provider(self, user_id: int, provider_id: int) -> bool:
        """Delete a provider, its cached models and any active selection.

        Deletion is explicit at every level - SQLite FK cascades are not
        relied upon.

        Args:
            user_id: Owner's user ID.
            provider_id: Provider ID.

        Returns:
            True if the provider existed and was deleted.
        """
        if self.provider_repo.get_by_id(provider_id, user_id) is None:
            return False
        # Children first: models, then any dangling active selection.
        self.model_repo.replace_for_provider(provider_id, [])
        self.settings_repo.clear_active_provider(user_id, provider_id)
        return self.provider_repo.delete(provider_id, user_id)

    # =====================================================================
    # Model catalogue
    # =====================================================================

    async def fetch_models(
        self, user_id: int, provider_id: int, dek: bytes
    ) -> list[dict]:
        """Fetch the model catalogue from the provider API and merge it.

        The fresh catalogue replaces the stored one, except that per-model
        ``is_pinned`` flags are preserved and manually maintained limits
        (``limits_source == 'manual'``) survive when the fresh entry has no
        limit data.

        Args:
            user_id: Owner's user ID.
            provider_id: Provider ID.
            dek: Owner's Data Encryption Key.

        Returns:
            The merged model dicts (also persisted).

        Raises:
            HTTPException: 404 when the provider does not exist; 403 when
                the stored key cannot be decrypted; 503 when the LLM client
                package is unavailable; 502 when the provider call fails.
        """
        provider = self.provider_repo.get_by_id(provider_id, user_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")

        api_key = self._decrypt_key(provider, dek)

        try:
            ai_protocol_cls, get_llm_client, llm_error_cls = self._llm()
        except ImportError:
            raise HTTPException(
                status_code=503, detail="AI client is not available"
            )

        try:
            protocol = ai_protocol_cls(provider["protocol"])
        except (ValueError, KeyError):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown protocol: {provider['protocol']}",
            )

        try:
            fetched = await get_llm_client(protocol).fetch_models(
                provider["base_url"], api_key
            )
        except llm_error_cls as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        merged = self._merge_models(
            self.model_repo.list_for_provider(provider_id),
            fetched,
        )
        self.model_repo.replace_for_provider(provider_id, merged)
        return merged

    def update_model_limits(
        self,
        user_id: int,
        provider_id: int,
        model_id: str,
        context_tokens: Optional[int],
        max_output_tokens: Optional[int],
    ) -> dict:
        """Manually set one model's context/output limits.

        Values are stored with ``limits_source = 'manual'`` so catalogue
        refreshes never overwrite them (see :meth:`_merge_models`).
        Clearing both values returns the model to provider-reported (or
        unknown) limits.

        Args:
            user_id: Owner's user ID.
            provider_id: Provider ID.
            model_id: Provider-specific model identifier.
            context_tokens: Context window in tokens, or None to clear.
            max_output_tokens: Max output tokens, or None to clear.

        Returns:
            The updated model dict.

        Raises:
            HTTPException: 404 when the provider or model does not exist;
                400 when a value is out of range.
        """
        provider = self.provider_repo.get_by_id(provider_id, user_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        if self.model_repo.get(provider_id, model_id) is None:
            raise HTTPException(status_code=404, detail="Model not found")

        for value in (context_tokens, max_output_tokens):
            if value is not None and not (
                1 <= value <= self.MAX_MANUAL_TOKEN_LIMIT
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Token limits must be between 1 and "
                        f"{self.MAX_MANUAL_TOKEN_LIMIT}"
                    ),
                )

        limits_source = (
            "manual"
            if context_tokens is not None or max_output_tokens is not None
            else None
        )
        self.model_repo.update_limits(
            provider_id,
            model_id,
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
            limits_source=limits_source,
        )
        return self.model_repo.get(provider_id, model_id)

    # =====================================================================
    # Settings / active endpoint
    # =====================================================================

    def get_settings(self, user_id: int) -> dict:
        """Return the user's chat settings, enriched with provider/model info.

        Args:
            user_id: Owner's user ID.

        Returns:
            Settings dict plus ``provider_label``, ``protocol``,
            ``model_display_name``, ``supports_vision``,
            ``supports_tools`` and the active model's ``context_tokens``
            / ``max_output_tokens`` (None when the selection is unset or
            dangling).
        """
        settings = dict(self.settings_repo.ensure(user_id))

        provider_label = None
        protocol = None
        model_display_name = None
        supports_vision = None
        supports_tools = None
        context_tokens = None
        max_output_tokens = None

        provider_id = settings.get("active_provider_id")
        model_id = settings.get("active_model_id")
        if provider_id is not None:
            provider = self.provider_repo.get_by_id(provider_id, user_id)
            if provider is not None:
                provider_label = provider["label"]
                protocol = provider["protocol"]
                if model_id is not None:
                    model = self.model_repo.get(provider_id, model_id)
                    if model is not None:
                        model_display_name = model["display_name"]
                        supports_vision = bool(model["supports_vision"])
                        supports_tools = bool(model["supports_tools"])
                        context_tokens = model["context_tokens"]
                        max_output_tokens = model["max_output_tokens"]

        settings["provider_label"] = provider_label
        settings["protocol"] = protocol
        settings["model_display_name"] = model_display_name
        settings["supports_vision"] = supports_vision
        settings["supports_tools"] = supports_tools
        settings["context_tokens"] = context_tokens
        settings["max_output_tokens"] = max_output_tokens
        return settings

    def update_settings(
        self,
        user_id: int,
        active_provider_id: Optional[int],
        active_model_id: Optional[str],
    ) -> dict:
        """Set the user's active provider/model pair.

        Args:
            user_id: Owner's user ID.
            active_provider_id: Provider ID or None to clear.
            active_model_id: Model identifier or None to clear.

        Returns:
            The enriched settings dict (see :meth:`get_settings`).

        Raises:
            HTTPException: 400 when the pair is inconsistent, the provider
                is unknown or the model is not in the provider.
        """
        if active_provider_id is None:
            if active_model_id is not None:
                raise HTTPException(
                    status_code=400,
                    detail="active_model_id cannot be set without a provider",
                )
        else:
            provider = self.provider_repo.get_by_id(
                active_provider_id, user_id
            )
            if provider is None:
                raise HTTPException(
                    status_code=400, detail="Unknown provider"
                )
            if active_model_id is not None:
                if self.model_repo.get(active_provider_id, active_model_id) is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Model is not available for this provider",
                    )

        self.settings_repo.ensure(user_id)
        self.settings_repo.update(
            user_id,
            {
                "active_provider_id": active_provider_id,
                "active_model_id": active_model_id,
            },
        )
        return self.get_settings(user_id)

    def get_active_endpoint(self, user_id: int, dek: bytes) -> Optional[dict]:
        """Return the active LLM endpoint for the chat orchestrator.

        Args:
            user_id: Owner's user ID.
            dek: Owner's Data Encryption Key.

        Returns:
            Dict with ``base_url``, ``api_key`` (decrypted plaintext),
            ``model_id``, ``protocol``, ``temperature`` and the model's
            ``max_output_tokens`` (None when unset or the selection is
            dangling), or None when no provider/model is configured.

        Raises:
            HTTPException: 403 when the stored key cannot be decrypted.
        """
        settings = self.settings_repo.ensure(user_id)
        provider_id = settings["active_provider_id"]
        model_id = settings["active_model_id"]
        if provider_id is None or model_id is None:
            return None

        provider = self.provider_repo.get_by_id(provider_id, user_id)
        if provider is None:
            return None

        model = self.model_repo.get(provider_id, model_id)
        return {
            "base_url": provider["base_url"],
            "api_key": self._decrypt_key(provider, dek),
            "model_id": model_id,
            "protocol": provider["protocol"],
            "temperature": settings["temperature"],
            "max_output_tokens": (
                model["max_output_tokens"] if model is not None else None
            ),
        }

    # =====================================================================
    # Helpers
    # =====================================================================

    @staticmethod
    def mask_key(plain: str) -> str:
        """Mask an API key for display.

        Args:
            plain: Plaintext API key.

        Returns:
            First 3 characters + "…" + last 4 characters for keys longer
            than 9 characters, otherwise "•••".
        """
        if len(plain) > 9:
            return f"{plain[:3]}…{plain[-4:]}"
        return "•••"

    def _validate_provider_fields(
        self,
        label: str,
        protocol: str,
        base_url: str,
        api_key: str,
    ) -> None:
        """Validate provider fields, raising 400 on bad input."""
        if not label or not str(label).strip():
            raise HTTPException(status_code=400, detail="Label is required")
        if not base_url or not str(base_url).strip():
            raise HTTPException(status_code=400, detail="Base URL is required")
        if not api_key:
            raise HTTPException(status_code=400, detail="API key is required")
        if protocol not in self.VALID_PROTOCOLS:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Protocol must be one of: "
                    + ", ".join(self.VALID_PROTOCOLS)
                ),
            )

    def _decrypt_key(self, provider: dict, dek: bytes) -> str:
        """Decrypt a provider's stored API key.

        Raises:
            HTTPException: 400 when no key is stored; 403 when decryption
                fails (wrong DEK / corrupted blob).
        """
        blob = provider["api_key_encrypted"]
        if not blob:
            raise HTTPException(
                status_code=400, detail="Provider has no stored API key"
            )
        try:
            return EncryptionService.decrypt_bytes(blob, dek).decode("utf-8")
        except EncryptionError:
            raise HTTPException(
                status_code=403, detail="unable to decrypt key"
            )

    def _mask_stored(self, row: dict, dek: bytes) -> Optional[str]:
        """Mask a provider's stored API key for display.

        Decrypts the stored blob so the masked value shows the same
        prefix/suffix as after a fresh create. Returns None when no key
        is stored.

        Raises:
            HTTPException: 403 when decryption fails (see
                :meth:`_decrypt_key`).
        """
        if not row["api_key_encrypted"]:
            return None
        return self.mask_key(self._decrypt_key(row, dek))

    def _provider_view(
        self,
        row: dict,
        masked: Optional[str],
        model_count: Optional[int] = None,
    ) -> dict:
        """Build the API view of a provider row (never the raw blob)."""
        view = {
            "id": row["id"],
            "label": row["label"],
            "protocol": row["protocol"],
            "base_url": row["base_url"],
            "created_at": row["created_at"],
            "has_api_key": row["api_key_encrypted"] is not None,
            "api_key_masked": masked,
        }
        if model_count is not None:
            view["model_count"] = model_count
        return view

    @staticmethod
    def _entry_field(entry, key: str, default=None):
        """Read a field from a fetched entry (dict or attribute object)."""
        if isinstance(entry, dict):
            return entry.get(key, default)
        return getattr(entry, key, default)

    def _coerce_fetched(self, entry) -> Optional[dict]:
        """Normalise one freshly fetched model entry into a storage dict.

        Returns None for entries without a model identifier.
        """
        model_id = self._entry_field(entry, "model_id") or self._entry_field(
            entry, "id"
        )
        if not model_id:
            return None
        display_name = self._entry_field(entry, "display_name") or model_id
        return {
            "model_id": str(model_id),
            "display_name": str(display_name),
            "supports_tools": 1 if self._entry_field(entry, "supports_tools") else 0,
            "supports_vision": 1 if self._entry_field(entry, "supports_vision") else 0,
            "is_pinned": 0,
            "context_tokens": self._entry_field(entry, "context_tokens"),
            "max_output_tokens": self._entry_field(entry, "max_output_tokens"),
            "limits_source": self._entry_field(entry, "limits_source"),
        }

    def _merge_models(self, existing: list[dict], fetched: list) -> list[dict]:
        """Merge a fresh catalogue with stored per-user adjustments.

        - ``is_pinned`` is preserved for known models (new entries default
          to 0).
        - Manually maintained limits (``limits_source == 'manual'``)
          always win: provider-reported values (or their absence) never
          overwrite explicit user input.
        """
        by_model_id = {row["model_id"]: row for row in existing}

        merged: list[dict] = []
        for entry in fetched:
            fresh = self._coerce_fetched(entry)
            if fresh is None:
                continue
            old = by_model_id.get(fresh["model_id"])
            if old is not None:
                fresh["is_pinned"] = 1 if old["is_pinned"] else 0
                if old.get("limits_source") == "manual":
                    fresh["context_tokens"] = old["context_tokens"]
                    fresh["max_output_tokens"] = old["max_output_tokens"]
                    fresh["limits_source"] = old["limits_source"]
            merged.append(fresh)
        return merged
