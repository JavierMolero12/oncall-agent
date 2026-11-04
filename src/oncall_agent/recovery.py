"""After the fix PR is opened: follow it until the environment is healthy again.

Stages, in order: waiting_merge -> merged -> synced -> healthy. Or it ends early: closed, timeout.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from oncall_agent.github import GitHubError
from oncall_agent.kube import ClusterUnavailable
from oncall_agent.status import EnvironmentStatus, Health


@dataclass(frozen=True)
class RecoveryUpdate:
    stage: str
    message: str
    done: bool


async def watch_recovery(
    pr_number: int,
    environment: str,
    get_pr: Callable[[int], dict[str, Any]],
    get_app: Callable[[str], dict[str, Any]],
    get_status: Callable[[str], EnvironmentStatus],
    poll_seconds: float = 5.0,
    timeout_seconds: float = 900.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[RecoveryUpdate]:
    """Poll GitHub and Argo CD, and announce each stage once, when it is reached.

    The three checks are ordinary blocking functions (HTTP calls), so they run in a worker
    thread and the web server keeps answering in the meantime.
    """
    started = clock()
    reached: set[str] = set()
    last_error: str | None = None
    while True:
        try:
            pr = await asyncio.to_thread(get_pr, pr_number)
            if pr["merged"]:
                if "merged" not in reached:
                    reached.add("merged")
                    message = f"PR #{pr_number} fusionado. Esperando a que Argo CD lo aplique…"
                    yield RecoveryUpdate("merged", message, False)
                sha = pr["merge_commit_sha"]
                if "synced" not in reached:
                    app = await asyncio.to_thread(get_app, environment)
                    # "Synced" alone is not enough: it has to be the commit of this merge.
                    if app["revision"] == sha and app["sync_status"] == "Synced":
                        reached.add("synced")
                        message = f"Argo CD ha aplicado el commit {sha[:7]}."
                        yield RecoveryUpdate("synced", message, False)
                if "synced" in reached:
                    status = await asyncio.to_thread(get_status, environment)
                    if status.health == Health.HEALTHY:
                        message = (
                            f"Resuelto. Argo CD ha aplicado el cambio y {environment} "
                            "vuelve a estar sano."
                        )
                        yield RecoveryUpdate("healthy", message, True)
                        return
            elif pr["state"] == "closed":
                message = f"Has cerrado el PR #{pr_number} sin fusionarlo. No se ha aplicado nada."
                yield RecoveryUpdate("closed", message, True)
                return
            elif "waiting_merge" not in reached:
                reached.add("waiting_merge")
                message = f"Esperando a que revises y fusiones el PR #{pr_number} en GitHub."
                yield RecoveryUpdate("waiting_merge", message, False)
            last_error = None
        except (ClusterUnavailable, GitHubError) as exc:
            last_error = str(exc)  # a blip (Docker restarting, network): try again next round

        if clock() - started >= timeout_seconds:
            minutes = round(timeout_seconds / 60)
            message = (
                f"Han pasado {minutes} minutos y {environment} no se ha recuperado. Revisa Argo CD."
            )
            if last_error:
                message += f" Último error: {last_error}"
            yield RecoveryUpdate("timeout", message, True)
            return
        await sleep(poll_seconds)
