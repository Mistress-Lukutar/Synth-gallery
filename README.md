# Photo Gallery

Локальная фотогалерея с поддержкой ИИ-тегирования.

## Быстрый старт (Docker)

```bash
# Запуск
docker-compose up --build

# Открыть в браузере
http://localhost:8000
```

## Запуск без Docker (для разработки в PyCharm)

```bash
# Установить зависимости
pip install -r requirements.txt

# Запустить сервер
uvicorn app.main:app --reload --port 8000
```

## Структура проекта

```
photo-gallery/
├── app/
│   ├── main.py          # Основной код приложения
│   ├── database.py      # Работа с SQLite
│   ├── templates/       # HTML шаблоны
│   └── static/          # CSS стили
├── uploads/             # Оригиналы фото
├── thumbnails/          # Превью
├── gallery.db           # База данных (создаётся автоматически)
└── docker-compose.yml
```

## API для ИИ-сервиса

### Получить фото без тегов
```
GET /api/photos/untagged
```

### Установить теги
```
POST /api/photos/{photo_id}/tags
Content-Type: application/json

["тег1", "тег2", "тег3"]
```

## Планы развития

- [ ] Авторизация
- [ ] Поиск по тегам
- [ ] Альбомы
- [ ] Интеграция с ИИ-сервисом
- [ ] Доступ извне (HTTPS)
