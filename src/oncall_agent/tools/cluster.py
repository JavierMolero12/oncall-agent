"""Read-only cluster tools: Argo CD Application, Deployment and pods."""

import re
from datetime import UTC, datetime

from oncall_agent.config import ENVIRONMENTS, EnvironmentSpec, Settings
from oncall_agent.kube import KubeClients, call_kube
from oncall_agent.status import classify, is_ready, pod_failure_reason

APP_NAME = "sample-api"  # the Deployment's name in both environments
AGENT_ENVIRONMENTS = ("dev", "prod")

ARGO_STATES_ES = {
    "Synced": "Sincronizado",
    "OutOfSync": "Desincronizado",
    "Healthy": "Sano",
    "Progressing": "En progreso",
    "Degraded": "Degradado",
    "Missing": "No encontrado",
}

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
# Kubernetes object names (RFC 1123): lowercase letters, digits, "-" and ".".
_POD_NAME = re.compile(r"^[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?$")
MAX_EVENTS = 10
_LONG_AGO = datetime.min.replace(tzinfo=UTC)


def environment_spec(environment: str) -> EnvironmentSpec:
    """Only dev and prod run sample-api; anything else is a mistake worth telling the model."""
    if environment not in AGENT_ENVIRONMENTS:
        raise ValueError(f"Entorno desconocido: {environment}. Usa dev o prod.")
    return ENVIRONMENTS[environment]


def short_image(image: str) -> str:
    """ghcr.io/javiermolero12/sample-api:<40-char sha> -> sample-api:<7-char sha>."""
    name = image.rsplit("/", 1)[-1]
    repo, _, tag = name.partition(":")
    if _GIT_SHA.match(tag):
        tag = tag[:7]
    return f"{repo}:{tag}" if tag else repo


def get_argocd_app(environment: str, clients: KubeClients, settings: Settings) -> dict:
    spec = environment_spec(environment)
    app = call_kube(
        clients.custom.get_namespaced_custom_object,
        "argoproj.io",
        "v1alpha1",
        settings.argocd_namespace,
        "applications",
        spec.argocd_app,
    )
    status = app.get("status", {})
    sync_status = status.get("sync", {}).get("status", "Unknown")
    health_status = status.get("health", {}).get("status", "Unknown")
    sync_es = ARGO_STATES_ES.get(sync_status, sync_status)
    health_es = ARGO_STATES_ES.get(health_status, health_status)
    return {
        "environment": environment,
        "app": spec.argocd_app,
        "sync_status": sync_status,
        "health_status": health_status,
        "revision": status.get("sync", {}).get("revision", ""),
        "repo_path": app.get("spec", {}).get("source", {}).get("path", ""),
        "summary": f"{sync_es} · {health_es}",
    }


def get_deployment(environment: str, clients: KubeClients) -> dict:
    spec = environment_spec(environment)
    dep = call_kube(clients.apps.read_namespaced_deployment, APP_NAME, spec.namespace)
    image = dep.spec.template.spec.containers[0].image
    wanted = dep.spec.replicas or 0
    st = dep.status
    ready = st.ready_replicas or 0
    updated = st.updated_replicas or 0
    available = st.available_replicas or 0
    # Complete = the controller has seen the latest spec and only new, available pods remain.
    complete = (
        (st.observed_generation or 0) >= (dep.metadata.generation or 0)
        and updated == wanted
        and available == wanted
        and (st.replicas or 0) == wanted
    )
    rollout = "actualización completada" if complete else "actualización sin completar"
    return {
        "name": dep.metadata.name,
        "image": image,
        "replicas": wanted,
        "ready_replicas": ready,
        "updated_replicas": updated,
        "available_replicas": available,
        "rollout_complete": complete,
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": c.message}
            for c in st.conditions or []
        ],
        "summary": f"Imagen {short_image(image)} · {ready}/{wanted} listos · {rollout}",
    }


def list_pods(environment: str, clients: KubeClients) -> dict:
    spec = environment_spec(environment)
    pods = call_kube(clients.core.list_namespaced_pod, spec.namespace).items
    rows = []
    for pod in pods:
        statuses = pod.status.container_statuses or []
        created = pod.metadata.creation_timestamp
        rows.append(
            {
                "name": pod.metadata.name,
                "ready": is_ready(pod),
                "phase": pod.status.phase,
                "restarts": sum(cs.restart_count or 0 for cs in statuses),
                "image": pod.spec.containers[0].image,
                "failure_reason": pod_failure_reason(pod),
                "terminating": pod.metadata.deletion_timestamp is not None,
                "node": pod.spec.node_name,
                "created": created.isoformat() if created else None,
            }
        )

    _, ready, total = classify(pods)
    summary = f"{ready} de {total} pods listos"
    for row in rows:
        if row["failure_reason"] and not row["ready"] and not row["terminating"]:
            summary += f" · {row['name']} en {row['failure_reason']}"
    return {"environment": environment, "pods": rows, "summary": summary}


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _event_time(event) -> datetime:
    # Core events fill last_timestamp; newer controllers may only fill event_time.
    return (
        event.last_timestamp
        or event.event_time
        or event.first_timestamp
        or event.metadata.creation_timestamp
        or _LONG_AGO
    )


def get_pod_events(environment: str, pod_name: str, clients: KubeClients) -> dict:
    """The pod's recent events, newest first: where Kubernetes explains why a pod won't start."""
    spec = environment_spec(environment)
    if not _POD_NAME.match(pod_name):
        raise ValueError(f"«{pod_name}» no es un nombre de pod. Usa un nombre de list_pods.")
    events = call_kube(
        clients.core.list_namespaced_event,
        spec.namespace,
        field_selector=f"involvedObject.name={pod_name}",
    ).items
    events = sorted(events, key=_event_time, reverse=True)[:MAX_EVENTS]
    rows = [
        {
            "type": e.type,
            "reason": e.reason,
            "message": e.message,
            "count": e.count or 1,
            "last_seen": _event_time(e).isoformat() if _event_time(e) is not _LONG_AGO else None,
        }
        for e in events
    ]
    if not rows:
        summary = "Sin eventos recientes"
    else:
        noun = "evento" if len(rows) == 1 else "eventos"
        last = rows[0]
        summary = (
            f"{len(rows)} {noun} · último: {last['reason']}: {_cut(last['message'] or '', 80)}"
        )
    return {"environment": environment, "pod": pod_name, "events": rows, "summary": summary}
