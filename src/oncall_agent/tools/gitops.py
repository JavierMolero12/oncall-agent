"""Git tools over gitops-manifests: read the history and manifests, propose and open the fix."""

import re

from oncall_agent.github import GitHubClient

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_PATCH = 4000  # characters per file, to keep tool results small


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
