"""`ena-serve`: start Ena's API with the synthetic month and the agent (docs/build-plan.md §8).

    uv run ena-serve                       # http://127.0.0.1:8000, the store in tmp/ena.db
    uv run ena-serve --memory --day 2026-11-04 --port 8001
    uv run ena-serve --bank boc            # pay through the bocAPI sandbox

The app the demo runs, built from `.env` as each module reads it:

- The store: SQLite at `tmp/ena.db` under the working directory (`--store`), or in
  memory (`--memory`)
- The synthetic month (`ena/seed.py`), read as of `--day`: by default 16 November, the
  Monday of the lean week, when both invoices have arrived and the forecast flags the week
- The gate, with the month's business and its limit. Its bank is `StandInBank` by
  default, which completes every payment, so a payment is simulated. With `--bank boc`
  it is `BocBank` (`BOC_*`): each payment the gate makes is initiated in the bocAPI
  sandbox and executed once the owner confirms it in BoC's screen, and the agent can read
  the sandbox account's balance and statement (`get_bank_account`)
- The chat agent (`ena/agent.py`, `ENA_*`) and the reply cache, in memory or in `--cache`
- The JCC client (`JCC_*`) for `POST /jcc/callback`
- The Jinius buyer's client (`JINIUS_*`) for `GET /jinius/invoices`, the dashboard's
  "Read invoices from Jinius" button. It only reads: the month's invoices stay the seed's
- The built chat page (`--web web/dist`, from `npm run build`) on the same origin, for a
  host with no Vite dev server in front; `GET /health` answers 200 for the host's checks

Without `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`) in the environment the server
still starts, and `/chat` answers with an error event that names the missing key. The
key is read from the environment, not `.env`: the agent's process inherits only that.
Without `JCC_CALLBACK_SECRET` the callback route refuses every call with 503, so no
unsigned callback is ever accepted; a JCC section filled only in part fails at start.
Without `JINIUS_CLIENT_ID` the Jinius route answers 503 and names the missing settings.
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
from dotenv import dotenv_values
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from ena.advances import Advances
from ena.agent import AgentError, AgentRunner, AgentSettings, Chat, ClaudeAgent
from ena.api import create_app
from ena.cache import ReplyCache
from ena.connectors.boc import BocClient, BocSettings
from ena.connectors.boc_bank import AccountReads, BocBank, check_redirect
from ena.connectors.jcc import JccClient, JccSettings, JccUnavailable
from ena.connectors.jinius import (
    JiniusClient,
    JiniusNotConfigured,
    JiniusSettings,
    PayableFeed,
    read_payable,
)
from ena.dashboard import Dashboard
from ena.gate import COMPLETED, ConsentBank, Gate, PaymentBank
from ena.mcp_server import BankReader, EnaTools, Ledger, ReplayDay, build_server
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
NO_JINIUS = "Jinius is not configured: set JINIUS_CLIENT_ID and JINIUS_CLIENT_SECRET in .env"
CALLBACK_PATH = "/jcc/callback"
SIMULATED_PAYMENTS = (
    "payments are simulated: the stand-in bank completes every payment and no money moves;"
    " each receipt is labelled simulated"
)


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
    jinius: JiniusSettings | None = None
    """None when the buyer's Jinius client is not set: the Jinius route answers 503."""
    model_credential: bool
    """Whether the environment holds a credential for the model."""
    boc: BocSettings | None = None
    """None for the stand-in bank; the bocAPI sandbox's settings for `--bank boc`."""
    web: Path | None = None
    """The built chat page to serve at `/`, or None when Vite serves it."""

    @model_validator(mode="after")
    def _day_in_month(self) -> Self:
        if not FIRST_DAY <= self.day <= LAST_DAY:
            raise ValueError(f"the day must be from {FIRST_DAY} to {LAST_DAY}, got {self.day}")
        if self.web is not None and not (self.web / "index.html").is_file():
            raise ValueError(f"the web folder {self.web} has no index.html: run npm run build")
        return self


def _only_init(
    cls: type[BaseSettings],
    /,
    settings_cls: type[BaseSettings],
    init_settings: PydanticBaseSettingsSource,
    **_others: PydanticBaseSettingsSource,
) -> tuple[PydanticBaseSettingsSource, ...]:
    return (init_settings,)


def env_values(prefix: str, env_file: Path | None, environ: Mapping[str, str]) -> dict[str, str]:
    """The `prefix` section from `env_file`, then `environ` over it, keyed by field name.

    A name that would start with `_` is dropped: it would reach one of pydantic-settings'
    own init arguments (`ENA__CLI_PARSE_ARGS` as `_cli_parse_args`), never a field."""
    found = {} if env_file is None or not env_file.is_file() else dotenv_values(env_file)
    merged = {k: v for k, v in found.items() if v is not None} | dict(environ)
    fields = {
        name[len(prefix) :].lower(): value
        for name, value in merged.items()
        if name.upper().startswith(prefix)
    }
    return {name: value for name, value in fields.items() if not name.startswith("_")}


def from_values[S: BaseSettings](cls: type[S], values: Mapping[str, str]) -> S:
    """`cls` built from `values` alone: the process environment and `.env` are not read, so
    a variable exported in the shell reaches the app only through the `environ` given."""
    only = type(cls.__name__, (cls,), {"settings_customise_sources": classmethod(_only_init)})
    built = only(**values)
    if not isinstance(built, cls):
        raise TypeError(f"expected {cls.__name__}, built {type(built).__name__}")
    return built


