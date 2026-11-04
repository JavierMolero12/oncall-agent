import json
import re

import pytest
from github_fakes import FakeGitHub
from llm_fakes import ScriptedLLM, connection_error, refusal, reply, text, thinking, tool_use
from mcp import Client
from servers import server_with
from toolbox_fakes import FIX, PODS_OK, FakeToolBox

from oncall_agent.agent import (
    SYSTEM_PROMPT,
    AgentError,
    AssistantMessage,
    Conversation,
    FixProposed,
    StepFinished,
    StepStarted,
    ask,
    run_turn,
)
from oncall_agent.config import load_settings
from oncall_agent.toolbox import ToolBox, ToolOutcome

MODEL = "claude-sonnet-5-5"
LIST_DEV = tool_use("list_pods", {"environment": "dev"})


async def run(conversation, question, toolbox, llm, max_steps=15) -> list:
    return [
        event async for event in run_turn(conversation, question, toolbox, llm, MODEL, max_steps)
    ]


def last_tool_results(llm: ScriptedLLM) -> list[dict]:
    return llm.calls[-1]["messages"][-1]["content"]


async def test_model_only_receives_read_only_tools():
    llm = ScriptedLLM(reply(text("Todo bien.")))
    async with Client(server_with()) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()
        await run(Conversation(), "¿Cómo está dev?", toolbox, llm)

    names = [tool["name"] for tool in llm.calls[0]["tools"]]
    assert "open_pull_request" not in names
    assert {"list_pods", "read_manifest", "propose_fix"} <= set(names)


async def test_model_cannot_call_a_tool_it_was_not_given():
    github = FakeGitHub({FIX["file_path"]: "x: 1\n"})
    sneaky = tool_use(
        "open_pull_request",
        {
            "file_path": FIX["file_path"],
            "new_content": "x: 2\n",
            "title": "t",
            "explanation": "e",
            "base_blob_sha": "abc",
        },
    )
    llm = ScriptedLLM(reply(sneaky, stop_reason="tool_use"), reply(text("No puedo abrirlo.")))
    async with Client(server_with(github=github)) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()
        events = await run(Conversation(), "Abre el PR", toolbox, llm)

    assert github.requests == []  # nothing reached GitHub
    assert not next(e for e in events if isinstance(e, StepFinished)).ok
    [result] = last_tool_results(llm)
    assert result["is_error"] is True
    assert "no está disponible" in result["content"]


async def test_tool_steps_emit_started_and_finished_with_spanish_title():
    llm = ScriptedLLM(
        reply(thinking(), LIST_DEV, stop_reason="tool_use"),
        reply(thinking(), text("El pod está sano.")),
    )
    events = await run(Conversation(), "¿Cómo está dev?", FakeToolBox(list_pods=PODS_OK), llm)

    assert events == [
        StepStarted(index=1, tool="list_pods", title="Ver pods", arguments={"environment": "dev"}),
        StepFinished(
            index=1, tool="list_pods", title="Ver pods", ok=True, summary="1 de 1 pods listos"
        ),
        AssistantMessage(text="El pod está sano."),
    ]


async def test_final_text_is_emitted_and_history_kept():
    conversation = Conversation()
    toolbox = FakeToolBox(list_pods=PODS_OK)
    first = ScriptedLLM(
        reply(tool_use("list_pods", {"environment": "dev"}, id="toolu_9"), stop_reason="tool_use"),
        reply(text("Dev está sano.")),
    )
    await run(conversation, "¿Cómo está dev?", toolbox, first)

    question, call, results, answer = conversation.messages
    assert question == {"role": "user", "content": "¿Cómo está dev?"}
    assert call["role"] == "assistant"
    assert call["content"][0].name == "list_pods"
    assert results["role"] == "user"
    [result] = results["content"]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "toolu_9"
    assert result["is_error"] is False
    assert json.loads(result["content"])["summary"] == "1 de 1 pods listos"
    assert answer["role"] == "assistant"

    second = ScriptedLLM(reply(text("Sigue sano.")))
    await run(conversation, "¿Y ahora?", toolbox, second)
    sent = second.calls[0]["messages"]
    assert sent[:4] == conversation.messages[:4]
    assert sent[4] == {"role": "user", "content": "¿Y ahora?"}


async def test_tool_errors_reach_the_model_marked_as_errors():
    down = ToolOutcome(ok=False, data=None, text="No puedo conectar con el clúster: ¿está Docker?")
    llm = ScriptedLLM(reply(LIST_DEV, stop_reason="tool_use"), reply(text("No responde.")))
    events = await run(Conversation(), "¿Cómo está dev?", FakeToolBox(list_pods=down), llm)

    assert events[1] == StepFinished(
        index=1, tool="list_pods", title="Ver pods", ok=False, summary=down.text
    )
    [result] = last_tool_results(llm)
    assert result["is_error"] is True
    assert result["content"] == down.text


async def test_long_tool_results_are_clipped_for_the_model():
    huge = ToolOutcome(ok=True, data={"content": "x" * 20000, "summary": "Leído"}, text="")
    llm = ScriptedLLM(reply(LIST_DEV, stop_reason="tool_use"), reply(text("Hecho.")))
    await run(Conversation(), "Lee", FakeToolBox(list_pods=huge), llm)

    [result] = last_tool_results(llm)
    assert len(result["content"]) < 8100
    assert result["content"].endswith("[recortado]")


