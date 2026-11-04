"""The chat backend: serves the page, streams the agent's steps and opens the PR on request.

Start it with: python -m oncall_agent.web   (then open http://127.0.0.1:8765)

The agent's steps travel to the browser as Server-Sent Events (SSE): a response that stays open
and sends one small text block per event, so each step shows up while the agent is still working.
"""

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from oncall_agent.agent import (
    AgentError,
    AgentEvent,
    AssistantMessage,
    Conversation,
    FixProposed,
    Proposal,
    StepFinished,
    StepStarted,
    run_turn,
)
from oncall_agent.config import Settings, load_settings
from oncall_agent.github import GitHubClient
from oncall_agent.kube import kube_clients
from oncall_agent.recovery import watch_recovery
from oncall_agent.status import EnvironmentStatus, get_all_statuses, get_environment_status
from oncall_agent.toolbox import ToolBox, connect_toolbox
from oncall_agent.tools import cluster

STATIC_DIR = Path(__file__).parent / "static"
WATCHED_ENVIRONMENT = "dev"  # the fixes only ever change the dev overlay
SSE_HEADERS = {"Cache-Control": "no-cache"}
UNEXPECTED = "Mira la terminal donde lo arrancaste."

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str


@dataclass(frozen=True)
class RecoveryDeps:
    """The three checks the recovery watch polls, and how often. Tests pass fakes."""

    get_pr: Callable[[int], dict[str, Any]]
    get_app: Callable[[str], dict[str, Any]]
    get_status: Callable[[str], EnvironmentStatus]
    poll_seconds: float = 5.0
    timeout_seconds: float = 900.0


def default_recovery_deps(settings: Settings) -> RecoveryDeps:
    github = GitHubClient(settings.gitops_repo, settings.github_token)
    return RecoveryDeps(
        get_pr=github.get_pull_request,
        get_app=lambda env: cluster.get_argocd_app(env, kube_clients(settings), settings),
        get_status=lambda env: get_environment_status(env, kube_clients(settings)),
    )


