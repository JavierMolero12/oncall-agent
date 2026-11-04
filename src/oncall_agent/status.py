"""Health of each environment, computed from its pods, for the live panel."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from kubernetes.client import V1Pod

from oncall_agent.config import ENVIRONMENTS
from oncall_agent.kube import ClusterUnavailable, KubeClients, call_kube


class Health(StrEnum):
    HEALTHY = "Sano"
    STUCK = "Actualización atascada"  # the new version won't start, the old one still serves
    FAILING = "Con fallos"  # no pod is ready
    UNREACHABLE = "Sin conexión"


# Container "waiting" reasons that mean the container cannot start on its own.
FAILURE_REASONS = frozenset(
    {
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
        "CrashLoopBackOff",
        "CreateContainerConfigError",
        "CreateContainerError",
    }
)

# Pods in these phases are finished (a completed job, an evicted pod): they are history.
FINISHED_PHASES = frozenset({"Succeeded", "Failed"})


@dataclass(frozen=True)
class EnvironmentStatus:
    environment: str
    namespace: str
    health: Health
    ready_pods: int
    total_pods: int
    detail: str


def pod_failure_reason(pod: V1Pod) -> str | None:
    """Why this pod's container cannot run, or None. OOMKilled wins: it is the real cause."""
    waiting_reason = None
    for cs in pod.status.container_statuses or []:
        last_terminated = cs.last_state.terminated if cs.last_state else None
        if last_terminated and last_terminated.reason == "OOMKilled":
            return "OOMKilled"
        waiting = cs.state.waiting if cs.state else None
        if waiting and waiting.reason in FAILURE_REASONS:
            waiting_reason = waiting.reason
    return waiting_reason


def is_ready(pod: V1Pod) -> bool:
    return any(c.type == "Ready" and c.status == "True" for c in pod.status.conditions or [])


def classify(pods: list[V1Pod]) -> tuple[Health, int, int]:
    """Return (health, ready pods, counted pods)."""
    live = [
        p
        for p in pods
        if p.metadata.deletion_timestamp is None and p.status.phase not in FINISHED_PHASES
    ]
    ready = [p for p in live if is_ready(p)]
    broken = [p for p in live if not is_ready(p) and pod_failure_reason(p)]

    if live and not ready:
        health = Health.FAILING
    elif broken:
        health = Health.STUCK
    else:
        health = Health.HEALTHY
    return health, len(ready), len(live)


def get_environment_status(environment: str, clients: KubeClients) -> EnvironmentStatus:
    spec = ENVIRONMENTS[environment]
    pods = call_kube(clients.core.list_namespaced_pod, spec.namespace).items
    health, ready, total = classify(pods)
    return EnvironmentStatus(
        environment=environment,
        namespace=spec.namespace,
        health=health,
        ready_pods=ready,
        total_pods=total,
        detail=f"{ready} de {total} pods listos",
    )


def get_all_statuses(clients_factory: Callable[[], KubeClients]) -> list[EnvironmentStatus]:
    try:
        clients = clients_factory()
        return [get_environment_status(env, clients) for env in ENVIRONMENTS]
    except ClusterUnavailable:
        return [
            EnvironmentStatus(
                environment=env,
                namespace=spec.namespace,
                health=Health.UNREACHABLE,
                ready_pods=0,
                total_pods=0,
                detail="No puedo conectar con el clúster",
            )
            for env, spec in ENVIRONMENTS.items()
        ]
