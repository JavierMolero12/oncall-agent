"""The Argo CD setup script: what it sends to the Kubernetes API, checked without a cluster."""

from datetime import UTC, datetime
from types import SimpleNamespace

from setup_argocd_polling import MERGE_PATCH, setup_polling


class RecordingApi:
    """Records every API call; reading argocd-cm returns the value the patch set."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name == "read_namespaced_config_map":
                return SimpleNamespace(data={"timeout.reconciliation": "30s"})
            return None

        return method


def test_sets_30s_and_restarts_the_controller_and_the_repo_server():
    core, apps = RecordingApi(), RecordingApi()
    when = datetime(2026, 11, 4, 18, 0, 0, tzinfo=UTC)

    value = setup_polling(core, apps, now=lambda: when)

    assert value == "30s"
    # A merge patch: with an object body this client would otherwise send a JSON Patch.
    assert MERGE_PATCH == "application/merge-patch+json"
    assert core.calls[0] == (
        "patch_namespaced_config_map",
        ("argocd-cm", "argocd", {"data": {"timeout.reconciliation": "30s"}}),
        {"_content_type": MERGE_PATCH},
    )
    stamp = {"kubectl.kubernetes.io/restartedAt": "2026-11-04T18:00:00Z"}
    restart = {"spec": {"template": {"metadata": {"annotations": stamp}}}}
    assert apps.calls == [
        (
            "patch_namespaced_stateful_set",
            ("argocd-application-controller", "argocd", restart),
            {"_content_type": MERGE_PATCH},
        ),
        (
            "patch_namespaced_deployment",
            ("argocd-repo-server", "argocd", restart),
            {"_content_type": MERGE_PATCH},
        ),
    ]
