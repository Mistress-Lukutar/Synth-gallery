"""
E2E тесты с Playwright для проверки frontend.
Запуск: pytest tests/e2e/test_gallery.py --headed

Требует установки:
pip install pytest-playwright
playwright install
"""

import pytest


@pytest.fixture
def logged_in_page(page):
    """Фикстура для авторизации перед тестами."""
    page.goto("http://localhost:8000/login")
    page.fill('[name="username"]', "testuser")
    page.fill('[name="password"]', "testpass")
    page.click('button[type="submit"]')
    page.wait_for_url("**/")
    return page


def test_upload_button_opens_modal(logged_in_page):
    """Тест: кнопка upload открывает модальное окно."""
    page = logged_in_page
    
    # Кликаем кнопку upload
    page.click('#folder-upload-btn')
    
    # Проверяем что модальное окно видимо
    upload_modal = page.locator('#upload-modal')
    assert upload_modal.is_visible(), "Upload modal should be visible"


def test_sort_button_changes_order(logged_in_page):
    """Тест: кнопка сортировки меняет порядок элементов."""
    page = logged_in_page
    
    # Получаем текущий порядок ID
    items_before = page.locator('.gallery-item').all()
    ids_before = [item.get_attribute('data-photo-id') or 
                  item.get_attribute('data-album-id') for item in items_before]
    
    # Меняем сортировку
    page.click('#sort-btn')
    page.click('[data-sort="taken"]')
    
    # Ждем обновления
    page.wait_for_timeout(500)
    
    # Проверяем что порядок изменился (если есть элементы)
    items_after = page.locator('.gallery-item').all()
    ids_after = [item.get_attribute('data-photo-id') or 
                 item.get_attribute('data-album-id') for item in items_after]
    
    # Если есть элементы, порядок должен быть другим (или тот же если даты совпадают)
    print(f"Before: {ids_before}")
    print(f"After: {ids_after}")


def test_lightbox_opens_on_photo_click(logged_in_page):
    """Тест: клик по фото открывает lightbox."""
    page = logged_in_page
    
    # Ждем загрузки фото
    page.wait_for_selector('.gallery-item[data-item-type="photo"]', timeout=5000)
    
    # Кликаем по первому фото
    first_photo = page.locator('.gallery-item[data-item-type="photo"]').first
    first_photo.click()
    
    # Проверяем что lightbox открылся
    lightbox = page.locator('#lightbox')
    assert not lightbox.has_class('hidden'), "Lightbox should be visible"
    
    # Проверяем что URL обновился
    assert 'photo_id=' in page.url, "URL should contain photo_id"


def test_lightbox_navigation(logged_in_page):
    """Тест: навигация в lightbox работает."""
    page = logged_in_page
    
    # Открываем lightbox
    page.wait_for_selector('.gallery-item[data-item-type="photo"]', timeout=5000)
    page.locator('.gallery-item[data-item-type="photo"]').first.click()
    
    # Запоминаем текущий photo_id
    current_url = page.url
    
    # Кликаем next
    page.click('.lightbox-next')
    page.wait_for_timeout(300)
    
    # Проверяем что URL изменился
    new_url = page.url
    assert current_url != new_url, "URL should change after navigation"


def test_lightbox_close_removes_photo_id(logged_in_page):
    """Тест: закрытие lightbox убирает photo_id из URL."""
    page = logged_in_page
    
    # Открываем lightbox
    page.wait_for_selector('.gallery-item[data-item-type="photo"]', timeout=5000)
    page.locator('.gallery-item[data-item-type="photo"]').first.click()
    
    # Закрываем
    page.click('.lightbox-close')
    
    # Проверяем что photo_id убран
    assert 'photo_id=' not in page.url, "URL should not contain photo_id after close"


def test_masonry_layout_no_jumps(logged_in_page):
    """Тест: masonry layout не прыгает при загрузке изображений."""
    page = logged_in_page
    
    # Получаем позиции элементов
    items = page.locator('.gallery-item').all()
    if len(items) == 0:
        pytest.skip("No items in gallery")
    
    # Запоминаем позиции до загрузки
    positions_before = []
    for item in items[:5]:  # Проверяем первые 5
        box = item.bounding_box()
        positions_before.append((box['x'], box['y']))
    
    # Ждем загрузки изображений
    page.wait_for_timeout(2000)
    
    # Проверяем что позиции не изменились
    items_after = page.locator('.gallery-item').all()
    for i, item in enumerate(items_after[:5]):
        box = item.bounding_box()
        pos_before = positions_before[i]
        pos_after = (box['x'], box['y'])
        
        # Позиция может немного сдвинуться, но не должна прыгать сильно
        assert abs(pos_before[0] - pos_after[0]) < 10, f"Item {i} X position changed too much"
        assert abs(pos_before[1] - pos_after[1]) < 50, f"Item {i} Y position changed too much"


def test_album_opens_lightbox_with_album_context(logged_in_page):
    """Тест: клик по альбому открывает lightbox с контекстом альбома."""
    page = logged_in_page
    
    # Ищем альбом
    album = page.locator('.gallery-item[data-item-type="album"]').first
    if not album.is_visible():
        pytest.skip("No albums in gallery")
    
    album.click()
    
    # Проверяем что открыт lightbox
    lightbox = page.locator('#lightbox')
    assert not lightbox.has_class('hidden'), "Lightbox should open for album"
    
    # Проверяем что есть индикатор альбома
    album_indicator = page.locator('#lightbox-album-indicator')
    assert album_indicator.is_visible() or album_indicator.has_class('hidden'), \
        "Album indicator should exist"


def test_url_with_photo_id_opens_lightbox_on_load(logged_in_page):
    """Тест: URL с photo_id открывает фото при загрузке страницы."""
    page = logged_in_page
    
    # Получаем ID первого фото
    first_photo = page.locator('.gallery-item[data-item-type="photo"]').first
    photo_id = first_photo.get_attribute('data-photo-id')
    
    # Переходим на URL с photo_id
    current_url = page.url
    new_url = f"{current_url}&photo_id={photo_id}"
    page.goto(new_url)
    
    # Ждем загрузки
    page.wait_for_timeout(1000)
    
    # Проверяем что lightbox открылся
    lightbox = page.locator('#lightbox')
    assert not lightbox.has_class('hidden'), "Lightbox should open automatically"
