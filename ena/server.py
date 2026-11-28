"""`ena-serve`: start Ena's API with the synthetic month and the agent (docs/build-plan.md §8).

    uv run ena-serve                       # http://127.0.0.1:8000, the store in tmp/ena.db
    uv run ena-serve --memory --day 2026-11-04 --port 8001

The app the demo runs, built from `.env` as each module reads it:

- The store: SQLite at `tmp/ena.db` under the working directory (`--store`), or in
  memory (`--memory`)
- The synthetic month (`ena/seed.py`), read as of `--day`: by default 16 November, the
  Monday of the lean week, when both invoices have arrived and the forecast flags the week
- The gate, with the month's business and its limit. Its bank is `StandInBank`, which
  completes every payment: the gate is not wired to bocAPI yet, so a payment is simulated
- The chat agent (`ena/agent.py`, `ENA_*`) and the reply cache, in memory or in `--cache`
- The JCC client (`JCC_*`) for `POST /jcc/callback`

Without `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) in the environment the server
still starts, and `/chat` answers with an error event that names the missing key. The
key is read from the environment, not `.env`: the agent's process inherits only that.
Without `JCC_CALLBACK_SECRET` the callback route refuses every call with 503, so no
unsigned callback is ever accepted; a JCC section filled only in part fails at start.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ena.agent import AgentError, AgentRunner, AgentSettings, Chat, ClaudeAgent
from ena.api import create_app
from ena.cache import ReplyCache
from ena.connectors.jcc import JccClient, JccSettings, JccUnavailable
from ena.gate import COMPLETED, Gate, PaymentBank
from ena.mcp_server import EnaTools, Ledger, ReplayDay, build_server
from ena.seed import FIRST_DAY, LAST_DAY, LEAN_WEEK, SEED, synthetic_month
from ena.store import Store

log = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_STORE = Path("tmp/ena.db")
DEFAULT_DAY = LEAN_WEEK
ENV_FILE = Path(".env")

CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
"""Either one lets the agent reach the model."""

NO_CREDENTIAL = "no Anthropic credential: set ANTHROPIC_API_KEY in the shell that runs ena-serve"
NO_JCC = "JCC is not configured: set JCC_API_USER, JCC_API_PASSWORD and JCC_CALLBACK_SECRET"
CALLBACK_PATH = "/jcc/callback"


class ServeSettings(BaseModel):
    """What the served app is built from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    store: Path | None
    """The SQLite file, or None for a store in memory."""
    day: dt.date
    """The business's current day in the synthetic month."""
    cache: Path | None
    """The reply cache's JSON file, or None to keep it in memory only."""
    agent: AgentSettings
    jcc: JccSettings | None
    """None when JCC is not configured: the callback route refuses every call."""
    model_credential: bool
    """Whether the environment holds a credential for the model."""

    @model_validator(mode="after")
    def _day_in_month(self) -> Self:
        if not FIRST_DAY <= self.day <= LAST_DAY:
            raise ValueError(f"the day must be from {FIRST_DAY} to {LAST_DAY}, got {self.day}")
        return self


class _JccPresence(BaseSettings):
    """Only whether `JCC_CALLBACK_SECRET` is set: it decides whether JCC is configured."""

    model_config = SettingsConfigDict(env_prefix="JCC_", env_file=".env", extra="ignore")

    callback_secret: SecretStr = SecretStr("")


def load_jcc(env_file: Path | None) -> JccSettings | None:
    """JCC's settings when `JCC_CALLBACK_SECRET` is set, else None; refuses a partial set."""
    if not _JccPresence(_env_file=env_file).callback_secret.get_secret_value():
        return None
    return JccSettings(_env_file=env_file)


def has_model_credential(environ: Mapping[str, str]) -> bool:
    return any(environ.get(name, "").strip() for name in CREDENTIAL_ENV)


# The app


class StandInBank:
    """Completes every payment: the gate is not wired to bocAPI yet (README, the loop)."""

    def pay(self, action: str, amount_cents: int, ref: str) -> str:
        log.info("stand-in bank: %s, %d cents, ref %s", action, amount_cents, ref)
        return COMPLETED


class NoCredentialAgent:
    """The agent when the model can't be reached: every turn is the error event."""

    async def run(self, prompt: str) -> AsyncIterator[str]:
        raise AgentError(NO_CREDENTIAL)
        yield  # an async generator, as `AgentRunner.run` is


RunnerFactory = Callable[[EnaTools], AgentRunner]


