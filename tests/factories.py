"""Builders for real Kubernetes model objects, so tests use the same shapes the cluster returns."""

from datetime import UTC, datetime

from kubernetes.client import models as k

DEFAULT_IMAGE = "ghcr.io/javiermolero12/sample-api:ed01aa4"


def make_pod(
    name: str,
    ready: bool = True,
    waiting_reason: str | None = None,
    last_terminated_reason: str | None = None,
    image: str = DEFAULT_IMAGE,
    deleting: bool = False,
    restarts: int = 0,
    node: str = "k3d-gitops-demo-agent-0",
    phase: str | None = None,
) -> k.V1Pod:
    if waiting_reason:
        state = k.V1ContainerState(waiting=k.V1ContainerStateWaiting(reason=waiting_reason))
    else:
        state = k.V1ContainerState(running=k.V1ContainerStateRunning())

    last_state = k.V1ContainerState()
    if last_terminated_reason:
        last_state = k.V1ContainerState(
            terminated=k.V1ContainerStateTerminated(exit_code=137, reason=last_terminated_reason)
        )

    container_status = k.V1ContainerStatus(
        name="sample-api",
        image=image,
        image_id="",
        ready=ready,
        restart_count=restarts,
        state=state,
        last_state=last_state,
    )
    return k.V1Pod(
        metadata=k.V1ObjectMeta(
            name=name,
            creation_timestamp=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
            deletion_timestamp=datetime.now(UTC) if deleting else None,
        ),
        spec=k.V1PodSpec(
            node_name=node, containers=[k.V1Container(name="sample-api", image=image)]
        ),
        status=k.V1PodStatus(
            phase=phase or ("Running" if ready else "Pending"),
            conditions=[k.V1PodCondition(type="Ready", status="True" if ready else "False")],
            container_statuses=[container_status],
        ),
    )


def make_deployment(
    image: str = DEFAULT_IMAGE,
    replicas: int = 1,
    ready: int = 1,
    updated: int = 1,
    available: int = 1,
    generation: int = 2,
    observed_generation: int = 2,
    conditions: list[tuple[str, str, str, str]] | None = None,
) -> k.V1Deployment:
    """conditions: (type, status, reason, message) tuples."""
    return k.V1Deployment(
        metadata=k.V1ObjectMeta(name="sample-api", generation=generation),
        spec=k.V1DeploymentSpec(
            replicas=replicas,
            selector=k.V1LabelSelector(match_labels={"app": "sample-api"}),
            template=k.V1PodTemplateSpec(
                spec=k.V1PodSpec(containers=[k.V1Container(name="sample-api", image=image)])
            ),
        ),
        status=k.V1DeploymentStatus(
            replicas=max(replicas, updated),
            ready_replicas=ready,
            updated_replicas=updated,
            available_replicas=available,
            observed_generation=observed_generation,
            conditions=[
                k.V1DeploymentCondition(type=t, status=s, reason=r, message=m)
                for t, s, r, m in (conditions or [])
            ],
        ),
    )


def make_argo_app(
    sync: str = "Synced",
    health: str = "Healthy",
    revision: str = "a7f169c0000000000000000000000000000000aa",
    path: str = "apps/overlays/dev",
) -> dict:
    """The shape the Kubernetes API returns for an Argo CD Application (trimmed)."""
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "sample-api-dev", "namespace": "argocd"},
        "spec": {
            "source": {
                "repoURL": "https://github.com/javiermolero12/gitops-manifests.git",
                "path": path,
                "targetRevision": "main",
            }
        },
        "status": {
            "sync": {"status": sync, "revision": revision},
            "health": {"status": health},
        },
    }


def make_event(
    reason: str,
    message: str,
    last_seen: datetime,
    type_: str = "Warning",
    count: int = 1,
    pod: str = "sample-api-new",
) -> k.CoreV1Event:
    return k.CoreV1Event(
        metadata=k.V1ObjectMeta(name=f"{pod}.{reason.lower()}"),
        involved_object=k.V1ObjectReference(kind="Pod", name=pod),
        type=type_,
        reason=reason,
        message=message,
        count=count,
        first_timestamp=last_seen,
        last_timestamp=last_seen,
    )


class FakeCoreApi:
    """Stands in for CoreV1Api: canned pods and events per namespace, or raises `error`."""

    def __init__(
        self,
        pods_by_namespace: dict[str, list[k.V1Pod]] | None = None,
        error=None,
        events_by_namespace: dict[str, list[k.CoreV1Event]] | None = None,
    ):
        self.pods_by_namespace = pods_by_namespace or {}
        self.events_by_namespace = events_by_namespace or {}
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []

    def list_namespaced_pod(self, namespace: str, **kwargs) -> k.V1PodList:
        self.calls.append(("list_namespaced_pod", namespace, kwargs))
        if self.error:
            raise self.error
        return k.V1PodList(items=self.pods_by_namespace.get(namespace, []))

    def list_namespaced_event(self, namespace: str, **kwargs) -> k.CoreV1EventList:
        self.calls.append(("list_namespaced_event", namespace, kwargs))
        if self.error:
            raise self.error
        return k.CoreV1EventList(items=self.events_by_namespace.get(namespace, []))


class FakeAppsApi:
    def __init__(self, deployments_by_namespace: dict[str, k.V1Deployment] | None = None):
        self.deployments_by_namespace = deployments_by_namespace or {}

    def read_namespaced_deployment(self, name: str, namespace: str, **kwargs) -> k.V1Deployment:
        return self.deployments_by_namespace[namespace]


class FakeCustomApi:
    def __init__(self, apps_by_name: dict[str, dict] | None = None):
        self.apps_by_name = apps_by_name or {}
        self.calls: list[tuple] = []

    def get_namespaced_custom_object(self, group, version, namespace, plural, name, **kwargs):
        self.calls.append((group, version, namespace, plural, name))
        return self.apps_by_name[name]