def load_jcc(env_file: Path | None, environ: Mapping[str, str]) -> JccSettings | None:
    """JCC's settings when `JCC_CALLBACK_SECRET` is set, else None; refuses a partial set."""
    values = env_values("JCC_", env_file, environ)
    if not values.get("callback_secret", ""):
        return None
    return from_values(JccSettings, values)


def load_boc(env_file: Path | None, environ: Mapping[str, str]) -> BocSettings:
    """The bocAPI settings `--bank boc` needs; refuses a set that can't complete a payment."""
    settings = from_values(BocSettings, env_values("BOC_", env_file, environ))
    missing = [
        f"BOC_{name.upper()}"
        for name in ("subscription_id", "account_id")
        if getattr(settings, name) is None
    ]
    if missing:
        raise SystemExit(
            f"ena-serve --bank boc: set {' and '.join(missing)} in .env"
            " (uv run ena-boc subscribe, then activate)"
        )
    try:
        check_redirect(settings)
    except ValueError as error:
        raise SystemExit(f"ena-serve --bank boc: {error}") from error
    return settings


def load_jinius(env_file: Path | None, environ: Mapping[str, str]) -> JiniusSettings | None:
    """The buyer's Jinius settings when `JINIUS_CLIENT_ID` is set, else None."""
    values = env_values("JINIUS_", env_file, environ)
    if not values.get("client_id", "").strip():
        return None
    return from_values(JiniusSettings, values)


def _no_jinius() -> PayableFeed:
    raise JiniusNotConfigured(NO_JINIUS)


def has_model_credential(environ: Mapping[str, str]) -> bool:
    return any(environ.get(name, "").strip() for name in CREDENTIAL_ENV)


# The app


class StandInBank:
    """Completes every payment: the gate is not wired to bocAPI yet (README, the loop)."""

    simulated = True
    """No money moves: each receipt it completes says simulated."""

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
    return lambda tools: ClaudeAgent(settings.agent, build_server(tools), tools.today)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise JccUnavailable(f"JCC is not configured: refused {request.url}")


@dataclass
class Server:
    app: FastAPI
    store: Store
    jcc_http: httpx.AsyncClient
    boc_http: httpx.Client | None = None
    jinius_http: httpx.Client | None = None

    def close(self) -> None:
        asyncio.run(self.jcc_http.aclose())
        if self.boc_http is not None:
            self.boc_http.close()
        if self.jinius_http is not None:
            self.jinius_http.close()
        self.store.close()


def build(
    settings: ServeSettings,
    *,
    runner: RunnerFactory | None = None,
    bank: PaymentBank | ConsentBank | None = None,
    jcc_transport: httpx.AsyncBaseTransport | None = None,
    boc_transport: httpx.BaseTransport | None = None,
    jinius_transport: httpx.BaseTransport | None = None,
) -> Server:
    """The app the demo runs; the runner, the bank and the transports are injectable."""
    month = synthetic_month()
    if settings.store is not None:
        settings.store.parent.mkdir(parents=True, exist_ok=True)
    store = Store(":memory:" if settings.store is None else settings.store)
    boc_http: httpx.Client | None = None
    reader: BankReader | None = None
    if settings.boc is not None:
        if bank is not None:
            raise ValueError("pass a bank or the bocAPI settings, not both")
        boc_http = httpx.Client(transport=boc_transport, timeout=30.0)
        boc_bank = BocBank.from_settings(BocClient(settings.boc, boc_http), _now)
        bank, reader = boc_bank, AccountReads(boc_bank, store, _now)
        log.info("payments go through the bocAPI sandbox, from account %s", settings.boc.account_id)
    elif bank is None:
        log.warning(SIMULATED_PAYMENTS)
    gate = Gate(month.business, store, StandInBank() if bank is None else bank, _now)
    today = ReplayDay(settings.day)
    ledger = Ledger(month, store, today)
    tools = EnaTools(ledger, store, gate, _now, reader)
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
    jinius_http: httpx.Client | None = None
    read_jinius = _no_jinius
    if settings.jinius is not None:
        jinius_http = httpx.Client(transport=jinius_transport, timeout=10.0)
        jinius = JiniusClient(settings.jinius, jinius_http)

        def read_jinius() -> PayableFeed:
            return read_payable(jinius)

    app = create_app(
        store,
        JccClient(jcc_settings, http),
        jcc_settings.callback_secret,
        chat=chat,
        gate=gate,
        advances=Advances(store, gate, month.events, _now),
        dashboard=Dashboard(ledger, store),
        jinius=read_jinius,
    )
    if settings.jcc is None:
        _refuse_callbacks(app)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if settings.web is not None:
        # mounted last, so every API route above wins over a file of the same path
        app.mount("/", StaticFiles(directory=settings.web, html=True), name="web")
    return Server(app, store, http, boc_http, jinius_http)


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
    p.add_argument(
        "--bank",
        choices=("stand-in", "boc"),
        default="stand-in",
        help="stand-in: simulated payments (default); boc: the bocAPI sandbox (BOC_* in .env)",
    )
    p.add_argument(
        "--web", type=Path, default=None, help="serve the built chat page from this folder"
    )
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
            agent=from_values(AgentSettings, env_values("ENA_", env_file, environ)),
            jcc=load_jcc(env_file, environ),
            jinius=load_jinius(env_file, environ),
            model_credential=has_model_credential(environ),
            boc=load_boc(env_file, environ) if args.bank == "boc" else None,
            web=args.web,
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
    if settings.jinius is None:
        log.warning("%s; /jinius/invoices answers 503", NO_JINIUS)
    where = "memory" if settings.store is None else settings.store
    log.info("the synthetic month as of %s, the store in %s", settings.day, where)
    server = build(settings)
    try:
        uvicorn.run(server.app, host=args.host, port=args.port)
    finally:
        server.close()
