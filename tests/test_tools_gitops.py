import pytest
from github_fakes import REPO, FakeGitHub

from oncall_agent.github import GitHubClient
from oncall_agent.tools.gitops import get_commit_diff, list_recent_commits, read_manifest

BAD = "3f1c2a5" + "1" * 33
GOOD = "cfc84f4" + "2" * 33
PATH = "apps/overlays/dev/kustomization.yaml"


def fake_with_history() -> FakeGitHub:
    fake = FakeGitHub({PATH: 'newTag: "v9.9.9"\n'})
    fake.add_commit(GOOD, "chore(dev): deploy sample-api ed01aa4", {PATH: "+ed01aa4"}, "2026-10-19")
    fake.add_commit(
        BAD,
        "chore(dev): bump sample-api to v9.9.9",
        {PATH: '-    newTag: "ed01aa4"\n+    newTag: "v9.9.9"'},
        "2026-11-04",
    )
    return fake


def gh(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient(REPO, None, transport=fake.transport())


def test_list_recent_commits_summary():
    result = list_recent_commits("apps/overlays/dev", gh(fake_with_history()))
    assert result["summary"] == "Último cambio: 3f1c2a5 «chore(dev): bump sample-api to v9.9.9»"
    assert [c["short_sha"] for c in result["commits"]] == ["3f1c2a5", "cfc84f4"]


def test_list_recent_commits_without_history():
    result = list_recent_commits("apps/overlays/qa", gh(FakeGitHub()))
    assert result["summary"] == "Sin commits en apps/overlays/qa"


def test_commit_diff_summary_lists_files():
    result = get_commit_diff(BAD, gh(fake_with_history()))
    assert result["summary"] == f"Cambia {PATH}"
    assert '+    newTag: "v9.9.9"' in result["files"][0]["patch"]


def test_commit_diff_rejects_non_sha():
    with pytest.raises(ValueError, match="no es un sha"):
        get_commit_diff("../../user", gh(FakeGitHub()))


def test_read_manifest_returns_content():
    result = read_manifest(PATH, gh(fake_with_history()))
    assert result["content"] == 'newTag: "v9.9.9"\n'
    assert result["summary"] == f"Leído {PATH} (1 líneas)"
