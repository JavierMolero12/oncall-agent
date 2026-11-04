"""Break dev on purpose for the demo, and put it back, the GitOps way: a commit to main.

    python scripts/demo_faults.py break image   # dev asks for a tag that does not exist
    python scripts/demo_faults.py restore       # dev goes back to the image prod runs

These are Javier's tools, not the agent's. They commit straight to main, which the
agent can never do; Argo CD then applies the change like any other commit.
"""

import argparse
import re
import sys

from kubernetes.client.exceptions import ApiException

from oncall_agent.config import Settings, load_settings
from oncall_agent.github import GitHubClient, GitHubError
from oncall_agent.kube import ClusterUnavailable, kube_clients
from oncall_agent.tools import cluster

KUSTOMIZATION = "apps/overlays/dev/kustomization.yaml"
BRANCH = "main"
BROKEN_TAG = "v9.9.9"
BREAK_MESSAGE = f"chore(dev): bump sample-api to {BROKEN_TAG}"
RESTORE_MESSAGE = "fix(dev): restore sample-api image tag (demo reset)"

# A `newTag:` key line: the key, an optional quote, the value, the same quote. Anchored at
# the start of the line, so the comment in the file that talks about newTag never matches.
_NEW_TAG = re.compile(r"""^[ \t]*newTag:[ \t]*(["']?)([^"'\s#]+)\1""", re.MULTILINE)


def set_image_tag(kustomization_text: str, tag: str) -> str:
    """Replace only the value on the `newTag:` line; quotes, comments and the rest stay."""
    found = list(_NEW_TAG.finditer(kustomization_text))
    if not found:
        raise ValueError(f"No encuentro ninguna línea newTag en {KUSTOMIZATION}")
    if len(found) > 1:
        raise ValueError(f"Hay más de una línea newTag en {KUSTOMIZATION}: no sé cuál cambiar")
    start, end = found[0].span(2)
    return kustomization_text[:start] + tag + kustomization_text[end:]


def image_tag(image: str) -> str:
    """The tag of an image reference: ghcr.io/x/sample-api:<tag> -> <tag>."""
    if "@" in image:
        raise ValueError(f"La imagen {image} va fijada por digest, no por tag")
    _, separator, tag = image.rsplit("/", 1)[-1].partition(":")
    if not separator or not tag:
        raise ValueError(f"La imagen {image} no tiene tag")
    return tag


def _commit_tag(github: GitHubClient, tag: str, message: str) -> str | None:
    """Commit `tag` into the dev overlay on main. None, and no commit, if dev already has it."""
    text, blob_sha = github.get_file(KUSTOMIZATION, ref=BRANCH)
    changed = set_image_tag(text, tag)
    if changed == text:
        return None
    # The blob sha makes GitHub refuse the commit if the file changed since we read it.
    return github.put_file(KUSTOMIZATION, changed, message, BRANCH, blob_sha)


def break_image(github: GitHubClient) -> str | None:
    return _commit_tag(github, BROKEN_TAG, BREAK_MESSAGE)


def restore(github: GitHubClient, prod_image: str) -> str | None:
    return _commit_tag(github, image_tag(prod_image), RESTORE_MESSAGE)


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rompe dev a propósito para la demo, o lo arregla, con un commit en main."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    breaking = commands.add_parser("break", help="rompe dev")
    breaking.add_argument(
        "fault", choices=["image"], help=f"image: sample-api:{BROKEN_TAG}, que no existe"
    )
    commands.add_parser("restore", help="dev vuelve a la imagen que corre en prod")
    args = parser.parse_args(argv)

    if settings is None:
        settings = load_settings()
    if not settings.github_token:
        print("Falta GITHUB_TOKEN en el fichero .env")
        return 1
    github = GitHubClient(settings.gitops_repo, settings.github_token)
    try:
        if args.command == "break":
            sha = break_image(github)
            result = f"dev pedirá sample-api:{BROKEN_TAG}, que no existe"
        else:
            prod_image = cluster.get_deployment("prod", kube_clients(settings))["image"]
            sha = restore(github, prod_image)
            result = f"dev vuelve a la imagen de prod ({cluster.short_image(prod_image)})"
    except (GitHubError, ClusterUnavailable, ApiException, ValueError) as exc:
        print(f"No he podido hacerlo: {exc}")
        return 1
    if sha is None:
        print("No hay nada que cambiar: dev ya tiene ese tag. No he hecho ningún commit.")
        return 0
    print(f"Hecho: commit {sha[:7]} en {BRANCH}; {result}.")
    print("Argo CD lo aplicará en unos 30 s (tras ejecutar scripts/setup_argocd_polling.py).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
