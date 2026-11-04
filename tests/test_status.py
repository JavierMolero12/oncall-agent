import pytest
from factories import FakeCoreApi, make_pod
from urllib3.exceptions import MaxRetryError

from oncall_agent.kube import ClusterUnavailable, KubeClients, call_kube
from oncall_agent.status import (
    Health,
    classify,
    get_all_statuses,
    get_environment_status,
    pod_failure_reason,
)


def clients_with(core: FakeCoreApi) -> KubeClients:
    return KubeClients(core=core, apps=None, custom=None)


def test_healthy_when_all_pods_ready():
    assert classify([make_pod("a"), make_pod("b")]) == (Health.HEALTHY, 2, 2)


def test_stuck_when_new_pod_cannot_pull_but_old_serves():
    pods = [make_pod("old"), make_pod("new", ready=False, waiting_reason="ImagePullBackOff")]
    assert classify(pods) == (Health.STUCK, 1, 2)


def test_failing_when_no_pod_ready():
    pods = [make_pod("a", ready=False, waiting_reason="CrashLoopBackOff")]
    assert classify(pods)[0] == Health.FAILING


def test_oomkilled_is_a_failure_reason():
    pod = make_pod(
        "a", ready=False, waiting_reason="CrashLoopBackOff", last_terminated_reason="OOMKilled"
    )
    assert pod_failure_reason(pod) == "OOMKilled"


def test_ready_pod_with_an_old_oomkill_is_still_healthy():
    # It restarted once because of memory but is serving now: not a stuck rollout.
    assert classify([make_pod("a", last_terminated_reason="OOMKilled")]) == (Health.HEALTHY, 1, 1)


def test_terminating_pods_are_ignored():
    pods = [make_pod("a"), make_pod("b", ready=False, deleting=True)]
    assert classify(pods) == (Health.HEALTHY, 1, 1)


def test_finished_pods_are_ignored():
    pods = [
        make_pod("a"),
        make_pod("job", ready=False, phase="Succeeded"),
        make_pod("evicted", ready=False, phase="Failed"),
    ]
    assert classify(pods) == (Health.HEALTHY, 1, 1)


def test_detail_text_in_spanish():
    core = FakeCoreApi(
        {
            "sample-api-dev": [
                make_pod("old"),
                make_pod("new", ready=False, waiting_reason="ErrImagePull"),
            ]
        }
    )
    status = get_environment_status("dev", clients_with(core))
    assert status.health == Health.STUCK
    assert status.namespace == "sample-api-dev"
    assert status.detail == "1 de 2 pods listos"


def test_all_unreachable_when_cluster_down():
    # Docker stopped: the kubeconfig loads fine, the first API call fails.
    core = FakeCoreApi(error=MaxRetryError(None, "/api/v1/namespaces/sample-api-dev/pods"))
    statuses = get_all_statuses(lambda: clients_with(core))
    assert [s.environment for s in statuses] == ["dev", "prod", "monitoring"]
    assert all(s.health == Health.UNREACHABLE for s in statuses)
    assert statuses[0].detail == "No puedo conectar con el clúster"


def test_all_unreachable_when_clients_cannot_be_built():
    def broken_factory():
        raise ClusterUnavailable("No encuentro el contexto")

    assert all(s.health == Health.UNREACHABLE for s in get_all_statuses(broken_factory))


def test_call_kube_turns_connection_errors_into_spanish_message():
    def refused():
        raise MaxRetryError(None, "/api")

    with pytest.raises(ClusterUnavailable, match="¿está Docker arrancado\\?"):
        call_kube(refused)
