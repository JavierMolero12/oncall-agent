"""MCP server that exposes the agent's tools. Run it with: python -m oncall_agent.mcp_server

The chat backend starts this as a subprocess and talks to it over stdio. Any MCP client
(Claude Code, the MCP Inspector) can use the same tools.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from oncall_agent.config import Settings, load_settings
from oncall_agent.github import GitHubClient, GitHubError
from oncall_agent.kube import ClusterUnavailable, KubeClients, kube_clients
from oncall_agent.status import get_environment_status as environment_status
from oncall_agent.tools import cluster, gitops

READ_ONLY = ToolAnnotations(read_only_hint=True)
OPENS_PR = ToolAnnotations(read_only_hint=False, destructive_hint=False)

AgentEnvironment = Literal["dev", "prod"]
PanelEnvironment = Literal["dev", "prod", "monitoring"]
Result = dict[str, Any]


def guarded(call: Callable[[], Result]) -> Result:
    """Run a tool; failures we expect reach the model as a Spanish error message."""
    try:
        return call()
    except (ClusterUnavailable, GitHubError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


def build_server(
    settings: Settings | None = None,
    clients_factory: Callable[[], KubeClients] | None = None,
    github_factory: Callable[[], GitHubClient] | None = None,
) -> MCPServer:
    settings = settings or load_settings()
    # Built on first use, so the server starts even with Docker stopped.
    clients = clients_factory or (lambda: kube_clients(settings))
    github = github_factory or functools.cache(
        lambda: GitHubClient(settings.gitops_repo, settings.github_token)
    )
    server = MCPServer("oncall-agent")

    # -- cluster ------------------------------------------------------------------------------
    @server.tool(title="Ver estado del entorno", annotations=READ_ONLY)
    def get_environment_status(environment: PanelEnvironment) -> Result:
        """Health of one environment as the live panel shows it (Sano / Actualización atascada /
        Con fallos), computed from its pods. A quick first look."""

        def run() -> Result:
            st = environment_status(environment, clients())
            return {
                "environment": st.environment,
                "namespace": st.namespace,
                "health": str(st.health),
                "ready_pods": st.ready_pods,
                "total_pods": st.total_pods,
                "summary": f"{st.health} · {st.detail}",
            }

        return guarded(run)

    @server.tool(title="Consultar Argo CD", annotations=READ_ONLY)
    def get_argocd_app(environment: AgentEnvironment) -> Result:
        """Argo CD sync status, health and applied Git revision of the environment's Application.
        Synced + Degraded/Progressing means Git was applied but the app does not work."""
        return guarded(lambda: cluster.get_argocd_app(environment, clients(), settings))

    @server.tool(title="Ver despliegue", annotations=READ_ONLY)
    def get_deployment(environment: AgentEnvironment) -> Result:
        """The sample-api Deployment: the image it asks for, replica counts, whether the rollout
        finished, and its conditions."""
        return guarded(lambda: cluster.get_deployment(environment, clients()))

    @server.tool(title="Ver pods", annotations=READ_ONLY)
    def list_pods(environment: AgentEnvironment) -> Result:
        """sample-api pods with readiness, restarts, image and failure reason (ImagePullBackOff,
        CrashLoopBackOff, OOMKilled...)."""
        return guarded(lambda: cluster.list_pods(environment, clients()))

    @server.tool(title="Leer eventos del pod", annotations=READ_ONLY)
    def get_pod_events(environment: AgentEnvironment, pod_name: str) -> Result:
        """Recent Kubernetes events of one pod, newest first. They say in words why a pod cannot
        start, for example an image that cannot be pulled."""
        return guarded(lambda: cluster.get_pod_events(environment, pod_name, clients()))

    # -- git ----------------------------------------------------------------------------------
    @server.tool(title="Revisar historial de Git", annotations=READ_ONLY)
    def list_recent_commits(path: str, limit: int = 5) -> Result:
        """Recent gitops-manifests commits that touched a path, such as apps/overlays/dev.
        In GitOps every change enters through Git, so the latest commit is the first suspect."""
        return guarded(lambda: gitops.list_recent_commits(path, github(), limit=min(limit, 20)))

    @server.tool(title="Ver cambios de un commit", annotations=READ_ONLY)
    def get_commit_diff(sha: str) -> Result:
        """The diff of one gitops-manifests commit: which lines changed and how."""
        return guarded(lambda: gitops.get_commit_diff(sha, github()))

    @server.tool(title="Leer manifiesto", annotations=READ_ONLY)
    def read_manifest(path: str) -> Result:
        """Current content of a gitops-manifests file on main, for example
        apps/overlays/dev/kustomization.yaml. Read it before proposing a fix."""
        return guarded(lambda: gitops.read_manifest(path, github(), ref=settings.gitops_branch))

    @server.tool(title="Proponer arreglo", annotations=READ_ONLY)
    def propose_fix(file_path: str, new_content: str, title: str, explanation: str) -> Result:
        """Prepare a fix for ONE file under apps/overlays/dev/. Pass the COMPLETE new content of
        the file, a short title and the explanation in Spanish. It applies nothing: it returns a
        diff for a person to review, and only that person can open the pull request."""
        return guarded(
            lambda: gitops.propose_fix(file_path, new_content, title, explanation, github())
        )

    @server.tool(title="Consultar Pull Request", annotations=READ_ONLY)
    def get_pull_request(number: int) -> Result:
        """State of a gitops-manifests pull request: open, merged or closed."""

        def run() -> Result:
            pr = github().get_pull_request(number)
            state = "fusionado" if pr["merged"] else {"open": "abierto"}.get(pr["state"], "cerrado")
            return {**pr, "summary": f"PR #{pr['number']} · {state}"}

        return guarded(run)

    # -- the only write -------------------------------------------------------------------------
    @server.tool(title="Abrir Pull Request", annotations=OPENS_PR)
    def open_pull_request(
        file_path: str, new_content: str, title: str, explanation: str, base_blob_sha: str
    ) -> Result:
        """Open the pull request for a proposal made with propose_fix. The web app calls this
        after a person presses the button; the agent's model is never given this tool."""
        if not settings.github_token:
            raise ToolError("Falta GITHUB_TOKEN en el fichero .env")
        return guarded(
            lambda: gitops.open_pull_request(
                file_path,
                new_content,
                title,
                explanation,
                base_blob_sha,
                github(),
                base=settings.gitops_branch,
            )
        )

    return server


def main() -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per request is noise
    build_server().run()


if __name__ == "__main__":
    main()
