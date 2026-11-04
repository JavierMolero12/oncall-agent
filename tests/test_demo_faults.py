"""The demo scripts: one line of the dev overlay changes, committed straight to main."""

import pytest
from demo_faults import (
    BREAK_MESSAGE,
    KUSTOMIZATION,
    RESTORE_MESSAGE,
    break_image,
    image_tag,
    main,
    restore,
    set_image_tag,
)
from github_fakes import REPO, FakeGitHub, git_blob_sha

from oncall_agent.config import load_settings
from oncall_agent.github import GitHubClient

GOOD = "ed01aa462622ba11c87c77902dd1f4f63819a249"
# apps/overlays/dev/kustomization.yaml as it is in gitops-manifests on 2026-11-04
DEV = f"""\
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: sample-api-dev
resources:
  - ../../base
# El pipeline de sample-api reescribe newName y newTag en cada push a main
# (commit automático). "pending-first-ci-run" solo existe hasta ese primer push.
images:
  - name: sample-api
    newName: ghcr.io/javiermolero12/sample-api
    newTag: "{GOOD}"
patches:
  # Strategic merge: configuración propia de dev (más log, APP_ENV=dev).
  - path: patch-env.yaml
"""


def github_with(text: str = DEV) -> tuple[FakeGitHub, GitHubClient]:
    fake = FakeGitHub({KUSTOMIZATION: text})
    return fake, GitHubClient(REPO, "t", transport=fake.transport())


# -- set_image_tag --------------------------------------------------------------------------


def test_set_image_tag_replaces_only_tag():
    changed = set_image_tag(DEV, "v9.9.9")

    # Same file, quotes included; the comment that mentions newTag is left alone.
    assert changed == DEV.replace(f'"{GOOD}"', '"v9.9.9"')


def test_set_image_tag_keeps_single_quotes_bare_values_and_trailing_comments():
    assert set_image_tag("    newTag: 'abc'  # ci\n", "v1") == "    newTag: 'v1'  # ci\n"
    assert set_image_tag("    newTag: abc\n", "v1") == "    newTag: v1\n"


def test_set_image_tag_without_newtag_raises():
    with pytest.raises(ValueError, match="newTag"):
        set_image_tag("images:\n  - name: sample-api\n", "v9.9.9")


def test_set_image_tag_refuses_to_guess_between_two_images():
    two = '    newTag: "a"\n    newTag: "b"\n'
    with pytest.raises(ValueError, match="más de una"):
        set_image_tag(two, "v9.9.9")


def test_image_tag_is_the_tag_of_the_running_image():
    assert image_tag(f"ghcr.io/javiermolero12/sample-api:{GOOD}") == GOOD
    assert image_tag("localhost:5000/sample-api:v1") == "v1"  # a registry port is not a tag
    with pytest.raises(ValueError):
        image_tag("ghcr.io/javiermolero12/sample-api")
    with pytest.raises(ValueError):
        image_tag("ghcr.io/javiermolero12/sample-api@sha256:" + "0" * 64)


# -- break and restore ----------------------------------------------------------------------


def test_break_image_commits_v999_straight_to_main():
    fake, github = github_with()

    sha = break_image(github)

    assert sha
    assert fake.branches["main"][KUSTOMIZATION] == set_image_tag(DEV, "v9.9.9")
    (put,) = fake.writes()
    body = fake.body(put)
    assert (body["branch"], body["message"]) == ("main", BREAK_MESSAGE)
    assert body["sha"] == git_blob_sha(DEV)  # GitHub refuses the commit if main moved meanwhile


def test_restore_puts_back_the_tag_that_runs_in_prod():
    fake, github = github_with(set_image_tag(DEV, "v9.9.9"))

    restore(github, prod_image=f"ghcr.io/javiermolero12/sample-api:{GOOD}")

    assert fake.branches["main"][KUSTOMIZATION] == DEV
    assert fake.body(fake.writes()[0])["message"] == RESTORE_MESSAGE


def test_nothing_is_committed_when_dev_already_has_that_tag():
    fake, github = github_with(set_image_tag(DEV, "v9.9.9"))

    assert break_image(github) is None
    assert fake.writes() == []


def test_the_command_needs_a_github_token(capsys):
    assert main(["break", "image"], settings=load_settings({})) == 1
    assert capsys.readouterr().out.strip() == "Falta GITHUB_TOKEN en el fichero .env"