async def test_propose_fix_creates_proposal_event():
    conversation = Conversation()
    arguments = {key: FIX[key] for key in ("file_path", "new_content", "title", "explanation")}
    llm = ScriptedLLM(
        reply(tool_use("propose_fix", arguments), stop_reason="tool_use"),
        reply(text("Revisa el cambio y pulsa el botón.")),
    )
    toolbox = FakeToolBox(propose_fix=ToolOutcome(ok=True, data=FIX, text="{}"))
    events = await run(conversation, "Arréglalo", toolbox, llm)

    assert [type(e) for e in events] == [StepStarted, StepFinished, FixProposed, AssistantMessage]
    proposal = events[2].proposal
    assert re.fullmatch(r"[0-9a-f]{8}", proposal.id)
    assert conversation.proposals == {proposal.id: proposal}
    assert proposal.file_path == FIX["file_path"]
    assert proposal.new_content == FIX["new_content"]
    assert proposal.diff == FIX["diff"]
    assert proposal.base_blob_sha == FIX["base_blob_sha"]
    assert proposal.pr is None


async def test_stops_at_max_steps_with_spanish_message():
    conversation = Conversation()
    endless = [
        reply(
            tool_use("list_pods", {"environment": "dev"}, id=f"toolu_{i}"), stop_reason="tool_use"
        )
        for i in range(3)
    ]
    llm = ScriptedLLM(*endless)
    events = await run(conversation, "¿Qué pasa?", FakeToolBox(list_pods=PODS_OK), llm, max_steps=3)

    assert len(llm.calls) == 3
    assert isinstance(events[-1], AssistantMessage)
    assert events[-1].text.startswith("He llegado al límite de 3 pasos sin una conclusión segura.")
    # The history still alternates user/assistant, so the next question can go on.
    assert conversation.messages[-2]["content"][0]["type"] == "tool_result"
    assert conversation.messages[-1]["role"] == "assistant"


async def test_llm_failure_rolls_back_conversation():
    conversation = Conversation()
    toolbox = FakeToolBox(list_pods=PODS_OK)
    broken = ScriptedLLM(reply(LIST_DEV, stop_reason="tool_use"), connection_error())
    events = await run(conversation, "¿Cómo está dev?", toolbox, broken)

    assert isinstance(events[-1], AgentError)
    assert events[-1].message.startswith("No he podido hablar con el modelo: ")
    assert conversation.messages == []

    working = ScriptedLLM(reply(text("Dev está sano.")))
    events = await run(conversation, "¿Cómo está dev?", toolbox, working)
    assert events == [AssistantMessage(text="Dev está sano.")]
    assert working.calls[0]["messages"] == [{"role": "user", "content": "¿Cómo está dev?"}]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (refusal("cyber"), "ha rechazado"),
        (reply(text("El pod está en"), stop_reason="max_tokens"), "se ha cortado"),
    ],
)
async def test_an_unfinished_answer_ends_the_turn_and_leaves_history_as_it_was(response, expected):
    conversation = Conversation()
    events = await run(conversation, "¿Cómo está dev?", FakeToolBox(), ScriptedLLM(response))

    [error] = events
    assert isinstance(error, AgentError)
    assert expected in error.message
    assert conversation.messages == []


async def test_closing_the_stream_mid_turn_rolls_back():
    """If the browser goes away in the middle of an answer, the half-done turn is not kept."""
    conversation = Conversation()
    llm = ScriptedLLM(reply(LIST_DEV, stop_reason="tool_use"), reply(text("Sano.")))
    stream = run_turn(conversation, "¿Dev?", FakeToolBox(list_pods=PODS_OK), llm, MODEL, 15)

    assert isinstance(await anext(stream), StepStarted)
    await stream.aclose()
    assert conversation.messages == []


async def test_requests_only_ever_append_to_history():
    """Thinking blocks stay valid only if system, tools and earlier messages never change."""
    conversation = Conversation()
    toolbox = FakeToolBox(list_pods=PODS_OK)
    llm = ScriptedLLM(
        reply(thinking(), LIST_DEV, stop_reason="tool_use"),
        reply(thinking(), text("Dev está sano.")),
        reply(
            thinking(),
            tool_use("list_pods", {"environment": "prod"}, id="t2"),
            stop_reason="tool_use",
        ),
        reply(text("Prod también.")),
    )
    await run(conversation, "¿Dev?", toolbox, llm)
    await run(conversation, "¿Y prod?", toolbox, llm)

    for before, after in zip(llm.calls, llm.calls[1:]):
        assert after["system"] == before["system"]
        assert after["tools"] == before["tools"]
        assert after["messages"][: len(before["messages"])] == before["messages"]


async def test_request_uses_adaptive_thinking_caching_and_refusal_fallback():
    llm = ScriptedLLM(reply(text("Hola.")))
    await run(Conversation(), "Hola", FakeToolBox(), llm)

    request = llm.calls[0]
    assert request["model"] == MODEL
    assert request["system"] == SYSTEM_PROMPT
    assert request["max_tokens"] == 16000
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "medium"}
    assert request["cache_control"] == {"type": "ephemeral"}
    assert request["fallbacks"] == "default"
    assert request["betas"] == ["server-side-fallback-2026-07-01"]


def test_system_prompt_keeps_the_safety_rules():
    for rule in (
        "español",
        "evidencia",
        "propose_fix",
        "apps/overlays/dev/",
        "read_manifest",
        "Abrir Pull Request con el arreglo",
    ):
        assert rule in SYSTEM_PROMPT


async def test_ask_without_api_key_says_what_is_missing(capsys):
    await ask("¿Cómo está dev?", settings=load_settings({}))

    assert capsys.readouterr().out.strip() == "Falta ANTHROPIC_API_KEY en el fichero .env"