def claude_runner(settings: ServeSettings) -> RunnerFactory:
    if not settings.model_credential:
        return lambda _tools: NoCredentialAgent()
    return lambda tools: ClaudeAgent(settings.agent, build_server(tools))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise JccUnavailable(f"JCC is not configured: refused {request.url}")


@dataclass
class Server:
    app: FastAPI
    store: Store
    jcc_http: httpx.AsyncClient

    def close(self) -> None:
        asyncio.run(self.jcc_http.aclose())
        self.store.close()


def build(
    settings: ServeSettings,
    *,
    runner: RunnerFactory | None = None,
    bank: PaymentBank | None = None,
    jcc_transport: httpx.AsyncBaseTransport | None = None,
) -> Server:
    """The app the demo runs; the runner, the bank and JCC's transport are injectable."""
    month = synthetic_month()
    if settings.store is not None:
        settings.store.parent.mkdir(parents=True, exist_ok=True)
    store = Store(":memory:" if settings.store is None else settings.store)
    gate = Gate(month.business, store, StandInBank() if bank is None else bank, _now)
    today = ReplayDay(settings.day)
    tools = EnaTools(Ledger(month, store, today), store, gate, _now)
    agent = (claude_runner(settings) if runner is None else runner)(tools)
    chat = Chat(tools, agent, today, ReplyCache(SEED, settings.cache))

    if settings.jcc is None:
        # create_app refuses an empty secret: an unguessable one, and a client that never
        # reaches the network, stand behind the middleware that refuses the route
        secret = SecretStr(secrets.token_urlsafe(32))
        jcc_settings = JccSettings(
            api_user="not-configured",
            api_password=secret,
            callback_secret=secret,
            _env_file=None,
        )
        http = httpx.AsyncClient(transport=httpx.MockTransport(_refuse))
    else:
        jcc_settings = settings.jcc
        http = httpx.AsyncClient(transport=jcc_transport, timeout=10.0)
    app = create_app(
        store,
        JccClient(jcc_settings, http),
        jcc_settings.callback_secret,
        chat=chat,
        gate=gate,
    )
    if settings.jcc is None:
        _refuse_callbacks(app)
    return Server(app, store, http)


def _refuse_callbacks(app: FastAPI) -> None:
    @app.middleware("http")
    async def no_jcc(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path.rstrip("/") == CALLBACK_PATH:
            return JSONResponse({"detail": NO_JCC}, status_code=503)
        return await call_next(request)


# The command


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ena-serve", description=__doc__.split("\n", 1)[0])
    p.add_argument("--host", default=DEFAULT_HOST, help=f"default {DEFAULT_HOST}")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    p.add_argument(
        "--day",
        type=dt.date.fromisoformat,
        default=DEFAULT_DAY,
        help=f"the business's day, {FIRST_DAY} to {LAST_DAY} (default {DEFAULT_DAY})",
    )
    where = p.add_mutually_exclusive_group()
    where.add_argument("--store", type=Path, default=DEFAULT_STORE, help=f"default {DEFAULT_STORE}")
    where.add_argument("--memory", action="store_true", help="keep the store in memory")
    p.add_argument("--cache", type=Path, default=None, help="the reply cache's JSON file")
    return p


def load_settings(
    args: argparse.Namespace, environ: Mapping[str, str], env_file: Path | None
) -> ServeSettings:
    """The settings from the flags, `env_file` and the environment; exits on a bad one."""
    try:
        return ServeSettings(
            store=None if args.memory else args.store,
            day=args.day,
            cache=args.cache,
            agent=AgentSettings(_env_file=env_file),
            jcc=load_jcc(env_file),
            model_credential=has_model_credential(environ),
        )
    except ValidationError as error:
        problems = "; ".join(
            f"{error.title} {'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()
        )
        raise SystemExit(f"ena-serve: invalid settings: {problems}") from error


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = load_settings(args, os.environ, ENV_FILE)
    if not settings.model_credential:
        log.warning("%s; /chat answers with an error event", NO_CREDENTIAL)
    if settings.jcc is None:
        log.warning("%s in .env; %s refuses every call", NO_JCC, CALLBACK_PATH)
    where = "memory" if settings.store is None else settings.store
    log.info("the synthetic month as of %s, the store in %s", settings.day, where)
    server = build(settings)
    try:
        uvicorn.run(server.app, host=args.host, port=args.port)
    finally:
        server.close()
