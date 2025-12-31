"""Photo Gallery Application - FastAPI Entry Point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR
from .database import init_db, cleanup_expired_sessions
from .middleware import AuthMiddleware, CSRFMiddleware

# Import routers
from .routes.auth import router as auth_router
from .routes.gallery import router as gallery_router
from .routes.folders import router as folders_router, users_router
from .routes.tags import router as tags_router
from .routes.api import router as api_router
from .routes.admin import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup: runs before the application starts accepting requests
    init_db()
    cleanup_expired_sessions()
    yield
    # Shutdown: runs when application is stopping (cleanup code goes here)


app = FastAPI(title="Photo Gallery", lifespan=lifespan)

# Add middleware (order matters - first added = last executed)
app.add_middleware(AuthMiddleware)
app.add_middleware(CSRFMiddleware)

# Static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

# Include routers
app.include_router(auth_router)
app.include_router(gallery_router)
app.include_router(folders_router)
app.include_router(users_router)
app.include_router(tags_router)
app.include_router(api_router)
app.include_router(admin_router)
