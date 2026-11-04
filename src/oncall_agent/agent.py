"""The agent loop, written by hand: ask the model, run the tools it asks for, repeat.

Try it from a terminal (needs ANTHROPIC_API_KEY in .env and the cluster running):
    python -m oncall_agent.agent "¿Cómo está sample-api en dev?"
"""

import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anthropic

from oncall_agent.config import Settings, load_settings
from oncall_agent.toolbox import ToolBox, ToolOutcome, connect_toolbox

MAX_TOKENS = 16000  # thinking counts towards it, so leave room for it and the answer
EFFORT = "medium"  # Sonnet 5.5's starting point for multistep tool use
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # retry some refusals on another model
MAX_TOOL_RESULT = 8000  # characters of each tool result that the model reads
MAX_SUMMARY = 300  # characters of an error shown in the steps panel

SYSTEM_PROMPT = """\
Eres el agente de guardia del clúster de Kubernetes «gitops-demo». Ayudas a entender por qué \
falla la aplicación sample-api y a preparar el arreglo.

Contexto de la plataforma:
- Clúster k3d local con Argo CD. Todo se despliega con GitOps desde el repositorio \
gitops-manifests: Argo CD aplica lo que hay en la rama main y deshace los cambios hechos a mano.
- sample-api tiene dos entornos: dev (namespace sample-api-dev, 1 réplica, overlay \
apps/overlays/dev) y prod (namespace sample-api-prod, 3 réplicas con autoescalado, overlay \
apps/overlays/prod).
- La imagen de cada entorno se fija con newTag en el kustomization.yaml de su overlay. La CI de \
sample-api reescribe ese newTag cuando despliega una versión nueva.
- Argo CD «Sincronizado» con salud «Degradado» o «En progreso» significa que Git se aplicó, pero \
la aplicación no funciona.

Cómo trabajas:
- Responde siempre en español.
- Investiga con las herramientas antes de afirmar nada. No inventes nombres de pods, imágenes ni \
commits: usa los que devuelven las herramientas.
- Cita la evidencia de cada conclusión: qué herramienta lo mostró y el dato concreto (el motivo \
del pod, el evento, la línea que cambió en el commit).
- Si no hay evidencia suficiente, dilo claramente y explica qué falta. Si una herramienta falla, \
dilo y no supongas su resultado.
- Compara dev con prod cuando sirva para encontrar la última versión que funciona.

Arreglos:
- Solo propones arreglos con la herramienta propose_fix y solo en ficheros de apps/overlays/dev/. \
Nunca propongas cambios en prod.
- Antes de proponer, lee el fichero con read_manifest y pasa a propose_fix su contenido COMPLETO \
con solo el cambio necesario, conservando los comentarios y las comillas.
- Tú no aplicas nada ni abres Pull Requests. Nunca digas que has aplicado, desplegado o arreglado \
algo: una persona revisa la propuesta, pulsa el botón y fusiona el PR en GitHub.
- Si te piden abrir el PR, contesta que pulsen el botón «Abrir Pull Request con el arreglo» que \
aparece bajo la propuesta.

Formato: respuestas cortas y directas, **negrita** para lo importante, `código` para los nombres \
técnicos y listas con «-».
"""


@dataclass
class Proposal:
    id: str
    file_path: str
    new_content: str
    title: str
    explanation: str
    diff: str
    base_blob_sha: str
    pr: dict | None = None


@dataclass
class Conversation:
    messages: list[dict[str, Any]] = field(default_factory=list)
    proposals: dict[str, Proposal] = field(default_factory=dict)


@dataclass(frozen=True)
class StepStarted:
    index: int
    tool: str
    title: str
    arguments: dict


@dataclass(frozen=True)
class StepFinished:
    index: int
    tool: str
    title: str
    ok: bool
    summary: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str


@dataclass(frozen=True)
class FixProposed:
    proposal: Proposal


@dataclass(frozen=True)
class AgentError:
    message: str


AgentEvent = StepStarted | StepFinished | AssistantMessage | FixProposed | AgentError