def sse(event: str, data: dict[str, Any]) -> str:
    """One Server-Sent Event. json.dumps never writes a newline, so `data:` fits on one line."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def agent_event_to_sse(event: AgentEvent) -> str:
    match event:
        case StepStarted():
            return sse("step_started", dataclasses.asdict(event))
        case StepFinished():
            return sse("step_finished", dataclasses.asdict(event))
        case AssistantMessage():
            return sse("assistant_message", {"text": event.text})
        case FixProposed():
            p = event.proposal
            # The file content and its blob sha stay here: the browser only ever sends the id back.
            fields = {"file_path": p.file_path, "title": p.title, "explanation": p.explanation}
            return sse("fix_proposed", {"id": p.id, **fields, "diff": p.diff})
        case AgentError():
            return sse("error", {"message": event.message})


def create_app(
    settings: Settings | None = None,
    toolbox_factory: Callable[[], AbstractAsyncContextManager[ToolBox]] = connect_toolbox,
    llm_factory: Callable[[], Any] | None = None,
    statuses: Callable[[], list[EnvironmentStatus]] | None = None,
    recovery_deps: RecoveryDeps | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    make_llm = llm_factory or (lambda: anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key))
    statuses = statuses or (lambda: get_all_statuses(lambda: kube_clients(settings)))
    recovery = recovery_deps or default_recovery_deps(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # One MCP server for the whole life of the app, not one per question.
        async with toolbox_factory() as toolbox:
            app.state.toolbox = toolbox
            app.state.llm = make_llm() if settings.anthropic_api_key else None
            app.state.conversation = Conversation()
            app.state.chat_lock = asyncio.Lock()  # one question at a time
            app.state.pr_lock = asyncio.Lock()  # two clicks at once still open one PR
            try:
                yield
            finally:
                if app.state.llm is not None:
                    await app.state.llm.close()

    app = FastAPI(title="oncall-agent", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def find_proposal(proposal_id: str) -> Proposal:
        proposal = app.state.conversation.proposals.get(proposal_id)
        if proposal is None:
            raise HTTPException(
                404, "No encuentro esa propuesta. Puede que hayas empezado una conversación nueva."
            )
        return proposal

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        # A plain def: FastAPI runs it in a worker thread, because the Kubernetes calls block.
        environments = [
            {
                "environment": s.environment,
                "health": str(s.health),
                "ready_pods": s.ready_pods,
                "total_pods": s.total_pods,
                "detail": s.detail,
            }
            for s in statuses()
        ]
        return {"environments": environments}

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        return StreamingResponse(
            chat_stream(request.message), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def chat_stream(message: str) -> AsyncIterator[str]:
        lock = app.state.chat_lock
        if not settings.anthropic_api_key:
            yield sse("error", {"message": "Falta ANTHROPIC_API_KEY en el fichero .env"})
        elif lock.locked():
            yield sse("error", {"message": "Ya estoy respondiendo a otra pregunta"})
        else:
            # Nothing awaits between locked() and taking the lock, so no other question can
            # slip in between. If the browser goes away, the lock is released here as well.
            async with lock:
                turn = run_turn(
                    app.state.conversation,
                    message,
                    app.state.toolbox,
                    app.state.llm,
                    settings.model,
                    settings.max_steps,
                )
                try:
                    # aclosing: the turn is closed (and rolled back if unfinished) before the
                    # lock is released, even when the browser leaves half-way.
                    async with aclosing(turn):
                        async for event in turn:
                            yield agent_event_to_sse(event)
                except Exception as exc:  # a bug or a dead MCP server: still end cleanly
                    logger.exception("The chat turn failed")
                    problem = f"Error inesperado en el servidor ({type(exc).__name__})."
                    yield sse("error", {"message": f"{problem} {UNEXPECTED}"})
        yield sse("done", {})

    @app.post("/api/conversation/reset")
    async def reset() -> dict[str, bool]:
        app.state.conversation = Conversation()
        return {"ok": True}

    @app.post("/api/proposals/{proposal_id}/pull-request")
    async def open_pull_request(proposal_id: str) -> dict[str, Any]:
        if not settings.github_token:
            raise HTTPException(400, "Falta GITHUB_TOKEN en el fichero .env")
        proposal = find_proposal(proposal_id)
        async with app.state.pr_lock:
            if proposal.pr is None:
                # The stored proposal, never data from the browser: what was reviewed is
                # exactly what goes to GitHub.
                outcome = await app.state.toolbox.call(
                    "open_pull_request",
                    {
                        "file_path": proposal.file_path,
                        "new_content": proposal.new_content,
                        "title": proposal.title,
                        "explanation": proposal.explanation,
                        "base_blob_sha": proposal.base_blob_sha,
                    },
                )
                if not outcome.ok:
                    raise HTTPException(409, outcome.text)
                proposal.pr = {"number": outcome.data["number"], "url": outcome.data["url"]}
        return proposal.pr

    @app.get("/api/proposals/{proposal_id}/watch")
    async def watch(proposal_id: str) -> StreamingResponse:
        proposal = find_proposal(proposal_id)
        if proposal.pr is None:
            raise HTTPException(409, "Esta propuesta todavía no tiene Pull Request")
        return StreamingResponse(
            watch_stream(proposal.pr["number"]), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def watch_stream(pr_number: int) -> AsyncIterator[str]:
        updates = watch_recovery(
            pr_number,
            WATCHED_ENVIRONMENT,
            recovery.get_pr,
            recovery.get_app,
            recovery.get_status,
            poll_seconds=recovery.poll_seconds,
            timeout_seconds=recovery.timeout_seconds,
        )
        try:
            async for update in updates:
                yield sse("recovery", dataclasses.asdict(update))
        except Exception as exc:  # done=True so the browser stops listening
            logger.exception("The recovery watch failed")
            problem = f"He dejado de vigilar por un error inesperado ({type(exc).__name__})."
            yield sse(
                "recovery", {"stage": "error", "message": f"{problem} {UNEXPECTED}", "done": True}
            )

    return app
