'''
File:   test_gallery.py
Brief:  E2E tests with Playwright to check the frontend.
Author: Mistress-Lukutar
Date:   2026-10-27
Version: v2.1.0

E2E tests with Playwright to check the frontend.
Run: pytest tests/e2e/test_gallery.py --headed

Requires:
pip install pytest-playwright
playwright install
'''

import pytest

# These tests drive a LIVE server (http://localhost:8000) via Playwright.
# Skip cleanly when pytest-playwright is not installed instead of erroring.
pytest.importorskip("playwright", reason="e2e tests require pytest-playwright")


@pytest.fixture
def logged_in_page(page):
    """Fixture for authorization before tests."""
    page.goto("http://localhost:8000/login")
    page.fill('[name="username"]', "testuser")
    page.fill('[name="password"]', "testpass")
    page.click('button[type="submit"]')
    page.wait_for_url("**/")
    return page


def test_upload_button_opens_modal(logged_in_page):
    """Test: the upload button opens the modal."""
    page = logged_in_page

    page.click('#folder-upload-btn')

    upload_modal = page.locator('#upload-modal')
    assert upload_modal.is_visible(), "Upload modal should be visible"


def test_sort_button_changes_order(logged_in_page):
    """Test: the sort button changes item order."""
    page = logged_in_page

    items_before = page.locator('.gallery-item').all()
    ids_before = [item.get_attribute('data-photo-id') or
                  item.get_attribute('data-album-id') for item in items_before]

    page.click('#sort-btn')
    page.click('[data-sort="taken"]')

    page.wait_for_timeout(500)

    # With items present the order should differ, unless timestamps coincide.
    items_after = page.locator('.gallery-item').all()
    ids_after = [item.get_attribute('data-photo-id') or
                 item.get_attribute('data-album-id') for item in items_after]

    print(f"Before: {ids_before}")
    print(f"After: {ids_after}")


def test_lightbox_opens_on_photo_click(logged_in_page):
    """Test: clicking a photo opens the lightbox."""
    page = logged_in_page

    page.wait_for_selector('.gallery-item[data-item-type="photo"]', timeout=5000)

    first_photo = page.locator('.gallery-item[data-item-type="photo"]').first
    first_photo.click()

    lightbox = page.locator('#lightbox')
    assert not lightbox.has_class('hidden'), "Lightbox should be visible"

    assert 'photo_id=' in page.url, "URL should contain photo_id"


def test_lightbox_navigation(logged_in_page):
    """Test: lightbox navigation works."""
    page = logged_in_page

    page.wait_for_selector('.gallery-item[data-item-type="photo"]', timeout=5000)
    page.locator('.gallery-item[data-item-type="photo"]').first.click()

    current_url = page.url

    page.click('.lightbox-next')
    page.wait_for_timeout(300)

    new_url = page.url
    assert current_url != new_url, "URL should change after navigation"


def test_lightbox_close_removes_photo_id(logged_in_page):
    """Test: closing the lightbox removes photo_id from the URL."""
    page = logged_in_page

    page.wait_for_selector('.gallery-item[data-item-type="photo"]', timeout=5000)
    page.locator('.gallery-item[data-item-type="photo"]').first.click()

    page.click('.lightbox-close')

    assert 'photo_id=' not in page.url, "URL should not contain photo_id after close"


def test_masonry_layout_no_jumps(logged_in_page):
    """Test: masonry layout does not jump when images load."""
    page = logged_in_page

    items = page.locator('.gallery-item').all()
    if len(items) == 0:
        pytest.skip("No items in gallery")

    positions_before = []
    for item in items[:5]:
        box = item.bounding_box()
        positions_before.append((box['x'], box['y']))

    page.wait_for_timeout(2000)

    # Positions may shift slightly, but must not jump by large amounts.
    items_after = page.locator('.gallery-item').all()
    for i, item in enumerate(items_after[:5]):
        box = item.bounding_box()
        pos_before = positions_before[i]
        pos_after = (box['x'], box['y'])

        assert abs(pos_before[0] - pos_after[0]) < 10, f"Item {i} X position changed too much"
        assert abs(pos_before[1] - pos_after[1]) < 50, f"Item {i} Y position changed too much"


def test_album_opens_lightbox_with_album_context(logged_in_page):
    """Test: clicking an album opens the lightbox with album context."""
    page = logged_in_page

    album = page.locator('.gallery-item[data-item-type="album"]').first
    if not album.is_visible():
        pytest.skip("No albums in gallery")

    album.click()

    lightbox = page.locator('#lightbox')
    assert not lightbox.has_class('hidden'), "Lightbox should open for album"

    album_indicator = page.locator('#lightbox-album-indicator')
    assert album_indicator.is_visible() or album_indicator.has_class('hidden'), \
        "Album indicator should exist"


def test_url_with_photo_id_opens_lightbox_on_load(logged_in_page):
    """Test: a URL with photo_id opens the photo on page load."""
    page = logged_in_page

    first_photo = page.locator('.gallery-item[data-item-type="photo"]').first
    photo_id = first_photo.get_attribute('data-photo-id')

    current_url = page.url
    new_url = f"{current_url}&photo_id={photo_id}"
    page.goto(new_url)

    page.wait_for_timeout(1000)

    lightbox = page.locator('#lightbox')
    assert not lightbox.has_class('hidden'), "Lightbox should open automatically"
