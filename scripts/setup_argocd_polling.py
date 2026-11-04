"""Make Argo CD check Git every 30 s instead of every ~3 min, so the demo does not wait.

Run it once after creating the cluster:

    python scripts/setup_argocd_polling.py

Argo CD keeps this setting in the ConfigMap argocd-cm (key timeout.reconciliation).
It is cluster setup, like installing Argo CD itself, so it is written straight to the
cluster and not to gitops-manifests. The application controller and the repo server
only read it when they start, so both are restarted, as `kubectl rollout restart` does.
"""

import sys
from collections.abc import Callable
from datetime import UTC, datetime

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError as Urllib3Error

from oncall_agent.config import load_settings
from oncall_agent.kube import reachable_host

NAMESPACE = "argocd"
CONFIG_MAP = "argocd-cm"
KEY = "timeout.reconciliation"
VALUE = "30s"
CONTROLLER = "argocd-application-controller"  # a StatefulSet
REPO_SERVER = "argocd-repo-server"  # a Deployment
# Given a dict body, this client would pick JSON Patch (the first type it lists),
# which expects a list of operations. A merge patch is the dict we mean.
MERGE_PATCH = "application/merge-patch+json"


def restart_patch(when: datetime) -> dict:
    """What `kubectl rollout restart` sends: a new annotation on the pod template."""
    stamp = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    annotations = {"kubectl.kubernetes.io/restartedAt": stamp}
    return {"spec": {"template": {"metadata": {"annotations": annotations}}}}


def setup_polling(core, apps, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> str:
    """Store 30 s in argocd-cm, restart the two parts that read it, return the stored value."""
    core.patch_namespaced_config_map(
        CONFIG_MAP, NAMESPACE, {"data": {KEY: VALUE}}, _content_type=MERGE_PATCH
    )
    restart = restart_patch(now())
    apps.patch_namespaced_stateful_set(CONTROLLER, NAMESPACE, restart, _content_type=MERGE_PATCH)
    apps.patch_namespaced_deployment(REPO_SERVER, NAMESPACE, restart, _content_type=MERGE_PATCH)
    stored = core.read_namespaced_config_map(CONFIG_MAP, NAMESPACE)
    return (stored.data or {}).get(KEY, "")


def main() -> int:
    settings = load_settings()
    try:
        api_client = config.new_client_from_config(context=settings.kube_context)
    except (config.ConfigException, OSError) as exc:
        print(f"No puedo cargar el contexto «{settings.kube_context}» del kubeconfig: {exc}")
        return 1
    api_client.configuration.host = reachable_host(api_client.configuration.host)
    try:
        value = setup_polling(client.CoreV1Api(api_client), client.AppsV1Api(api_client))
    except (ApiException, Urllib3Error) as exc:
        print(f"No he podido cambiar Argo CD: {exc}")
        return 1
    print(f"argocd-cm {KEY} = {value}")
    print(f"He reiniciado {CONTROLLER} y {REPO_SERVER} para que lean el nuevo valor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