async def run_turn(
    conversation: Conversation,
    user_text: str,
    toolbox: ToolBox,
    llm: anthropic.AsyncAnthropic,
    model: str,
    max_steps: int,
) -> AsyncIterator[AgentEvent]:
    """Answer one question. The history only ever grows; a turn that fails is removed whole."""
    history = conversation.messages
    start = len(history)
    kept = False  # True once the turn ends in an answer the history can keep
    tools = toolbox.model_tools()  # the same list on every request, so the cache keeps working
    allowed = {tool["name"] for tool in tools}
    history.append({"role": "user", "content": user_text})
    step = 0
    try:
        for _ in range(max_steps):
            try:
                response = await llm.beta.messages.create(
                    model=model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=history,
                    thinking={"type": "adaptive"},
                    output_config={"effort": EFFORT},
                    cache_control={"type": "ephemeral"},
                    betas=[FALLBACK_BETA],
                    fallbacks="default",
                )
            except anthropic.APIError as exc:
                del history[start:]
                yield AgentError(f"No he podido hablar con el modelo: {_llm_problem(exc, model)}")
                return

            if response.stop_reason not in ("tool_use", "end_turn", "stop_sequence"):
                del history[start:]
                yield AgentError(_unfinished(response))
                return
            # The whole content goes back unchanged, thinking blocks included.
            history.append({"role": "assistant", "content": response.content})
            calls = [block for block in response.content if block.type == "tool_use"]
            if response.stop_reason != "tool_use" or not calls:
                kept = True
                yield AssistantMessage(_text_of(response.content))
                return

            results = []
            for call in calls:
                step += 1
                title = toolbox.title(call.name)
                yield StepStarted(step, call.name, title, dict(call.input))
                if call.name in allowed:
                    outcome = await toolbox.call(call.name, call.input)
                else:  # the model asked for a tool it was never given, e.g. open_pull_request
                    names = ", ".join(sorted(allowed))
                    message = f"La herramienta «{call.name}» no está disponible. Usa solo: {names}."
                    outcome = ToolOutcome(ok=False, data=None, text=message)
                yield StepFinished(step, call.name, title, outcome.ok, _summary(outcome))
                if call.name == "propose_fix" and outcome.ok and outcome.data:
                    proposal = _proposal(outcome.data)
                    conversation.proposals[proposal.id] = proposal
                    yield FixProposed(proposal)
                results.append(_tool_result(call.id, outcome))
            history.append({"role": "user", "content": results})

        limit = (
            f"He llegado al límite de {max_steps} pasos sin una conclusión segura. "
            "Revisa los pasos que he dado o hazme una pregunta más concreta."
        )
        history.append({"role": "assistant", "content": limit})  # keeps user/assistant turns
        kept = True
        yield AssistantMessage(limit)
    finally:
        if not kept:  # an error, or the reader stopped listening half-way
            del history[start:]


def _text_of(content: list) -> str:
    parts = [block.text.strip() for block in content if block.type == "text" and block.text.strip()]
    return "\n\n".join(parts) or "(El modelo no ha devuelto ningún texto.)"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "… [recortado]"


def _summary(outcome: ToolOutcome) -> str:
    summary = outcome.data.get("summary") if outcome.ok and outcome.data else None
    if isinstance(summary, str):
        return summary
    return _clip(outcome.text or ("Hecho" if outcome.ok else "Error"), MAX_SUMMARY)


def _tool_result(tool_use_id: str, outcome: ToolOutcome) -> dict[str, Any]:
    if outcome.ok and outcome.data is not None:
        content = json.dumps(outcome.data, ensure_ascii=False)
    else:
        content = outcome.text
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": _clip(content or "(sin resultado)", MAX_TOOL_RESULT),
        "is_error": not outcome.ok,
    }


def _proposal(data: dict[str, Any]) -> Proposal:
    return Proposal(
        id=uuid.uuid4().hex[:8],
        file_path=data["file_path"],
        new_content=data["new_content"],
        title=data["title"],
        explanation=data["explanation"],
        diff=data["diff"],
        base_blob_sha=data["base_blob_sha"],
    )


def _unfinished(response: Any) -> str:
    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        why = f" (motivo: {category})" if category else ""
        return (
            f"El modelo ha rechazado responder{why}. Prueba a formular la pregunta de otra forma."
        )
    if response.stop_reason == "max_tokens":
        return (
            "La respuesta del modelo se ha cortado por larga. Prueba con una pregunta más concreta."
        )
    return f"El modelo se ha detenido sin terminar la respuesta ({response.stop_reason})."


def _llm_problem(exc: anthropic.APIError, model: str) -> str:
    # Most specific first. The SDK has already retried 429, 5xx and network errors twice.
    if isinstance(exc, anthropic.AuthenticationError):
        return "la clave ANTHROPIC_API_KEY no es válida"
    if isinstance(exc, anthropic.NotFoundError):
        return f"el modelo «{model}» no existe o la cuenta no tiene acceso (revisa ONCALL_MODEL)"
    if isinstance(exc, anthropic.RateLimitError):
        return "se ha superado el límite de peticiones; espera un minuto y vuelve a probar"
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 402:
            return "la cuenta de Anthropic no tiene saldo"
        if exc.status_code >= 500:
            return f"la API de Anthropic tiene un problema temporal ({exc.status_code})"
        return f"la API respondió {exc.status_code}: {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):  # timeouts included
        return "no hay conexión con la API de Anthropic"
    return str(exc)


# -- manual test from a terminal ------------------------------------------------------------------


def describe(event: AgentEvent) -> str:
    match event:
        case StepStarted():
            return f"-> {event.title} {event.arguments}"
        case StepFinished():
            return f"   {'OK' if event.ok else 'ERROR'}: {event.summary}"
        case FixProposed():
            return f"Propuesta {event.proposal.id}:\n{event.proposal.diff}"
        case AssistantMessage():
            return f"\n{event.text}"
        case AgentError():
            return f"Error: {event.message}"


async def ask(question: str, settings: Settings | None = None) -> None:
    settings = settings or load_settings()
    if not settings.anthropic_api_key:
        print("Falta ANTHROPIC_API_KEY en el fichero .env")
        return
    async with (
        anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key) as llm,
        connect_toolbox() as toolbox,
    ):
        turn = run_turn(Conversation(), question, toolbox, llm, settings.model, settings.max_steps)
        async for event in turn:
            print(describe(event))


if __name__ == "__main__":
    asyncio.run(ask(" ".join(sys.argv[1:]) or "¿Cómo está sample-api en dev?"))
