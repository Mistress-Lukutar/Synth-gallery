# Synth Gallery AI Tagging API Reference

All agent endpoints require `X-API-Key` header.

---

## GET /api/ai/tags

Fetch the complete tag dictionary. Call once and cache.

### Request
```
GET /api/ai/tags
X-API-Key: <key>
```

### Response
```json
{
  "tags": [
    {
      "id": 2,
      "name": "animal",
      "display_name": "Animal",
      "description": "Living organisms",
      "category": "General"
    },
    {
      "id": 4,
      "name": "fox",
      "display_name": "Fox",
      "description": null,
      "category": "Animals"
    }
  ],
  "total": 50
}
```

---

## GET /api/ai/jobs/pending

List jobs waiting to be processed.

### Request
```
GET /api/ai/jobs/pending
X-API-Key: <key>
```

### Response
```json
{
  "jobs": [
    {
      "id": 1,
      "item_id": "54c04918-...",
      "status": "pending",
      "created_at": "2026-04-30T09:40:23",
      "retry_count": 0
    }
  ]
}
```

---

## POST /api/ai/jobs/{id}/claim

Atomically lock a job. Returns item metadata.

### Request
```
POST /api/ai/jobs/1/claim
X-API-Key: <key>
```

### Response
```json
{
  "job": {
    "id": 1,
    "item_id": "54c04918-...",
    "status": "processing",
    "created_at": "2026-04-30T09:40:23",
    "retry_count": 0
  },
  "item": {
    "id": "54c04918-...",
    "title": "photo.jpg",
    "description": "A red fox in the snow",
    "file_url": "/files/54c04918-...",
    "media_type": "image",
    "content_type": "image/jpeg"
  },
  "existing_tags": [2, 4, 5]
}
```

### Errors
- `409` — Job already claimed or not pending
- `404` — Job does not exist

---

## GET /api/ai/items/{id}/file

Download the actual media file.

### Request
```
GET /api/ai/items/54c04918-.../file
X-API-Key: <key>
```

### Response
Binary file data (`image/jpeg`, `image/png`, `video/mp4`, etc.)

### Errors
- `403` — Item is encrypted/safe (agent cannot access)
- `404` — Item or file not found

---

## POST /api/ai/jobs/{id}/results

Submit tagging results. Accepts `tag_ids` or `tag_names`.

### Request
```
POST /api/ai/jobs/1/results
X-API-Key: <key>
Content-Type: application/json

{"tag_names": ["fox", "red_fox"]}
```

### Success Response
```json
{"status": "ok"}
```

### Error Response (unknown tags)
```json
{
  "detail": {
    "message": "Unknown tags",
    "unknown_tags": ["dragon", "unicorn"]
  }
}
```

### Errors
- `400` — Job not in processing state, or unknown tags
- `404` — Job not found

---

## POST /api/ai/jobs/{id}/fail

Mark a job as failed if analysis is impossible.

### Request
```
POST /api/ai/jobs/1/fail
X-API-Key: <key>
Content-Type: application/json

{"error": "File is corrupted"}
```

### Response
```json
{"status": "ok"}
```
