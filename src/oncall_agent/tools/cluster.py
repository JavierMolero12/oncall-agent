"""Read-only cluster tools: Argo CD Application, Deployment and pods."""

import re

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
