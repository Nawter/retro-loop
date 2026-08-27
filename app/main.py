from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db, hub, sessions


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="Weekly Retro", lifespan=lifespan)
app.include_router(sessions.router)
app.include_router(hub.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
