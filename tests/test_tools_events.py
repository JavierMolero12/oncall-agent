from datetime import UTC, datetime, timedelta

import pytest
from factories import FakeCoreApi, make_event

from oncall_agent.kube import KubeClients
from oncall_agent.tools.cluster import get_pod_events

POD = "sample-api-7d9f-new"
T0 = datetime(2026, 11, 4, 18, 0, tzinfo=UTC)
PULL_FAILED = (
    'Failed to pull image "ghcr.io/javiermolero12/sample-api:v9.9.9": '
    "rpc error: code = NotFound desc = failed to pull and unpack image: not found"
)


def clients_with(events) -> KubeClients:
    core = FakeCoreApi(events_by_namespace={"sample-api-dev": events})
    return KubeClients(core=core, apps=None, custom=None)


def test_events_sorted_newest_first_and_capped_at_10():
    events = [
        make_event(f"Reason{i}", f"message {i}", T0 + timedelta(seconds=i)) for i in range(12)
    ]
    result = get_pod_events("dev", POD, clients_with(events))
    reasons = [e["reason"] for e in result["events"]]
    assert reasons == [f"Reason{i}" for i in range(11, 1, -1)]  # 11..2: newest first, 10 of them


def test_asks_only_for_this_pods_events():
    clients = clients_with([])
    get_pod_events("dev", POD, clients)
    assert clients.core.calls == [
        (
            "list_namespaced_event",
            "sample-api-dev",
            {"field_selector": f"involvedObject.name={POD}"},
        )
    ]


def test_summary_mentions_last_reason():
    events = [
        make_event("Pulling", "Pulling image ...", T0, type_="Normal"),
        make_event("Failed", PULL_FAILED, T0 + timedelta(seconds=5), count=4),
    ]
    result = get_pod_events("dev", POD, clients_with(events))
    assert result["summary"].startswith("2 eventos · último: Failed: Failed to pull image")
    message_part = result["summary"].split("último: Failed: ")[1]
    assert len(message_part) <= 80 and message_part.endswith("…")
    assert result["events"][0]["count"] == 4


def test_no_events_summary():
    assert get_pod_events("dev", POD, clients_with([]))["summary"] == "Sin eventos recientes"


def test_unknown_environment_rejected():
    with pytest.raises(ValueError, match="Usa dev o prod"):
        get_pod_events("staging", POD, clients_with([]))


def test_rejects_names_that_are_not_pod_names():
    # The name goes into a field selector: a comma would add a second condition.
    with pytest.raises(ValueError, match="no es un nombre de pod"):
        get_pod_events("dev", "x,type=Normal", clients_with([]))
