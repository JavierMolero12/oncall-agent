"""The web backend, with fake tools, a scripted model and fake cluster checks."""

import asyncio
import json
from contextlib import asynccontextmanager, nullcontext

import httpx
import pytest
from fastapi.testclient import TestClient
from llm_fakes import ScriptedLLM, reply, text, tool_use
from toolbox_fakes import FIX, PODS_OK, FakeToolBox

from oncall_agent.config import load_settings
from oncall_agent.status import EnvironmentStatus, Health
from oncall_agent.toolbox import ToolOutcome
from oncall_agent.web.app import ChatRequest, RecoveryDeps, create_app

KEYS = {"ANTHROPIC_API_KEY": "sk-ant-test", "GITHUB_TOKEN": "github_pat_test"}
LIST_DEV = tool_use("list_pods", {"environment": "dev"})
FIX_ARGUMENTS = {key: FIX[key] for key in ("file_path", "new_content", "title", "explanation")}
PROPOSED = ToolOutcome(ok=True, data=FIX, text="{}")
PR = {"number": 7, "url": "https://github.com/JavierMolero12/gitops-manifests/pull/7"}
PR_OPENED = ToolOutcome(
    ok=True,
    data={**PR, "branch": "oncall/fix-1", "summary": "PR #7 abierto en gitops-manifests"},
    text="{}",
)
MERGE_SHA = "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"


def never(_argument):
    raise AssertionError("this test does not watch a recovery")


NO_RECOVERY = RecoveryDeps(get_pr=never, get_app=never, get_status=never)


def propose_script() -> list:
    """The model proposes the fix, then answers."""
    return [
        reply(tool_use("propose_fix", FIX_ARGUMENTS), stop_reason="tool_use"),
        reply(text("Te propongo volver a la imagen que funciona en prod.")),
    ]


def make_app(toolbox=None, llm=None, env=KEYS, statuses=None, recovery=NO_RECOVERY):
    return create_app(
        settings=load_settings(env),
        toolbox_factory=lambda: nullcontext(toolbox or FakeToolBox()),
        llm_factory=lambda: llm or ScriptedLLM(),
        statuses=statuses or list,  # no environments
        recovery_deps=recovery,
    )


def sse(response: httpx.Response) -> list[tuple[str, dict]]:
    """Split a text/event-stream body into (event, data) pairs."""
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def proposal_id(response: httpx.Response) -> str:
    return next(data["id"] for name, data in sse(response) if name == "fix_proposed")


def opened(toolbox: FakeToolBox) -> int:
    return sum(1 for name, _ in toolbox.calls if name == "open_pull_request")


