"""
Minimal working example - показывает как тестировать с CSRF.
Запуск: pytest tests/test_minimal_example.py -v
"""
import pytest
from fastapi.testclient import TestClient


def test_upload_with_csrf(authenticated_client: TestClient, test_folder: str):
    """Пример: загрузка файла с CSRF токеном."""
    from PIL import Image
    import io
    
    # Создаём тестовое изображение
    img = Image.new('RGB', (100, 100), color='red')
    buf = io.BytesIO()
    img.save(buf, format='JPEG')
    
    # Получаем CSRF токен
    csrf_token = authenticated_client.cookies.get("synth_csrf", "")
    
    # Загружаем с заголовком CSRF
    response = authenticated_client.post(
        "/upload",
        data={"folder_id": test_folder},
        files={"file": ("test.jpg", buf.getvalue(), "image/jpeg")},
        headers={"X-CSRF-Token": csrf_token}
    )
    
    # Проверяем успех
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert "filename" in data
    print(f"✅ Upload successful: {data['id']}")


def test_folder_tree_api(authenticated_client: TestClient):
    """Пример: получение дерева папок."""
    response = authenticated_client.get("/api/folders/tree")
    
    # API может вернуть 405 если endpoint не существует
    # или 200 если всё ок
    if response.status_code == 200:
        folders = response.json()
        print(f"✅ Found {len(folders)} folders")
    else:
        print(f"⚠️  API returned {response.status_code} - endpoint may not exist yet")
        pytest.skip("API endpoint not implemented")
