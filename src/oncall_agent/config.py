"""Settings and fixed facts about the gitops-demo cluster."""

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import find_dotenv, load_dotenv

# The only folder of gitops-manifests the agent may propose changes to.
WRITABLE_PREFIX = "apps/overlays/dev/"


@dataclass(frozen=True)
class EnvironmentSpec:
    namespace: str
    argocd_app: str


ENVIRONMENTS: dict[str, EnvironmentSpec] = {
    "dev": EnvironmentSpec(namespace="sample-api-dev", argocd_app="sample-api-dev"),
    "prod": EnvironmentSpec(namespace="sample-api-prod", argocd_app="sample-api-prod"),
    "monitoring": EnvironmentSpec(namespace="monitoring", argocd_app="monitoring"),
}


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    github_token: str | None
    model: str = "claude-sonnet-5-5"
    max_steps: int = 15
    gitops_repo: str = "JavierMolero12/gitops-manifests"
    gitops_branch: str = "main"
    kube_context: str = "k3d-gitops-demo"
    argocd_namespace: str = "argocd"


def _secret(value: str | None) -> str | None:
    """An empty or blank secret is the same as a missing one."""
    if value is None or not value.strip():
        return None
    return value.strip()


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from `env`, or from .env + the real environment when env is None."""
    if env is None:
        # Looks for .env starting from the current folder (run commands from the project root).
        load_dotenv(find_dotenv(usecwd=True))
        env = os.environ

    defaults = Settings(anthropic_api_key=None, github_token=None)
    return Settings(
        anthropic_api_key=_secret(env.get("ANTHROPIC_API_KEY")),
        github_token=_secret(env.get("GITHUB_TOKEN")),
        model=env.get("ONCALL_MODEL") or defaults.model,
        max_steps=int(env.get("ONCALL_MAX_STEPS") or defaults.max_steps),
        gitops_repo=env.get("GITOPS_REPO") or defaults.gitops_repo,
        gitops_branch=env.get("GITOPS_BRANCH") or defaults.gitops_branch,
        kube_context=env.get("KUBE_CONTEXT") or defaults.kube_context,
        argocd_namespace=env.get("ARGOCD_NAMESPACE") or defaults.argocd_namespace,
    )
