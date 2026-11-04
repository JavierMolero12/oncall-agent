import threading

from oncall_agent.kube import CLUSTER_DOWN_MESSAGE, ClusterUnavailable
from oncall_agent.recovery import RecoveryUpdate, watch_recovery
from oncall_agent.status import EnvironmentStatus, Health

MERGE_SHA = "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"
OPEN = {"number": 7, "state": "open", "merged": False}
CLOSED = {"number": 7, "state": "closed", "merged": False}
MERGED = {"number": 7, "state": "closed", "merged": True, "merge_commit_sha": MERGE_SHA}


def app(revision: str, sync: str = "Synced") -> dict:
    return {"revision": revision, "sync_status": sync, "health_status": "Progressing"}


def status(health: Health) -> EnvironmentStatus:
    return EnvironmentStatus(
        environment="dev",
        namespace="sample-api-dev",
        health=health,
        ready_pods=1,
        total_pods=1,
        detail="1 de 1 pods listos",
    )


def phases(*values):
    """A fake check: each call returns the next value, then it keeps returning the last one."""
    remaining = list(values)

    def check(_argument):
        value = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(value, Exception):
            raise value
        return value

    return check


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def watch(get_pr, get_app, get_status) -> tuple[list[RecoveryUpdate], FakeClock]:
    clock = FakeClock()
    updates = [
        update
        async for update in watch_recovery(
            7, "dev", get_pr, get_app, get_status, sleep=clock.sleep, clock=clock
        )
    ]
    return updates, clock


async def test_full_happy_path_emits_each_stage_once():
    updates, clock = await watch(
        phases(OPEN, OPEN, MERGED),
        phases(app("a7f169c"), app(MERGE_SHA)),
        phases(status(Health.STUCK), status(Health.HEALTHY)),
    )

    assert updates == [
        RecoveryUpdate(
            "waiting_merge", "Esperando a que revises y fusiones el PR #7 en GitHub.", False
        ),
        RecoveryUpdate("merged", "PR #7 fusionado. Esperando a que Argo CD lo aplique…", False),
        RecoveryUpdate("synced", "Argo CD ha aplicado el commit 9f8e7d6.", False),
        RecoveryUpdate(
            "healthy", "Resuelto. Argo CD ha aplicado el cambio y dev vuelve a estar sano.", True
        ),
    ]
    assert clock.sleeps == [5.0] * 4


async def test_closed_pr_stops():
    updates, _ = await watch(
        phases(OPEN, CLOSED), phases(app("a7f169c")), phases(status(Health.FAILING))
    )

    assert [u.stage for u in updates] == ["waiting_merge", "closed"]
    assert updates[-1] == RecoveryUpdate(
        "closed", "Has cerrado el PR #7 sin fusionarlo. No se ha aplicado nada.", True
    )


async def test_timeout_stops():
    updates, clock = await watch(
        phases(OPEN), phases(app("a7f169c")), phases(status(Health.FAILING))
    )

    assert [u.stage for u in updates] == ["waiting_merge", "timeout"]
    assert updates[-1] == RecoveryUpdate(
        "timeout", "Han pasado 15 minutos y dev no se ha recuperado. Revisa Argo CD.", True
    )
    assert clock.now == 900.0


async def test_waits_for_matching_revision_not_any_sync():
    # Argo CD says Synced, but at the old commit; then at the merge commit but still OutOfSync.
    updates, clock = await watch(
        phases(MERGED),
        phases(app("a7f169c"), app(MERGE_SHA, sync="OutOfSync"), app(MERGE_SHA)),
        phases(status(Health.HEALTHY)),  # pods look fine all along: not enough on its own
    )

    assert [u.stage for u in updates] == ["merged", "synced", "healthy"]
    assert clock.sleeps == [5.0, 5.0]


async def test_a_failing_check_does_not_stop_the_watch():
    updates, _ = await watch(
        phases(MERGED),
        phases(ClusterUnavailable(CLUSTER_DOWN_MESSAGE), app(MERGE_SHA)),
        phases(status(Health.HEALTHY)),
    )

    assert [u.stage for u in updates] == ["merged", "synced", "healthy"]


async def test_timeout_mentions_the_last_error():
    updates, _ = await watch(
        phases(MERGED),
        phases(ClusterUnavailable(CLUSTER_DOWN_MESSAGE)),
        phases(status(Health.HEALTHY)),
    )

    assert updates[-1].stage == "timeout"
    assert updates[-1].message.endswith(f"Último error: {CLUSTER_DOWN_MESSAGE}")


async def test_checks_run_outside_the_event_loop_thread():
    threads = []

    def get_pr(_number):
        threads.append(threading.current_thread())
        return CLOSED

    await watch(get_pr, phases(app("x")), phases(status(Health.FAILING)))

    assert threads
    assert threading.main_thread() not in threads
