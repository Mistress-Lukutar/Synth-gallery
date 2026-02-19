# Synth Gallery Configuration

## Base URL / Subpath Configuration

The application supports running under a subpath (e.g., `localhost/synth/` instead of `localhost/`).

### Setting up Base URL

Set the `SYNTH_BASE_URL` environment variable before starting the application:

```bash
# Windows PowerShell
$env:SYNTH_BASE_URL = "synth"
python -m uvicorn app.main:app --reload

# Windows CMD
set SYNTH_BASE_URL=synth
python -m uvicorn app.main:app --reload

# Linux/macOS/Bash
export SYNTH_BASE_URL=synth
python -m uvicorn app.main:app --reload
```

Or using Docker Compose, add to your `docker-compose.yml`:

```yaml
services:
  gallery:
    build: .
    environment:
      - SYNTH_BASE_URL=synth
    # ... rest of config
```

### Examples

| SYNTH_BASE_URL | Resulting URL                            |
|----------------|------------------------------------------|
| (empty)        | `http://localhost:8000/login`            |
| `synth`        | `http://localhost:8000/synth/login`      |
| `gallery/v2`   | `http://localhost:8000/gallery/v2/login` |

### Reverse Proxy Configuration

When using a reverse proxy (nginx, traefik, etc.), configure it to strip the base path:

**nginx example:**
```nginx
location /synth/ {
    proxy_pass http://localhost:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Prefix /synth;
}
```

**traefik example:**
```yaml
labels:
  - "traefik.http.routers.gallery.rule=PathPrefix(`/synth`)"
  - "traefik.http.middlewares.strip-synth.stripprefix.prefixes=/synth"
  - "traefik.http.routers.gallery.middlewares=strip-synth"
```

### Notes

- The `SYNTH_BASE_URL` should NOT include leading or trailing slashes
- Static files (`/static/`) are automatically prefixed with the base URL
- All API endpoints, redirects, and templates respect the base URL setting
- The base URL is available in templates as `{{ base_url }}`
