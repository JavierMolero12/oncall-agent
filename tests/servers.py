"""The real MCP server from task 7, in memory, wired to fake Kubernetes and fake GitHub."""

from factories import FakeAppsApi, FakeCoreApi, FakeCustomApi, make_pod
from github_fakes import REPO, FakeGitHub

from oncall_agent.config import load_settings
from oncall_agent.github import GitHubClient
from oncall_agent.kube import KubeClients
from oncall_agent.mcp_server import build_server


def healthy_clients() -> KubeClients:
    core = FakeCoreApi({"sample-api-dev": [make_pod("sample-api-a")]})
    return KubeClients(core=core, apps=FakeAppsApi(), custom=FakeCustomApi())


def server_with(clients_factory=healthy_clients, github=None, token="t"):
    settings = load_settings({"GITHUB_TOKEN": token} if token else {})
    fake = github or FakeGitHub()
    return build_server(
        settings,
        clients_factory=clients_factory,
        github_factory=lambda: GitHubClient(REPO, token, transport=fake.transport()),
    )
