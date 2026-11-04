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


class FakeCoreApi:
    """Stands in for CoreV1Api: returns canned pods per namespace, or raises `error`."""

    def __init__(self, pods_by_namespace: dict[str, list[k.V1Pod]] | None = None, error=None):
        self.pods_by_namespace = pods_by_namespace or {}
        self.error = error

    def list_namespaced_pod(self, namespace: str, **kwargs) -> k.V1PodList:
        if self.error:
            raise self.error
        return k.V1PodList(items=self.pods_by_namespace.get(namespace, []))
