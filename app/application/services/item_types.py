'''
File:   item_types.py
Brief:  Typed item-type registry - single source of truth for polymorphic items.
Author: Mistress-Lukutar
Date:   2026-07-23
Version: v1.1.0

Each polymorphic ``items.type`` (``media`` today; ``note`` / ``audio`` /
``model`` in the future) is described by an :class:`ItemTypeSpec` that
binds together:

- the detail table name (``item_media``),
- the renderer factory (:class:`MediaRenderer`),
- the allowed MIME set for uploads of this type.

Dispatch sites (file serving, upload validation, ``ItemService``
hydration, renderer lookup) consult this registry via the helpers below
instead of hard-coding the string ``'media'``, so adding a new type
becomes a one-file change here plus a detail-table/repo/renderer.
'''
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from app.config import ALLOWED_MEDIA_TYPES, ALLOWED_NOTE_TYPES

from .item_renderers import ItemRenderer, MediaRenderer, NoteRenderer


class ItemType(str, enum.Enum):
    '''Polymorphic item types stored in ``items.type``.

    Sub-kinds within media (image vs video) live in ``item_media.media_type``,
    NOT here: a 3D model is not "a kind of photo", but a video *is* "a kind
    of media".
    '''

    MEDIA = 'media'
    NOTE = 'note'        # text files: item_texts detail table
    # AUDIO = 'audio'    # future: item_audio detail table
    # MODEL = 'model'    # future: item_models detail table


@dataclass(frozen=True)
class ItemTypeSpec:
    '''Descriptor for a polymorphic item type.'''

    value: str
    detail_table: Optional[str]
    renderer_factory: Callable[[], ItemRenderer]
    allowed_mime: Optional[frozenset[str]]


ITEM_TYPE_REGISTRY: Dict[str, ItemTypeSpec] = {
    ItemType.MEDIA.value: ItemTypeSpec(
        value=ItemType.MEDIA.value,
        detail_table='item_media',
        renderer_factory=MediaRenderer,
        allowed_mime=frozenset(ALLOWED_MEDIA_TYPES),
    ),
    ItemType.NOTE.value: ItemTypeSpec(
        value=ItemType.NOTE.value,
        detail_table='item_texts',
        renderer_factory=NoteRenderer,
        allowed_mime=frozenset(ALLOWED_NOTE_TYPES),
    ),
}


def get_item_type_spec(item_type: str) -> Optional[ItemTypeSpec]:
    '''Return the spec for ``item_type`` or ``None`` if unknown.'''
    return ITEM_TYPE_REGISTRY.get(item_type)


def resolve_item_type_for_content(content_type: str) -> Optional[str]:
    '''Return the item type whose allowed MIME set contains ``content_type``.

    Registry order decides precedence when MIME sets ever overlap; today
    media (image/*, video/*) and note (text formats) sets are disjoint.
    '''
    for spec in ITEM_TYPE_REGISTRY.values():
        if spec.allowed_mime and content_type in spec.allowed_mime:
            return spec.value
    return None


def require_item_type_spec(item_type: str) -> ItemTypeSpec:
    '''Return the spec for ``item_type``, raising ``ValueError`` if unknown.'''
    spec = ITEM_TYPE_REGISTRY.get(item_type)
    if spec is None:
        raise ValueError(f'Unknown item type: {item_type!r}')
    return spec


def is_known_item_type(item_type: str) -> bool:
    '''Return True if ``item_type`` is a registered item type.'''
    return item_type in ITEM_TYPE_REGISTRY


def allowed_mime_for(item_type: str) -> Optional[frozenset[str]]:
    '''Return the allowed MIME set for ``item_type`` or ``None``.'''
    spec = ITEM_TYPE_REGISTRY.get(item_type)
    return spec.allowed_mime if spec else None


def get_renderer_for(item_type: str) -> ItemRenderer:
    '''Return a renderer instance for ``item_type``.

    Raises ``ValueError`` for unknown types, mirroring the previous
    ``ItemService.get_renderer`` behaviour.
    '''
    return require_item_type_spec(item_type).renderer_factory()
