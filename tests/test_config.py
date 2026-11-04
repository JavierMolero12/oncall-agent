from oncall_agent.config import ENVIRONMENTS, WRITABLE_PREFIX, load_settings


def test_defaults_when_env_is_empty():
    s = load_settings({})
    assert s.model == "claude-sonnet-5-5" and s.max_steps == 15
    assert s.gitops_repo == "JavierMolero12/gitops-manifests"
    assert s.kube_context == "k3d-gitops-demo"
    assert s.anthropic_api_key is None and s.github_token is None


def test_env_overrides():
    s = load_settings({"ONCALL_MODEL": "x", "ONCALL_MAX_STEPS": "3", "GITHUB_TOKEN": "t"})
    assert (s.model, s.max_steps, s.github_token) == ("x", 3, "t")


def test_empty_secret_counts_as_missing():
    # .env.example ships "ANTHROPIC_API_KEY=" — copying it unchanged must not look like a key.
    s = load_settings({"ANTHROPIC_API_KEY": "", "GITHUB_TOKEN": "  "})
    assert s.anthropic_api_key is None and s.github_token is None


def test_environments_map_to_namespaces():
    assert ENVIRONMENTS["dev"].namespace == "sample-api-dev"
    assert ENVIRONMENTS["prod"].argocd_app == "sample-api-prod"
    assert ENVIRONMENTS["monitoring"].namespace == "monitoring"


def test_only_the_dev_overlay_is_writable():
    assert WRITABLE_PREFIX == "apps/overlays/dev/"
