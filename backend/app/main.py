"""FastAPI entrypoint for the SocialEase Agent backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router

app = FastAPI(
    title="SocialEase Agent API",
    description=(
        "A safe, controllable agent workflow for university social stress "
        "practice. This API is not a medical or diagnostic product."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Return a lightweight health status for local development."""
    return {"status": "ok"}
