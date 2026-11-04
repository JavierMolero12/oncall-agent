import json

from factories import FakeCoreApi
from mcp import Client
from servers import server_with
from urllib3.exceptions import MaxRetryError

from oncall_agent.kube import KubeClients

EXPECTED = {
    "get_environment_status": "Ver estado del entorno",
    "get_argocd_app": "Consultar Argo CD",
    "get_deployment": "Ver despliegue",
    "list_pods": "Ver pods",
    "get_pod_events": "Leer eventos del pod",
    "list_recent_commits": "Revisar historial de Git",
    "get_commit_diff": "Ver cambios de un commit",
    "read_manifest": "Leer manifiesto",
    "propose_fix": "Proponer arreglo",
    "get_pull_request": "Consultar Pull Request",
    "open_pull_request": "Abrir Pull Request",
}


def text_of(result) -> str:
    return "".join(block.text for block in result.content)


async def test_lists_all_tools_with_spanish_titles():
    async with Client(server_with()) as client:
        tools = (await client.list_tools()).tools
    assert {t.name: t.title for t in tools} == EXPECTED
    assert all(t.description for t in tools)


async def test_only_open_pull_request_is_not_read_only():
    async with Client(server_with()) as client:
        tools = (await client.list_tools()).tools
    writers = [t.name for t in tools if not t.annotations.read_only_hint]
    assert writers == ["open_pull_request"]


async def test_tool_result_carries_the_summary():
    async with Client(server_with()) as client:
        result = await client.call_tool("list_pods", {"environment": "dev"})
    assert not result.is_error
    assert json.loads(text_of(result))["summary"] == "1 de 1 pods listos"


async def test_tool_error_is_reported_in_spanish():
    def cluster_down() -> KubeClients:
        return KubeClients(
            core=FakeCoreApi(error=MaxRetryError(None, "/api")), apps=None, custom=None
        )

    async with Client(server_with(clients_factory=cluster_down)) as client:
        result = await client.call_tool("list_pods", {"environment": "dev"})
    assert result.is_error
    assert "¿está Docker arrancado?" in text_of(result)


async def test_server_starts_even_if_the_cluster_is_unreachable():
    def explode() -> KubeClients:
        raise AssertionError("clients must only be built when a tool needs them")

    async with Client(server_with(clients_factory=explode)) as client:
        assert len((await client.list_tools()).tools) == len(EXPECTED)


async def test_environment_outside_the_list_is_rejected_by_the_schema():
    async with Client(server_with()) as client:
        result = await client.call_tool("list_pods", {"environment": "monitoring"})
    assert result.is_error


async def test_open_pull_request_needs_a_token():
    async with Client(server_with(token=None)) as client:
        result = await client.call_tool(
            "open_pull_request",
            {
                "file_path": "apps/overlays/dev/kustomization.yaml",
                "new_content": "x: 1\n",
                "title": "t",
                "explanation": "e",
                "base_blob_sha": "abc",
            },
        )
    assert result.is_error
    assert "Falta GITHUB_TOKEN" in text_of(result)
