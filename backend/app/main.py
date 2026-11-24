import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Settings
from .db import Database
from .llm import AllProvidersUnavailable, LLMGateway, parse_simulation
from .schemas import ChatRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("api")


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = Database(settings.db_path)
    gateway: dict = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.init()  # schema is created automatically on startup
        gateway["g"] = LLMGateway(settings)
        logger.info("started: %d key(s), cache=%s", len(gateway["g"].keys), gateway["g"].cache_enabled)
        yield

    app = FastAPI(title="LiteLLM Chat API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],  # includes X-Simulate-Failure
    )

    @app.exception_handler(Exception)
    async def unhandled(_, exc: Exception):  # never leak a stack trace or HTML page
        logger.exception("unhandled error")
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    def health():
        try:
            db.ping()
        except Exception:
            return JSONResponse(status_code=503, content={"detail": "database unreachable"})
        g: LLMGateway = gateway["g"]
        if not g.keys:
            return JSONResponse(status_code=503, content={"detail": "no api keys configured"})
        return {"status": "ok", "keys_configured": len(g.keys), "cache_enabled": g.cache_enabled}

    @app.post("/chat")
    def chat(body: ChatRequest, x_simulate_failure: Optional[str] = Header(default=None)):
        start = time.perf_counter()
        g: LLMGateway = gateway["g"]

        stored_prompt = db.get_system_prompt(body.session_id)
        system_prompt = body.system_prompt or stored_prompt or settings.default_system_prompt
        history = db.recent_history(body.session_id, settings.max_history_messages)
        messages = (
            [{"role": "system", "content": system_prompt}]
            + history
            + [{"role": "user", "content": body.message}]
        )

        simulate = parse_simulation(x_simulate_failure) if settings.enable_failure_simulation else {}
        try:
            result = g.chat(messages, simulate)
        except AllProvidersUnavailable:
            return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

        latency_ms = max(1, int(round((time.perf_counter() - start) * 1000)))
        metadata = {
            "resolution_source": result.resolution_source,
            "model": result.model,
            "latency_ms": latency_ms,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "key_id": result.key_id,
            "fallback_attempts": result.fallback_attempts,
        }
        db.save_exchange(body.session_id, body.system_prompt, body.message, result.reply, metadata)
        return {"session_id": body.session_id, "reply": result.reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def session_messages(session_id: str):
        if not db.session_exists(session_id):
            return JSONResponse(status_code=404, content={"detail": "session not found"})
        return {"session_id": session_id, "messages": db.all_messages(session_id)}

    return app


app = create_app()
