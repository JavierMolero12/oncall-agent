"""Git tools over gitops-manifests: read the history and manifests, propose and open the fix."""

import difflib
import re
from collections.abc import Callable
from datetime import UTC, datetime

import yaml

from oncall_agent.config import WRITABLE_PREFIX
from oncall_agent.github import GitHubClient, GitHubError

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_PATCH = 4000  # characters per file, to keep tool results small
MAX_TITLE = 100
PR_FOOTER = (
    "\n\n---\n*Abierto por oncall-agent después de que una persona pulsara «Abrir Pull Request». "
    "No se aplica nada hasta que alguien revise y fusione este PR.*"
)


def list_recent_commits(path: str, github: GitHubClient, limit: int = 5) -> dict:
    commits = github.list_commits(path, limit=limit)
    if commits:
        last = commits[0]
        summary = f"Último cambio: {last['short_sha']} «{last['message']}»"
    else:
        summary = f"Sin commits en {path}"
    return {"path": path, "commits": commits, "summary": summary}


def get_commit_diff(sha: str, github: GitHubClient) -> dict:
    if not _SHA.match(sha):
        raise ValueError(f"«{sha}» no es un sha de commit. Usa uno de list_recent_commits.")
    commit = github.get_commit(sha)
    files = [{"filename": f["filename"], "patch": f["patch"][:MAX_PATCH]} for f in commit["files"]]
    names = ", ".join(f["filename"] for f in files) or "ningún fichero"
    return {
        "sha": commit["sha"],
        "message": commit["message"],
        "files": files,
        "summary": f"Cambia {names}",
    }


def read_manifest(path: str, github: GitHubClient, ref: str = "main") -> dict:
    content, _ = github.get_file(path, ref=ref)
    lines = len(content.splitlines())
    return {
        "path": path,
        "ref": ref,
        "content": content,
        "summary": f"Leído {path} ({lines} líneas)",
    }


# -- the fix: propose (no side effects) and open the PR (the only write) ---------------------


def ensure_writable(path: str) -> str:
    """Return the path in POSIX form if it is a file inside apps/overlays/dev/, else refuse."""
    clean = path.replace("\\", "/")
    parts = clean.split("/")
    refused = (
        clean.startswith("/")
        or re.match(r"^[A-Za-z]:", clean)
        or any(part in ("", ".", "..") for part in parts)
        or not clean.startswith(WRITABLE_PREFIX)
    )
    if refused:
        raise ValueError(
            f"Solo puedo proponer cambios en {WRITABLE_PREFIX} (ruta recibida: {path})"
        )
    return clean


def _check_text(title: str, explanation: str) -> tuple[str, str]:
    title, explanation = title.strip(), explanation.strip()
    if not title or len(title) > MAX_TITLE:
        raise ValueError(f"El título del arreglo debe tener entre 1 y {MAX_TITLE} caracteres.")
    if not explanation:
        raise ValueError("Falta la explicación del arreglo.")
    return title, explanation


def _check_yaml(path: str, content: str) -> None:
    if path.endswith((".yaml", ".yml")):
        try:
            list(yaml.safe_load_all(content))
        except yaml.YAMLError as exc:
            raise ValueError(f"El contenido propuesto no es YAML válido: {exc}") from exc


def propose_fix(
    file_path: str, new_content: str, title: str, explanation: str, github: GitHubClient
) -> dict:
    """Prepare a fix and its diff. Writes nothing: a person decides whether to open the PR."""
    path = ensure_writable(file_path)
    title, explanation = _check_text(title, explanation)
    try:
        current, blob_sha = github.get_file(path)
    except GitHubError as exc:
        raise ValueError(f"No puedo leer {path} en main: {exc}") from exc
    if current.endswith("\n") and not new_content.endswith("\n"):
        new_content += "\n"
    if new_content == current:
        raise ValueError("La propuesta no cambia nada: el contenido es igual al de main.")
    _check_yaml(path, new_content)
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return {
        "file_path": path,
        "new_content": new_content,
        "title": title,
        "explanation": explanation,
        "diff": diff,
        "base_blob_sha": blob_sha,
        "summary": f"Propuesta: cambiar {path}",
    }


def open_pull_request(
    file_path: str,
    new_content: str,
    title: str,
    explanation: str,
    base_blob_sha: str,
    github: GitHubClient,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    base: str = "main",
) -> dict:
    """Open a PR with exactly the proposed change: new branch, one commit, PR against main."""
    path = ensure_writable(file_path)
    title, explanation = _check_text(title, explanation)
    _check_yaml(path, new_content)

    head = github.branch_head(base)
    _, current_blob_sha = github.get_file(path, ref=head)
    if current_blob_sha != base_blob_sha:
        raise ValueError(
            "El fichero ha cambiado en main desde la propuesta. Vuelve a pedir el diagnóstico."
        )

    branch = f"oncall-agent/fix-{now():%Y%m%d-%H%M%S}"
    github.create_branch(branch, head)
    github.put_file(path, new_content, title, branch, base_blob_sha)
    pr = github.create_pull_request(title, explanation + PR_FOOTER, head=branch, base=base)
    return {
        "number": pr["number"],
        "url": pr["url"],
        "branch": branch,
        "summary": f"PR #{pr['number']} abierto en gitops-manifests",
    }
