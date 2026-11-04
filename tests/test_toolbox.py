from factories import FakeCoreApi
from mcp import Client
from mcp.server.mcpserver import MCPServer
from servers import server_with
from urllib3.exceptions import MaxRetryError

from oncall_agent.kube import KubeClients
from oncall_agent.toolbox import ToolBox, connect_toolbox

READ_ONLY_TOOLS = [
    "get_environment_status",
    "get_argocd_app",
    "get_deployment",
    "list_pods",
    "get_pod_events",
    "list_recent_commits",
    "get_commit_diff",
    "read_manifest",
    "propose_fix",
    "get_pull_request",
]


def cluster_down() -> KubeClients:
    return KubeClients(core=FakeCoreApi(error=MaxRetryError(None, "/api")), apps=None, custom=None)


async def test_model_tools_filters_by_read_only_hint():
    async with Client(server_with()) as client:
        toolbox = ToolBox(client)
        tools = await toolbox.refresh()

    assert len(tools) == 11
    assert next(t for t in tools if t.name == "open_pull_request").read_only is False
    assert [t["name"] for t in toolbox.model_tools()] == READ_ONLY_TOOLS
    assert all(set(t) == {"name", "description", "input_schema"} for t in toolbox.model_tools())


async def test_a_tool_that_does_not_declare_itself_read_only_is_never_offered():
    server = MCPServer("bare")

    @server.tool()
    def ping() -> str:
        """Answers pong."""
        return "pong"

    async with Client(server) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()

    assert toolbox.model_tools() == []


async def test_call_returns_the_structured_data():
    async with Client(server_with()) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()
        outcome = await toolbox.call("list_pods", {"environment": "dev"})

    assert outcome.ok
    assert outcome.data["summary"] == "1 de 1 pods listos"


async def test_a_failing_tool_keeps_only_the_spanish_message():
    async with Client(server_with(clients_factory=cluster_down)) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()
        outcome = await toolbox.call("list_pods", {"environment": "dev"})

    assert not outcome.ok
    assert outcome.data is None
    assert outcome.text == "No puedo conectar con el clúster: ¿está Docker arrancado?"


async def test_an_unknown_tool_is_an_error_not_a_crash():
    async with Client(server_with()) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()
        outcome = await toolbox.call("no_such_tool", {})

    assert not outcome.ok


async def test_title_is_the_spanish_title_or_else_the_name():
    async with Client(server_with()) as client:
        toolbox = ToolBox(client)
        await toolbox.refresh()

    assert toolbox.title("list_pods") == "Ver pods"
    assert toolbox.title("no_such_tool") == "no_such_tool"


async def test_connect_toolbox_starts_the_real_server_over_stdio():
    async with connect_toolbox() as toolbox:
        names = [t["name"] for t in toolbox.model_tools()]

    assert names == READ_ONLY_TOOLS
