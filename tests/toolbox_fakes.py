"""A stand-in for ToolBox, shared by the agent tests and the web tests."""

from oncall_agent.toolbox import ToolOutcome

TITLES = {
    "list_pods": "Ver pods",
    "get_pod_events": "Leer eventos del pod",
    "propose_fix": "Proponer arreglo",
}
PODS_OK = ToolOutcome(ok=True, data={"pods": [], "summary": "1 de 1 pods listos"}, text="{}")
FIX = {
    "file_path": "apps/overlays/dev/kustomization.yaml",
    "new_content": 'images:\n  - name: sample-api\n    newTag: "ed01aa4"\n',
    "title": "Volver sample-api dev a ed01aa4",
    "explanation": "La imagen v9.9.9 no existe; ed01aa4 es la que funciona en prod.",
    "diff": '-    newTag: "v9.9.9"\n+    newTag: "ed01aa4"\n',
    "base_blob_sha": "abc123",
    "summary": "Propuesta: cambiar apps/overlays/dev/kustomization.yaml",
}


class FakeToolBox:
    """What the agent needs from a ToolBox: the model's tools, titles and canned outcomes.

    An outcome that is an exception is raised, like a ToolBox whose MCP server has gone away.
    """

    def __init__(self, **outcomes: ToolOutcome | Exception):
        self.outcomes = outcomes
        self.calls: list[tuple[str, dict]] = []

    def model_tools(self) -> list[dict]:
        return [
            {"name": name, "description": f"{name} tool", "input_schema": {"type": "object"}}
            for name in TITLES
        ]

    def title(self, name: str) -> str:
        return TITLES.get(name, name)

    async def call(self, name: str, arguments: dict) -> ToolOutcome:
        self.calls.append((name, arguments))
        outcome = self.outcomes[name]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
