# Synth Gallery Test Suite

This test suite provides **regression protection** during the refactoring of the God Module (`database.py`) to Repository Pattern.

## Philosophy

**Test behavior, not implementation.**

These tests verify that:
- API contracts remain unchanged
- User workflows continue to work
- Data integrity is maintained

They do NOT test:
- Internal implementation details
- Specific SQL queries
- Module structure

This allows you to refactor `database.py` into multiple repositories while having confidence that nothing is broken.

## Structure

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── integration/             # API-level integration tests
│   ├── test_auth.py         # Login/logout/session
│   ├── test_upload.py       # File upload/download
│   ├── test_folders.py      # Folder CRUD & permissions
│   └── test_gallery.py      # Gallery view & file access
└── unit/                    # Isolated unit tests
    └── test_encryption.py   # Crypto primitives
```

## Running Tests

### Requirements

```bash
pip install pytest pytest-asyncio httpx
```

### Run All Tests

```bash
pytest tests/ -v
```

### Run Specific Test File

```bash
pytest tests/integration/test_upload.py -v
```

### Run with Coverage

```bash
pip install pytest-cov
pytest --cov=app --cov-report=html
```

### Run Critical Tests Only (Fast Feedback)

```bash
pytest tests/integration/test_auth.py tests/integration/test_upload.py -v
```

## Test Isolation

Each test runs in complete isolation:
- **Temporary database** (`tmp_path/test.db`)
- **Temporary directories** for uploads/thumbnails
- **Fresh user session** for each test

No test data persists between tests.

## Fixtures

### Client Fixtures

| Fixture                | Description                      |
|------------------------|----------------------------------|
| `client`               | Unauthenticated TestClient       |
| `authenticated_client` | Logged in as `test_user`         |
| `test_user`            | User credentials dict            |
| `second_user`          | Second user for permission tests |

### Data Fixtures

| Fixture            | Description                      |
|--------------------|----------------------------------|
| `test_folder`      | Folder created for test user     |
| `test_image_bytes` | Valid JPEG in memory             |
| `uploaded_photo`   | Photo uploaded during test setup |
| `encrypted_user`   | User with DEK in cache           |

### Context Managers

```python
from conftest import login_as, temp_folder

# Temporarily switch user
with login_as(client, "otheruser", "password"):
    response = client.get("/api/folders/tree")
    # Acting as otheruser

# Auto-cleanup folder
with temp_folder(client, user_id, "Temporary") as folder_id:
    # Use folder_id
    pass
# Folder deleted
```

## Using Tests During Refactoring

### Phase 1: Before Refactoring (Baseline)

```bash
# Ensure all tests pass with current implementation
pytest tests/ -v

# Save test output as baseline
pytest tests/ --tb=short > baseline.txt
```

### Phase 2: During Refactoring

**Key Rule:** Tests should remain green throughout refactoring.

#### Example: Extracting UserRepository

1. **Create new repository file** (doesn't affect tests yet):
   ```python
   # app/infrastructure/repositories/user_repo.py
   class UserRepository:
       def get_by_id(self, user_id: int) -> dict: ...
   ```

2. **Migrate one function at a time**:
   ```python
   # app/database.py
   from .infrastructure.repositories.user_repo import UserRepository
   
   def get_user_by_id(user_id: int):
       # Keep same signature, change implementation
       repo = UserRepository(get_db())
       return repo.get_by_id(user_id)
   ```

3. **Run tests after each change**:
   ```bash
   pytest tests/integration/test_auth.py -v
   ```

4. **If tests fail**: You changed behavior, not just structure. Revert and retry.

### Phase 3: After Refactoring

```bash
# Full test suite should still pass
pytest tests/ -v

# Compare with baseline
diff baseline.txt <(pytest tests/ --tb=short)
```

## Common Issues During Refactoring

### Issue: "Database is locked"

**Cause:** Connection not closed between tests.

**Fix:** Check `conftest.py:fresh_database` - it closes connections. If you create connections elsewhere, ensure cleanup.

### Issue: "No such table"

**Cause:** `init_db()` wasn't called or schema changed.

**Fix:** Ensure `fresh_database` fixture is used. If you changed schema, update `init_db()` first.

### Issue: Tests pass individually but fail together

**Cause:** Shared state between tests (global variables, caches).

**Fix:** Check for:
- Global caches not cleared (`dek_cache.invalidate_all()`)
- Filesystem state (`isolated_environment` should handle this)
- Thread-local storage not reset

## Writing New Tests During Refactoring

### DO

```python
def test_user_can_upload_photo(authenticated_client, test_folder, test_image_bytes):
    """Test through API, not internals."""
    response = authenticated_client.post(
        "/upload",
        data={"folder_id": test_folder},
        files={"file": ("test.jpg", test_image_bytes, "image/jpeg")}
    )
    assert response.status_code == 200
    assert "id" in response.json()
```

### DON'T

```python
def test_user_inserted_into_db():
    """Testing internals - fragile during refactoring."""
    db = get_db()
    cursor = db.execute("INSERT INTO users ...")
    assert cursor.rowcount == 1  # Will break when using Repository
```

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/tests.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-asyncio httpx
      - run: pytest tests/ -v --tb=short
```

### Pre-commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash
pytest tests/integration/test_auth.py tests/integration/test_upload.py -q || exit 1
```

## Test Coverage Goals

| Module                       | Coverage | Notes                            |
|------------------------------|----------|----------------------------------|
| `app/routes/`                | 80%+     | All endpoints tested             |
| `app/services/encryption.py` | 90%+     | Critical for security            |
| `app/database.py`            | 60%+     | Will decrease during refactoring |

## Migration Checklist

When moving from God Module to Repository Pattern:

- [ ] All existing tests pass
- [ ] New Repository has unit tests
- [ ] Database operations go through Repository
- [ ] Old functions deprecated (with warnings)
- [ ] Integration tests still pass
- [ ] Performance comparable (benchmark if needed)

## Getting Help

If tests fail during refactoring:

1. **Check which tests fail** - specific endpoint or all?
2. **Compare behavior** - use `pytest --pdb` to inspect
3. **Check transactions** - are commits happening?
4. **Verify fixtures** - is test isolation working?

Remember: Tests failing = behavior changed. Either fix the code or update tests if behavior change is intentional.
