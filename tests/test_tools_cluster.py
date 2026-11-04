import pytest
from factories import (
    FakeAppsApi,
    FakeCoreApi,
    FakeCustomApi,
    make_argo_app,
    make_deployment,
    make_pod,
)
from urllib3.exceptions import MaxRetryError

from oncall_agent.config import load_settings
from oncall_agent.kube import ClusterUnavailable, KubeClients
from oncall_agent.tools.cluster import get_argocd_app, get_deployment, list_pods

S = load_settings({})
BROKEN_IMAGE = "ghcr.io/javiermolero12/sample-api:v9.9.9"


def clients(core=None, apps=None, custom=None) -> KubeClients:
    return KubeClients(core=core or FakeCoreApi(), apps=apps or FakeAppsApi(), custom=custom)


def test_argocd_summary_translates_states():
    custom = FakeCustomApi({"sample-api-dev": make_argo_app(sync="Synced", health="Degraded")})
    result = get_argocd_app("dev", clients(custom=custom), S)
    assert result["summary"] == "Sincronizado · Degradado"
    # Reads the Application through the Kubernetes API, in the argocd namespace.
    assert custom.calls == [("argoproj.io", "v1alpha1", "argocd", "applications", "sample-api-dev")]


def test_argocd_keeps_raw_values_for_comparisons():
    app = make_argo_app(sync="OutOfSync", health="Progressing", revision="abc123")
    result = get_argocd_app("dev", clients(custom=FakeCustomApi({"sample-api-dev": app})), S)
    assert (result["sync_status"], result["health_status"]) == ("OutOfSync", "Progressing")
    assert result["revision"] == "abc123"
    assert result["repo_path"] == "apps/overlays/dev"
    assert result["summary"] == "Desincronizado · En progreso"


def test_unknown_argo_state_is_shown_as_is():
    app = make_argo_app(health="Suspended")
    result = get_argocd_app("dev", clients(custom=FakeCustomApi({"sample-api-dev": app})), S)
    assert result["summary"] == "Sincronizado · Suspended"


def test_list_pods_reports_failure_reason():
    core = FakeCoreApi(
        {
            "sample-api-dev": [
                make_pod("sample-api-old"),
                make_pod(
                    "sample-api-new",
                    ready=False,
                    waiting_reason="ImagePullBackOff",
                    image=BROKEN_IMAGE,
                ),
            ]
        }
    )
    result = list_pods("dev", clients(core=core))
    assert result["pods"][1]["failure_reason"] == "ImagePullBackOff"
    assert result["pods"][1]["image"] == BROKEN_IMAGE
    assert result["pods"][0]["failure_reason"] is None
    assert "1 de 2 pods listos" in result["summary"]
    assert "sample-api-new en ImagePullBackOff" in result["summary"]


def test_deployment_exposes_image_tag():
    apps = FakeAppsApi({"sample-api-prod": make_deployment(image="ghcr.io/x/sample-api:ed01aa4")})
    assert get_deployment("prod", clients(apps=apps))["image"].endswith(":ed01aa4")


def test_deployment_summary_flags_incomplete_rollout():
    stuck = make_deployment(
        image=BROKEN_IMAGE,
        replicas=1,
        ready=1,
        updated=1,
        available=1,
        conditions=[("Progressing", "True", "ReplicaSetUpdated", "ReplicaSet is progressing.")],
    )
    stuck.status.replicas = 2  # old pod + new pod
    stuck.status.ready_replicas = 1
    result = get_deployment("dev", clients(apps=FakeAppsApi({"sample-api-dev": stuck})))
    assert (
        result["summary"] == "Imagen sample-api:v9.9.9 · 1/1 listos · actualización sin completar"
    )
    assert result["conditions"][0]["reason"] == "ReplicaSetUpdated"


def test_deployment_summary_when_rollout_complete():
    done = make_deployment(image="ghcr.io/javiermolero12/sample-api:" + "ed01aa4" + "6" * 33)
    result = get_deployment("dev", clients(apps=FakeAppsApi({"sample-api-dev": done})))
    assert result["summary"] == "Imagen sample-api:ed01aa4 · 1/1 listos · actualización completada"


def test_unknown_environment_rejected():
    with pytest.raises(ValueError, match="Usa dev o prod"):
        list_pods("monitoring", clients())


def test_cluster_down_is_reported_in_spanish():
    core = FakeCoreApi(error=MaxRetryError(None, "/api/v1/namespaces/sample-api-dev/pods"))
    with pytest.raises(ClusterUnavailable, match="¿está Docker arrancado\\?"):
        list_pods("dev", clients(core=core))
