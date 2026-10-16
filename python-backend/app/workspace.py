"""Same-origin static client. Authentication remains on every research API."""
from pathlib import Path
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


def asset_root() -> Path:
    return Path(os.getenv('WEB_DIST_DIR', str(Path(__file__).resolve().parents[2] / 'dist' / 'web'))).resolve()


@router.get('/', include_in_schema=False)
async def workspace():
    path = asset_root() / 'index.html'
    if not path.is_file():
        raise HTTPException(503, 'Web workspace not built. Run bun run build:web.')
    return FileResponse(path, headers={'Cache-Control': 'no-store',
                                      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
                                      'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer'})


@router.get('/assets/{name}', include_in_schema=False)
async def asset(name: str):
    # Bun emits flat hashed filenames. No arbitrary paths or SPA API fallback.
    if Path(name).name != name or not name.endswith(('.js', '.css', '.svg', '.woff2')):
        raise HTTPException(404)
    path = asset_root() / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={'Cache-Control': 'public, max-age=31536000, immutable',
                                      'X-Content-Type-Options': 'nosniff'})