@asynccontextmanager
async def served(app):
    """Like TestClient, but async, so that two requests can be in flight at the same time."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


class GatedLLM(ScriptedLLM):
    """A model that holds its answers until the test opens the gate."""

    def __init__(self, *script):
        super().__init__(*script)
        self.asked = asyncio.Event()
        self.gate = asyncio.Event()

    async def _create(self, **request):
        self.asked.set()
        await self.gate.wait()
        return await super()._create(**request)


class GatedToolBox(FakeToolBox):
    """Opening the PR waits until the test opens the gate, like a slow GitHub."""

    def __init__(self, **outcomes):
        super().__init__(**outcomes)
        self.opening = asyncio.Event()
        self.gate = asyncio.Event()

    async def call(self, name, arguments):
        if name == "open_pull_request":
            self.opening.set()
            await self.gate.wait()
        return await super().call(name, arguments)


# -- page and status ----------------------------------------------------------------------------


def test_page_and_static_files_are_served():
    with TestClient(make_app()) as client:
        page = client.get("/")
        static = client.get("/static/index.html")

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert static.status_code == 200


def test_status_endpoint_shape():
    stuck = EnvironmentStatus("dev", "sample-api-dev", Health.STUCK, 1, 2, "1 de 2 pods listos")
    with TestClient(make_app(statuses=lambda: [stuck])) as client:
        response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "environments": [
            {
                "environment": "dev",
                "health": "Actualización atascada",
                "ready_pods": 1,
                "total_pods": 2,
                "detail": "1 de 2 pods listos",
            }
        ]
    }


# -- chat ---------------------------------------------------------------------------------------


def test_chat_streams_agent_events():
    llm = ScriptedLLM(reply(LIST_DEV, stop_reason="tool_use"), *propose_script())
    toolbox = FakeToolBox(list_pods=PODS_OK, propose_fix=PROPOSED)
    with TestClient(make_app(toolbox, llm)) as client:
        response = client.post("/api/chat", json={"message": "¿Por qué falla dev?"})

    events = sse(response)
    assert [name for name, _ in events] == [
        "step_started",
        "step_finished",
        "step_started",
        "step_finished",
        "fix_proposed",
        "assistant_message",
        "done",
    ]
    assert events[0][1] == {
        "index": 1,
        "tool": "list_pods",
        "title": "Ver pods",
        "arguments": {"environment": "dev"},
    }
    assert events[1][1] == {
        "index": 1,
        "tool": "list_pods",
        "title": "Ver pods",
        "ok": True,
        "summary": "1 de 1 pods listos",
    }
    fix = events[4][1]
    # The browser gets what a person reviews; the file content and blob sha stay on the server.
    assert set(fix) == {"id", "file_path", "title", "explanation", "diff"}
    assert (fix["file_path"], fix["diff"]) == (FIX["file_path"], FIX["diff"])
    assert events[5][1] == {"text": "Te propongo volver a la imagen que funciona en prod."}
    assert events[6][1] == {}


def test_chat_without_api_key_reports_spanish_error():
    llm = ScriptedLLM()  # an empty script: any call to the model would fail the test
    with TestClient(make_app(llm=llm, env={"GITHUB_TOKEN": "t"})) as client:
        response = client.post("/api/chat", json={"message": "Hola"})

    assert sse(response) == [
        ("error", {"message": "Falta ANTHROPIC_API_KEY en el fichero .env"}),
        ("done", {}),
    ]
    assert llm.calls == []


async def test_concurrent_chat_is_rejected():
    llm = GatedLLM(reply(text("Hola.")))
    app = make_app(llm=llm)
    async with served(app) as client:
        first = asyncio.create_task(client.post("/api/chat", json={"message": "Uno"}))
        await llm.asked.wait()  # the first question is waiting for the model
        try:
            # A busy chat answers at once; it must not queue the second question.
            second = await asyncio.wait_for(client.post("/api/chat", json={"message": "Dos"}), 5)
        finally:
            llm.gate.set()
        first_events = sse(await first)

    assert sse(second) == [
        ("error", {"message": "Ya estoy respondiendo a otra pregunta"}),
        ("done", {}),
    ]
    assert first_events[-2:] == [("assistant_message", {"text": "Hola."}), ("done", {})]


async def test_a_browser_that_leaves_mid_answer_frees_the_chat():
    llm = GatedLLM(reply(text("Nunca llega.")))  # the gate stays shut: the model never answers
    app = make_app(llm=llm)
    async with served(app) as client:
        asking = asyncio.create_task(client.post("/api/chat", json={"message": "¿Dev?"}))
        await llm.asked.wait()
        asking.cancel()  # what the server does when the browser goes away
        with pytest.raises(asyncio.CancelledError):
            await asking

        assert not app.state.chat_lock.locked()
        assert app.state.conversation.messages == []


async def test_a_stream_closed_between_two_events_rolls_back_before_freeing_the_chat():
    llm = ScriptedLLM(reply(LIST_DEV, stop_reason="tool_use"), reply(text("Sano.")))
    app = make_app(FakeToolBox(list_pods=PODS_OK), llm)
    async with app.router.lifespan_context(app):
        chat = next(route.endpoint for route in app.routes if route.path == "/api/chat")
        stream = (await chat(ChatRequest(message="¿Dev?"))).body_iterator
        await anext(stream)  # step_started: the turn is half-way
        await stream.aclose()  # the server closes the stream when the browser leaves

        # Rolled back now, not later: a new question must not find half a turn in the history.
        assert app.state.conversation.messages == []
        assert not app.state.chat_lock.locked()


def test_an_unexpected_failure_still_ends_the_stream():
    llm = ScriptedLLM(
        reply(LIST_DEV, stop_reason="tool_use"),
        reply(LIST_DEV, stop_reason="tool_use"),
        reply(text("Dev está sano.")),
    )
    toolbox = FakeToolBox(list_pods=RuntimeError("el servidor MCP se ha cerrado"))
    with TestClient(make_app(toolbox, llm)) as client:
        broken = client.post("/api/chat", json={"message": "¿Dev?"})
        toolbox.outcomes["list_pods"] = PODS_OK
        retried = client.post("/api/chat", json={"message": "¿Y ahora?"})

    assert sse(broken)[-2:] == [
        (
            "error",
            {
                "message": "Error inesperado en el servidor (RuntimeError). "
                "Mira la terminal donde lo arrancaste."
            },
        ),
        ("done", {}),
    ]
    # The chat is free again and the broken turn left nothing behind in the history.
    assert sse(retried)[-2] == ("assistant_message", {"text": "Dev está sano."})
    assert llm.calls[-1]["messages"][0]["content"] == "¿Y ahora?"


def test_reset_starts_a_new_conversation():
    llm = ScriptedLLM(reply(text("Hola.")), reply(text("No te conozco.")))
    with TestClient(make_app(llm=llm)) as client:
        client.post("/api/chat", json={"message": "Hola"})
        reset = client.post("/api/conversation/reset")
        client.post("/api/chat", json={"message": "¿Me recuerdas?"})

    assert reset.status_code == 200
    assert [message["content"] for message in llm.calls[-1]["messages"]] == ["¿Me recuerdas?"]


def test_the_toolbox_opens_once_and_the_model_client_closes_at_shutdown():
    opened_toolboxes = []
    llm = ScriptedLLM(reply(text("Uno.")), reply(text("Dos.")))

    def toolbox_factory():
        opened_toolboxes.append(1)
        return nullcontext(FakeToolBox())

    app = create_app(
        settings=load_settings(KEYS),
        toolbox_factory=toolbox_factory,
        llm_factory=lambda: llm,
        statuses=list,
        recovery_deps=NO_RECOVERY,
    )
    with TestClient(app) as client:
        client.post("/api/chat", json={"message": "Uno"})
        client.post("/api/chat", json={"message": "Dos"})
        assert not llm.closed

    assert opened_toolboxes == [1]
    assert llm.closed


# -- pull request -------------------------------------------------------------------------------


def test_open_pr_uses_stored_proposal():
    toolbox = FakeToolBox(propose_fix=PROPOSED, open_pull_request=PR_OPENED)
    with TestClient(make_app(toolbox, ScriptedLLM(*propose_script()))) as client:
        fix_id = proposal_id(client.post("/api/chat", json={"message": "Arréglalo"}))
        # Whatever the browser sends is ignored: the id alone picks the stored proposal.
        url = f"/api/proposals/{fix_id}/pull-request"
        response = client.post(url, json={"new_content": "otro contenido"})

    assert response.status_code == 200
    assert response.json() == PR
    assert toolbox.calls[-1] == (
        "open_pull_request",
        {
            "file_path": FIX["file_path"],
            "new_content": FIX["new_content"],
            "title": FIX["title"],
            "explanation": FIX["explanation"],
            "base_blob_sha": FIX["base_blob_sha"],
        },
    )


def test_open_pr_twice_returns_same_pr():
    toolbox = FakeToolBox(propose_fix=PROPOSED, open_pull_request=PR_OPENED)
    with TestClient(make_app(toolbox, ScriptedLLM(*propose_script()))) as client:
        fix_id = proposal_id(client.post("/api/chat", json={"message": "Arréglalo"}))
        url = f"/api/proposals/{fix_id}/pull-request"
        first, second = client.post(url), client.post(url)

    assert first.json() == second.json() == PR
    assert opened(toolbox) == 1


async def test_two_clicks_at_once_open_one_pr():
    toolbox = GatedToolBox(propose_fix=PROPOSED, open_pull_request=PR_OPENED)
    app = make_app(toolbox, ScriptedLLM(*propose_script()))
    async with served(app) as client:
        fix_id = proposal_id(await client.post("/api/chat", json={"message": "Arréglalo"}))
        url = f"/api/proposals/{fix_id}/pull-request"
        first = asyncio.create_task(client.post(url))
        await toolbox.opening.wait()  # the first click is opening the PR
        second = asyncio.create_task(client.post(url))
        await asyncio.sleep(0.05)  # the second click arrives in the meantime
        toolbox.gate.set()
        responses = [await first, await second]

    assert [r.json() for r in responses] == [PR, PR]
    assert opened(toolbox) == 1


def test_open_pr_without_token_is_400():
    toolbox = FakeToolBox(propose_fix=PROPOSED, open_pull_request=PR_OPENED)
    env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
    with TestClient(make_app(toolbox, ScriptedLLM(*propose_script()), env=env)) as client:
        fix_id = proposal_id(client.post("/api/chat", json={"message": "Arréglalo"}))
        response = client.post(f"/api/proposals/{fix_id}/pull-request")

    assert response.status_code == 400
    assert response.json() == {"detail": "Falta GITHUB_TOKEN en el fichero .env"}
    assert opened(toolbox) == 0


def test_open_pr_for_an_unknown_proposal_is_404():
    with TestClient(make_app()) as client:
        response = client.post("/api/proposals/nope/pull-request")

    assert response.status_code == 404
    assert response.json() == {
        "detail": "No encuentro esa propuesta. Puede que hayas empezado una conversación nueva."
    }


def test_a_refused_pr_is_409_with_the_tool_message_and_can_be_retried():
    refused = ToolOutcome(
        ok=False, data=None, text="El fichero ha cambiado en main desde la propuesta."
    )
    toolbox = FakeToolBox(propose_fix=PROPOSED, open_pull_request=refused)
    with TestClient(make_app(toolbox, ScriptedLLM(*propose_script()))) as client:
        fix_id = proposal_id(client.post("/api/chat", json={"message": "Arréglalo"}))
        url = f"/api/proposals/{fix_id}/pull-request"
        response = client.post(url)
        toolbox.outcomes["open_pull_request"] = PR_OPENED
        retried = client.post(url)

    assert response.status_code == 409
    assert response.json() == {"detail": "El fichero ha cambiado en main desde la propuesta."}
    assert retried.json() == PR


# -- recovery watch -----------------------------------------------------------------------------


def opened_pr_app(recovery: RecoveryDeps):
    toolbox = FakeToolBox(propose_fix=PROPOSED, open_pull_request=PR_OPENED)
    return make_app(toolbox, ScriptedLLM(*propose_script()), recovery=recovery)


def open_pr(client: TestClient) -> str:
    fix_id = proposal_id(client.post("/api/chat", json={"message": "Arréglalo"}))
    client.post(f"/api/proposals/{fix_id}/pull-request")
    return fix_id


def test_watch_streams_recovery_updates():
    seen = []

    def get_pr(number):
        seen.append(("pr", number))
        return {"number": number, "state": "closed", "merged": True, "merge_commit_sha": MERGE_SHA}

    def get_app(environment):
        seen.append(("app", environment))
        return {"revision": MERGE_SHA, "sync_status": "Synced", "health_status": "Healthy"}

    def get_status(environment):
        seen.append(("status", environment))
        return EnvironmentStatus(environment, "sample-api-dev", Health.HEALTHY, 1, 1, "1 de 1")

    recovery = RecoveryDeps(get_pr, get_app, get_status, poll_seconds=0)
    with TestClient(opened_pr_app(recovery)) as client:
        fix_id = open_pr(client)
        response = client.get(f"/api/proposals/{fix_id}/watch")

    assert sse(response) == [
        (
            "recovery",
            {
                "stage": "merged",
                "message": "PR #7 fusionado. Esperando a que Argo CD lo aplique…",
                "done": False,
            },
        ),
        (
            "recovery",
            {"stage": "synced", "message": "Argo CD ha aplicado el commit 9f8e7d6.", "done": False},
        ),
        (
            "recovery",
            {
                "stage": "healthy",
                "message": "Resuelto. Argo CD ha aplicado el cambio y dev vuelve a estar sano.",
                "done": True,
            },
        ),
    ]
    assert seen == [("pr", 7), ("app", "dev"), ("status", "dev")]


def test_watch_needs_an_opened_pr():
    toolbox = FakeToolBox(propose_fix=PROPOSED)
    with TestClient(make_app(toolbox, ScriptedLLM(*propose_script()))) as client:
        fix_id = proposal_id(client.post("/api/chat", json={"message": "Arréglalo"}))
        not_opened = client.get(f"/api/proposals/{fix_id}/watch")
        unknown = client.get("/api/proposals/nope/watch")

    assert not_opened.status_code == 409
    assert not_opened.json() == {"detail": "Esta propuesta todavía no tiene Pull Request"}
    assert unknown.status_code == 404


def test_a_watch_that_breaks_still_ends_with_a_final_update():
    def broken(_number):
        raise KeyError("merge_commit_sha")

    with TestClient(opened_pr_app(RecoveryDeps(broken, never, never))) as client:
        fix_id = open_pr(client)
        response = client.get(f"/api/proposals/{fix_id}/watch")

    assert sse(response) == [
        (
            "recovery",
            {
                "stage": "error",
                "message": "He dejado de vigilar por un error inesperado (KeyError). "
                "Mira la terminal donde lo arrancaste.",
                "done": True,
            },
        )
    ]
