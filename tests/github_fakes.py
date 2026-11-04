"""A tiny in-memory GitHub behind httpx.MockTransport: enough of the REST API for our client."""

import base64
import hashlib
import json
import re

import httpx

REPO = "JavierMolero12/gitops-manifests"


def git_blob_sha(text: str) -> str:
    """The same id Git gives a file's content: sha1("blob <size>\\0<content>")."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    def __init__(self, files: dict[str, str] | None = None, head: str = "a" * 40):
        # branch -> {path: text}
        self.branches: dict[str, dict[str, str]] = {"main": dict(files or {})}
        self.heads: dict[str, str] = {"main": head}
        self.commits: list[dict] = []  # newest first, for /commits?path=
        self.commit_details: dict[str, dict] = {}
        self.pulls: dict[int, dict] = {}
        self.requests: list[httpx.Request] = []
        self.fail_with: tuple[int, str] | None = None

    # -- helpers for tests -------------------------------------------------
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method in ("POST", "PUT", "PATCH", "DELETE")]

    def body(self, request: httpx.Request) -> dict:
        return json.loads(request.content or b"{}")

    # -- the fake API --------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with:
            status, message = self.fail_with
            return httpx.Response(status, json={"message": message})
        prefix = f"/repos/{REPO}"
        path = request.url.path
        if not path.startswith(prefix):
            return httpx.Response(404, json={"message": "Not Found"})
        route = path[len(prefix) :]
        method = request.method

        if method == "GET" and route == "/commits":
            limit = int(request.url.params.get("per_page", 30))
            wanted = request.url.params.get("path", "")
            found = [c for c in self.commits if any(f.startswith(wanted) for f in c["files"])]
            return httpx.Response(200, json=[c["api"] for c in found[:limit]])

        if method == "GET" and (m := re.fullmatch(r"/commits/([0-9a-f]{7,40})", route)):
            detail = self.commit_details.get(m[1])
            if not detail:
                return httpx.Response(404, json={"message": "No commit found for SHA"})
            return httpx.Response(200, json=detail)

        if method == "GET" and route.startswith("/contents/"):
            file_path = route[len("/contents/") :]
            ref = request.url.params.get("ref", "main")
            files = self.branches.get(ref) or self._files_at_sha(ref)
            if files is None or file_path not in files:
                return httpx.Response(404, json={"message": "Not Found"})
            text = files[file_path]
            encoded = base64.encodebytes(text.encode()).decode()  # GitHub wraps lines too
            return httpx.Response(
                200, json={"content": encoded, "encoding": "base64", "sha": git_blob_sha(text)}
            )

        if method == "GET" and (m := re.fullmatch(r"/git/ref/heads/(.+)", route)):
            if m[1] not in self.heads:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.heads[m[1]]}})

        if method == "POST" and route == "/git/refs":
            body = self.body(request)
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.heads:
                return httpx.Response(422, json={"message": "Reference already exists"})
            self.heads[name] = body["sha"]
            self.branches[name] = dict(self._files_at_sha(body["sha"]) or self.branches["main"])
            return httpx.Response(201, json={"ref": body["ref"], "object": {"sha": body["sha"]}})

        if method == "PUT" and route.startswith("/contents/"):
            file_path = route[len("/contents/") :]
            body = self.body(request)
            branch = body["branch"]
            files = self.branches[branch]
            if file_path in files and git_blob_sha(files[file_path]) != body.get("sha"):
                return httpx.Response(409, json={"message": f"{file_path} does not match"})
            files[file_path] = base64.b64decode(body["content"]).decode()
            commit_sha = hashlib.sha1(f"{branch}{len(self.requests)}".encode()).hexdigest()
            self.heads[branch] = commit_sha
            return httpx.Response(200, json={"commit": {"sha": commit_sha}})

        if method == "POST" and route == "/pulls":
            body = self.body(request)
            number = 7 + len(self.pulls)
            pr = {
                "number": number,
                "html_url": f"https://github.com/{REPO}/pull/{number}",
                "title": body["title"],
                "body": body["body"],
                "head": {"ref": body["head"]},
                "base": {"ref": body["base"]},
                "state": "open",
                "merged": False,
                "merge_commit_sha": None,
            }
            self.pulls[number] = pr
            return httpx.Response(201, json=pr)

        if method == "GET" and (m := re.fullmatch(r"/pulls/(\d+)", route)):
            pr = self.pulls.get(int(m[1]))
            if not pr:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=pr)

        return httpx.Response(404, json={"message": f"fake has no route for {method} {route}"})

    def _files_at_sha(self, sha: str) -> dict[str, str] | None:
        for branch, head in self.heads.items():
            if head == sha:
                return self.branches.get(branch)
        return None

    # -- seeding -------------------------------------------------------------
    def add_commit(self, sha: str, message: str, files: dict[str, str], date: str) -> None:
        """files: {filename: patch}"""
        self.commits.insert(
            0,
            {
                "files": list(files),
                "api": {
                    "sha": sha,
                    "commit": {
                        "message": message,
                        "author": {"name": "github-actions[bot]", "date": date},
                    },
                },
            },
        )
        self.commit_details[sha] = {
            "sha": sha,
            "commit": {"message": message},
            "files": [{"filename": f, "patch": p} for f, p in files.items()],
        }
