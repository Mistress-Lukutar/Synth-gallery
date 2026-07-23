'''
File:   item_renderers.py
Brief:  Item renderers - Strategy pattern for per-type presentation.
Author: Mistress-Lukutar
Date:   2026-07-23
Version: v1.1.0

Each polymorphic item type (``media``, and future ``note`` / ``audio`` /
``model``) implements an :class:`ItemRenderer` that knows how to produce
the URLs and metadata the gallery / lightbox UIs need. Renderers are
registered in :mod:`app.application.services.item_types` so the dispatch
is driven from a single source of truth.
'''
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict


class ItemRenderer(ABC):
    '''Abstract base for item type renderers.

    Strategy Pattern: each item type implements its own rendering logic.
    '''

    @abstractmethod
    def get_thumbnail_url(self, item: Dict) -> str:
        '''Get URL for item thumbnail.'''

    @abstractmethod
    def get_full_url(self, item: Dict) -> str:
        '''Get URL for full item view.'''

    @abstractmethod
    def get_dimensions(self, item: Dict) -> tuple:
        '''Get display dimensions (width, height).'''

    @abstractmethod
    def render_gallery_item(self, item: Dict) -> Dict:
        '''Render metadata for the gallery grid.'''

    @abstractmethod
    def render_lightbox(self, item: Dict) -> Dict:
        '''Render metadata for the lightbox view.'''


class MediaRenderer(ItemRenderer):
    '''Renderer for photos and videos.'''

    def get_thumbnail_url(self, item: Dict) -> str:
        from app.config import BASE_URL
        return f'{BASE_URL}/files/{item["id"]}/thumbnail'

    def get_full_url(self, item: Dict) -> str:
        from app.config import BASE_URL
        return f'{BASE_URL}/files/{item["id"]}'

    def get_dimensions(self, item: Dict) -> tuple:
        thumb_w = item.get('thumb_width', 280)
        thumb_h = item.get('thumb_height', 210)
        return thumb_w, thumb_h

    def render_gallery_item(self, item: Dict) -> Dict:
        '''Render gallery-grid metadata.

        Contract keys (consumed by ``gallery/main.py`` folder content API):
        - ``type``: polymorphic item type (``'media'``).
        - ``media_type``: ``'image'`` | ``'video'``.
        - ``width`` / ``height``: thumbnail display dimensions.
        - ``has_thumbnail``: whether a thumbnail is available for this item.
        - ``thumbnail_url``: URL of the thumbnail endpoint.

        The frontend currently derives thumbnail URLs itself from the item
        id, but the contract is published so future renderers/consumers agree
        on the shape.
        '''
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'width': item.get('thumb_width', 280),
            'height': item.get('thumb_height', 210),
            'has_thumbnail': True,
            'thumbnail_url': self.get_thumbnail_url(item),
        }

    def render_lightbox(self, item: Dict) -> Dict:
        return {
            'type': 'media',
            'media_type': item.get('media_type', 'image'),
            'url': self.get_full_url(item),
            'title': item.get('title', ''),
        }
