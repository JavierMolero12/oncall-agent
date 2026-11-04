from datetime import UTC, datetime

import pytest
from github_fakes import REPO, FakeGitHub, git_blob_sha

from oncall_agent.github import GitHubClient
from oncall_agent.tools.gitops import ensure_writable, open_pull_request, propose_fix

PATH = "apps/overlays/dev/kustomization.yaml"
CURRENT = 'images:\n  - name: sample-api\n    newTag: "v9.9.9"\n'
FIXED = CURRENT.replace("v9.9.9", "ed01aa4")
TITLE = "Volver sample-api dev a ed01aa4"
WHY = "La imagen v9.9.9 no existe en GHCR; ed01aa4 es la que funciona en prod."


def fixed_clock() -> datetime:
    return datetime(2026, 11, 4, 18, 0, 0, tzinfo=UTC)


def setup() -> tuple[FakeGitHub, GitHubClient]:
    fake = FakeGitHub({PATH: CURRENT, "apps/overlays/prod/kustomization.yaml": "x: 1\n"})
    return fake, GitHubClient(REPO, "token", transport=fake.transport())


@pytest.mark.parametrize(
    "path",
    [
        "apps/overlays/prod/kustomization.yaml",
        "apps/overlays/dev/../prod/kustomization.yaml",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "argocd-apps/dev-app.yaml",
        "apps/overlays/dev",
        "apps/overlays/dev/",
        "apps/overlays/dev//kustomization.yaml",
        "apps\\overlays\\prod\\kustomization.yaml",
    ],
)
def test_rejects_paths_outside_dev(path):
    with pytest.raises(ValueError, match="Solo puedo proponer cambios en apps/overlays/dev/"):
        ensure_writable(path)


def test_accepts_dev_files_written_with_backslashes():
    assert ensure_writable("apps\\overlays\\dev\\kustomization.yaml") == PATH


def test_propose_fix_has_no_side_effects():
    fake, github = setup()
    result = propose_fix(PATH, FIXED, TITLE, WHY, github)
    assert fake.writes() == []
    assert all(r.method == "GET" for r in fake.requests)
    assert '-    newTag: "v9.9.9"' in result["diff"]
    assert '+    newTag: "ed01aa4"' in result["diff"]
    assert result["diff"].startswith(f"--- a/{PATH}\n+++ b/{PATH}\n")
    assert result["base_blob_sha"] == git_blob_sha(CURRENT)
    assert result["summary"] == f"Propuesta: cambiar {PATH}"


def test_propose_fix_rejects_unchanged_content():
    _, github = setup()
    with pytest.raises(ValueError, match="La propuesta no cambia nada"):
        propose_fix(PATH, CURRENT, TITLE, WHY, github)


def test_propose_fix_rejects_broken_yaml():
    _, github = setup()
    with pytest.raises(ValueError, match="no es YAML válido"):
        propose_fix(PATH, "images: [unclosed\n", TITLE, WHY, github)


def test_propose_fix_keeps_the_final_newline():
    _, github = setup()
    result = propose_fix(PATH, FIXED.rstrip("\n"), TITLE, WHY, github)
    assert result["new_content"] == FIXED
    assert "No newline" not in result["diff"]


def test_propose_fix_rejects_prod_without_reading_anything():
    fake, github = setup()
    with pytest.raises(ValueError):
        propose_fix("apps/overlays/prod/kustomization.yaml", "x: 2\n", TITLE, WHY, github)
    assert fake.requests == []


def test_open_pr_never_targets_main():
    fake, github = setup()
    proposal = propose_fix(PATH, FIXED, TITLE, WHY, github)
    result = open_pull_request(
        PATH, FIXED, TITLE, WHY, proposal["base_blob_sha"], github, now=fixed_clock
    )

    puts = [r for r in fake.writes() if r.method == "PUT"]
    assert len(puts) == 1
    assert fake.body(puts[0])["branch"] == "oncall-agent/fix-20261104-180000"
    pulls = [r for r in fake.writes() if r.url.path.endswith("/pulls")]
    assert fake.body(pulls[0])["base"] == "main"
    assert fake.body(pulls[0])["head"] == "oncall-agent/fix-20261104-180000"
    assert "oncall-agent" in fake.body(pulls[0])["body"]
    assert fake.branches["main"][PATH] == CURRENT  # main itself is untouched
    assert fake.branches["oncall-agent/fix-20261104-180000"][PATH] == FIXED
    assert result == {
        "number": 7,
        "url": f"https://github.com/{REPO}/pull/7",
        "branch": "oncall-agent/fix-20261104-180000",
        "summary": "PR #7 abierto en gitops-manifests",
    }


def test_open_pr_refuses_when_main_changed():
    fake, github = setup()
    stale_sha = git_blob_sha(CURRENT)
    fake.branches["main"][PATH] = CURRENT + "# someone else's change\n"
    with pytest.raises(ValueError, match="ha cambiado en main"):
        open_pull_request(PATH, FIXED, TITLE, WHY, stale_sha, github, now=fixed_clock)
    assert fake.writes() == []


def test_open_pr_rejects_paths_outside_dev_before_any_request():
    fake, github = setup()
    with pytest.raises(ValueError):
        open_pull_request(
            "apps/overlays/prod/kustomization.yaml", "x: 2\n", TITLE, WHY, "sha", github
        )
    assert fake.requests == []
