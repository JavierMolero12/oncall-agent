import httpx
import pytest
from github_fakes import REPO, FakeGitHub, git_blob_sha

from oncall_agent.github import GitHubClient, GitHubError

KUSTOMIZATION = 'images:\n  - name: sample-api\n    newTag: "ed01aa4"\n'


def client_for(fake: FakeGitHub, token: str | None = None) -> GitHubClient:
    return GitHubClient(REPO, token, transport=fake.transport())


def test_list_commits_shapes_response():
    fake = FakeGitHub()
    fake.add_commit(
        "3f1c2a5" + "0" * 33,
        "chore(dev): bump sample-api to v9.9.9\n\nlonger body",
        {"apps/overlays/dev/kustomization.yaml": "-ed01aa4\n+v9.9.9"},
        "2026-11-04T17:00:00Z",
    )
    commits = client_for(fake).list_commits("apps/overlays/dev", limit=5)
    assert commits == [
        {
            "sha": "3f1c2a5" + "0" * 33,
            "short_sha": "3f1c2a5",
            "message": "chore(dev): bump sample-api to v9.9.9",
            "author": "github-actions[bot]",
            "date": "2026-11-04T17:00:00Z",
        }
    ]
    assert fake.requests[0].url.params["per_page"] == "5"


def test_get_file_decodes_base64():
    fake = FakeGitHub({"apps/overlays/dev/kustomization.yaml": KUSTOMIZATION})
    text, blob_sha = client_for(fake).get_file("apps/overlays/dev/kustomization.yaml")
    assert text == KUSTOMIZATION
    assert blob_sha == git_blob_sha(KUSTOMIZATION)


def test_http_error_becomes_github_error_in_spanish():
    with pytest.raises(GitHubError, match="404") as err:
        client_for(FakeGitHub()).get_file("apps/overlays/dev/missing.yaml")
    assert "GitHub respondió 404" in str(err.value)


def test_bad_token_explains_what_to_check():
    fake = FakeGitHub()
    fake.fail_with = (401, "Bad credentials")
    with pytest.raises(GitHubError, match="revisa GITHUB_TOKEN"):
        client_for(fake, token="wrong").list_commits("apps/overlays/dev")


def test_token_is_sent_only_when_present():
    fake = FakeGitHub({"a.yaml": "x"})
    client_for(fake).get_file("a.yaml")
    client_for(fake, token="secret").get_file("a.yaml")
    assert "authorization" not in fake.requests[0].headers
    assert fake.requests[1].headers["authorization"] == "Bearer secret"
    assert fake.requests[1].headers["x-github-api-version"] == "2022-11-28"


def test_network_failure_is_reported_in_spanish():
    def offline(request):
        raise httpx.ConnectError("no route to host")

    client = GitHubClient(REPO, None, transport=httpx.MockTransport(offline))
    with pytest.raises(GitHubError, match="No puedo conectar con GitHub"):
        client.list_commits("apps/overlays/dev")
