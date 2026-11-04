"""Read-only access to the cluster through the official Kubernetes client."""

import functools
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from kubernetes import client, config
from urllib3.exceptions import HTTPError as Urllib3Error

from oncall_agent.config import Settings

CLUSTER_DOWN_MESSAGE = "No puedo conectar con el clúster: ¿está Docker arrancado?"


class ClusterUnavailable(Exception):
    """The cluster cannot be reached. The message is in Spanish, ready for the user."""


@dataclass(frozen=True)
class KubeClients:
    core: client.CoreV1Api  # pods, events, logs
    apps: client.AppsV1Api  # deployments, replicasets
    custom: client.CustomObjectsApi  # Argo CD Application resources


def reachable_host(host: str) -> str:
    """k3d writes the API address as 0.0.0.0, which Windows refuses to connect to."""
    parts = urlsplit(host)
    if parts.hostname == "0.0.0.0":
        return urlunsplit(parts._replace(netloc=parts.netloc.replace("0.0.0.0", "127.0.0.1", 1)))
    return host


@functools.cache
def _clients_for(context: str) -> KubeClients:
    try:
        api_client = config.new_client_from_config(context=context)
    except (config.ConfigException, OSError) as exc:
        raise ClusterUnavailable(
            f"No puedo cargar el contexto «{context}» del kubeconfig: {exc}"
        ) from exc
    api_client.configuration.host = reachable_host(api_client.configuration.host)
    return KubeClients(
        core=client.CoreV1Api(api_client),
        apps=client.AppsV1Api(api_client),
        custom=client.CustomObjectsApi(api_client),
    )


def kube_clients(settings: Settings) -> KubeClients:
    """Clients for the configured context, created once and reused."""
    return _clients_for(settings.kube_context)


def call_kube(fn, *args, **kwargs):
    """Call a Kubernetes API method; a connection failure becomes ClusterUnavailable.

    Loading the kubeconfig never touches the network, so with Docker stopped the
    error only shows up here, on the first real request.
    """
    try:
        return fn(*args, **kwargs)
    except (Urllib3Error, ConnectionError) as exc:
        raise ClusterUnavailable(CLUSTER_DOWN_MESSAGE) from exc
