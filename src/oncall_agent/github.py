"""A small synchronous client for the GitHub REST API, limited to what the agent needs."""

import base64
from urllib.parse import quote

import httpx

API = "https://api.github.com"

_HINTS = {
    401: "revisa GITHUB_TOKEN",
    403: "el token no tiene permiso o se ha agotado el límite de peticiones",
    404: "no existe o el token no tiene acceso",
    409: "conflicto: el fichero ha cambiado en la rama",
    422: "GitHub ha rechazado los datos",
}


class GitHubError(Exception):
    """A failed GitHub call. The message is in Spanish and includes the HTTP status."""


class GitHubClient:
    def __init__(self, repo: str, token: str | None, transport: httpx.BaseTransport | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "oncall-agent",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.repo = repo
        self._http = httpx.Client(base_url=API, headers=headers, transport=transport, timeout=15)

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        try:
            response = self._http.request(method, f"/repos/{self.repo}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise GitHubError(f"No puedo conectar con GitHub: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("message", "")
            except ValueError:
                detail = response.text[:200]
            hint = _HINTS.get(response.status_code)
            message = f"GitHub respondió {response.status_code}: {detail}"
            raise GitHubError(f"{message} ({hint})" if hint else message)
        return response.json() if response.content else {}

    # -- reads ---------------------------------------------------------------
    def list_commits(self, path: str, limit: int = 5) -> list[dict]:
        commits = self._request("GET", "/commits", params={"path": path, "per_page": limit})
        return [
            {
                "sha": c["sha"],
                "short_sha": c["sha"][:7],
                "message": c["commit"]["message"].splitlines()[0],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
            }
            for c in commits
        ]

    def get_commit(self, sha: str) -> dict:
        c = self._request("GET", f"/commits/{sha}")
        return {
            "sha": c["sha"],
            "message": c["commit"]["message"],
            "files": [{"filename": f["filename"], "patch": f.get("patch", "")} for f in c["files"]],
        }

    def get_file(self, path: str, ref: str = "main") -> tuple[str, str]:
        """Return (text, blob_sha). GitHub needs the blob sha back to update the file."""
        data = self._request("GET", f"/contents/{quote(path)}", params={"ref": ref})
        return base64.b64decode(data["content"]).decode(), data["sha"]

    def branch_head(self, branch: str) -> str:
        return self._request("GET", f"/git/ref/heads/{quote(branch)}")["object"]["sha"]

    def get_pull_request(self, number: int) -> dict:
        pr = self._request("GET", f"/pulls/{int(number)}")
        return {
            "number": pr["number"],
            "url": pr["html_url"],
            "title": pr.get("title", ""),
            "state": pr["state"],
            "merged": bool(pr.get("merged")),
            "merge_commit_sha": pr.get("merge_commit_sha") if pr.get("merged") else None,
        }

    # -- writes (only open_pull_request uses these) --------------------------
    def create_branch(self, name: str, from_sha: str) -> None:
        self._request("POST", "/git/refs", json={"ref": f"refs/heads/{name}", "sha": from_sha})

    def put_file(self, path: str, content: str, message: str, branch: str, blob_sha: str) -> str:
        """Commit `content` to `path` on `branch`. Returns the new commit's sha."""
        body = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
            "sha": blob_sha,
        }
        return self._request("PUT", f"/contents/{quote(path)}", json=body)["commit"]["sha"]

    def create_pull_request(self, title: str, body: str, head: str, base: str) -> dict:
        pr = self._request(
            "POST", "/pulls", json={"title": title, "body": body, "head": head, "base": base}
        )
        return {"number": pr["number"], "url": pr["html_url"]}
