# oncall-agent · Fase 1: plan de implementación

> **Para quien lo ejecute:** SUB-SKILL OBLIGATORIA: superpowers:executing-plans, **en la sesión con Javier**. El diseño prohíbe delegar piezas en subagentes que construyan sin él. Los pasos usan casillas (`- [ ]`) para seguir el avance.

**Objetivo:** que el fallo 1 (imagen que no existe en dev) funcione de principio a fin. Se rompe dev con un script, el agente lo diagnostica en el chat con evidencias, Javier aprueba el arreglo, se abre un PR en `gitops-manifests` y el agente confirma que dev vuelve a estar sano.

**Arquitectura:** un servidor MCP (`oncall_agent.mcp_server`) expone las herramientas: lectura de Kubernetes, Argo CD y Git, una propuesta de arreglo sin efectos y una única herramienta de escritura que abre el PR. El backend FastAPI arranca ese servidor por stdio y ejecuta el bucle del agente escrito a mano con el SDK de Anthropic. **Al modelo solo se le dan las herramientas de solo lectura.** El PR lo abre el backend cuando Javier pulsa el botón, con exactamente el cambio que se le enseñó. El frontend es HTML, CSS y JS sin compilación, con la maqueta aprobada.

**Stack:** Python 3.13 · `anthropic` · `mcp` (FastMCP) · `kubernetes` · `httpx` · `fastapi` + `uvicorn` · `python-dotenv` · pytest + pytest-asyncio · ruff.

**Diseño:** `docs/superpowers/specs/2026-11-04-oncall-agent-design.md`. La maqueta está en `docs/superpowers/specs/2026-11-04-oncall-agent-mockup.html`.

## Restricciones globales

- Python 3.13 (en la máquina: 3.13.1). Windows 10 y PowerShell. Los comandos usan `.venv\Scripts\python` y no requieren activar el entorno.
- **Interfaz y respuestas del agente en español. Código, comentarios y README en inglés.**
- Modelo por defecto `claude-sonnet-5-5`, configurable con `ONCALL_MODEL`. Límite de pasos por pregunta: `ONCALL_MAX_STEPS`, por defecto 15.
- Contexto de kubectl `k3d-gitops-demo`. Namespaces: `sample-api-dev`, `sample-api-prod`, `monitoring` y `argocd`. Apps de Argo CD: `sample-api-dev`, `sample-api-prod` y `monitoring`. Deployment de la app: `sample-api`.
- Repositorio GitOps `JavierMolero12/gitops-manifests`, rama `main`. Único prefijo escribible: `apps/overlays/dev/`.
- **El agente nunca escribe en el clúster.** Ninguna herramienta MCP fusiona, aprueba ni hace push a `main`. La única escritura es `open_pull_request`, sobre una rama nueva.
- **El modelo nunca recibe herramientas de escritura.** Es una decisión del plan: se filtran por `readOnlyHint`.
- Sin frameworks de agentes. Bucle propio con el SDK oficial de Anthropic. Herramientas con el SDK oficial de MCP.
- Secretos solo en `.env`, que está en `.gitignore`. Se sube `.env.example`.
- Dependencias en sus versiones actuales, sin topes superiores.
- **Modo mixto:** cada tarea termina con una **explicación de Javier**. La tarea 4 la escribe Javier y Claude la revisa. Commit local al final de cada tarea. **Nada se publica en GitHub sin el OK explícito de Javier** (tarea 13).

## Foco de revisión

1. **Clúster apagado (Docker parado):**
   - el panel muestra *Sin conexión* en vez de romperse;
   - las herramientas devuelven un error en español, por ejemplo *"No puedo conectar con el clúster: ¿está Docker arrancado?"*;
   - el agente lo dice en el chat.

   Tests: tarea 2 (`test_all_unreachable_when_cluster_down`) y tarea 7 (`test_tool_error_is_reported_in_spanish`).
2. **Faltan claves en `.env`:**
   - la app arranca igual y el panel funciona;
   - el chat avisa en español de que falta `ANTHROPIC_API_KEY`;
   - abrir un PR sin `GITHUB_TOKEN` devuelve 400 con el motivo.

   Tests: tarea 10.
3. **La API de Claude falla a mitad de un turno:**
   - aparece un error en español;
   - la conversación vuelve al estado anterior a la pregunta;
   - la siguiente pregunta funciona.

   Test: tarea 8.
4. **Doble clic en "Abrir Pull Request":** no se abre un segundo PR, se devuelve el mismo. Test: tarea 10.
5. **`main` cambia entre la propuesta y la aprobación:** no se abre el PR y se pide volver a diagnosticar. Test: tarea 6.

---

### Tarea 1: Esqueleto del proyecto y configuración

**Ficheros:** crear `pyproject.toml`, `.gitignore`, `.env.example`, `src/oncall_agent/__init__.py`, `src/oncall_agent/config.py` y `tests/test_config.py`.

**Interfaces que produce:**
- `Settings`: dataclass congelada con estos campos:
  - `anthropic_api_key: str | None`
  - `github_token: str | None`
  - `model: str = "claude-sonnet-5-5"`
  - `max_steps: int = 15`
  - `gitops_repo: str = "JavierMolero12/gitops-manifests"`
  - `gitops_branch: str = "main"`
  - `kube_context: str = "k3d-gitops-demo"`
  - `argocd_namespace: str = "argocd"`
- `load_settings(env: Mapping[str, str] | None = None) -> Settings`. Con `None`, carga `.env` con python-dotenv y lee `os.environ`. Variables: `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `ONCALL_MODEL`, `ONCALL_MAX_STEPS`, `GITOPS_REPO`, `GITOPS_BRANCH`, `KUBE_CONTEXT` y `ARGOCD_NAMESPACE`.
- `EnvironmentSpec(namespace: str, argocd_app: str)` y `ENVIRONMENTS: dict[str, EnvironmentSpec]` con `dev`, `prod` y `monitoring`.
- `WRITABLE_PREFIX = "apps/overlays/dev/"`.

- [ ] **Paso 1:** `git init` en `C:\Users\javi\Desktop\JaviCode\oncall-agent`. Crear el venv: `python -m venv .venv`.
- [ ] **Paso 2:** `pyproject.toml`:
  - estructura `src/` con setuptools y `requires-python = ">=3.13"`;
  - dependencias: `anthropic`, `mcp`, `kubernetes`, `httpx`, `fastapi`, `uvicorn`, `python-dotenv`;
  - extra `dev`: `pytest`, `pytest-asyncio` y `ruff`;
  - `[tool.pytest.ini_options] asyncio_mode = "auto"`.

  `.gitignore` con `.venv/`, `.env`, `__pycache__/`, `.pytest_cache/` y `.superpowers/`. `.env.example` con todas las variables vacías o con su valor por defecto. Instalar: `.venv\Scripts\python -m pip install -e ".[dev]"`.
- [ ] **Paso 3: test que falla.**
  ```python
  def test_defaults_when_env_is_empty():
      s = load_settings({})
      assert s.model == "claude-sonnet-5-5" and s.max_steps == 15
      assert s.gitops_repo == "JavierMolero12/gitops-manifests" and s.kube_context == "k3d-gitops-demo"
      assert s.anthropic_api_key is None and s.github_token is None

  def test_env_overrides():
      s = load_settings({"ONCALL_MODEL": "x", "ONCALL_MAX_STEPS": "3", "GITHUB_TOKEN": "t"})
      assert (s.model, s.max_steps, s.github_token) == ("x", 3, "t")

  def test_environments_map_to_namespaces():
      assert ENVIRONMENTS["dev"].namespace == "sample-api-dev"
      assert ENVIRONMENTS["prod"].argocd_app == "sample-api-prod"
      assert ENVIRONMENTS["monitoring"].namespace == "monitoring"
  ```
  `.venv\Scripts\python -m pytest tests/test_config.py -v` → FAIL (no se puede importar).
- [ ] **Paso 4:** implementar `config.py`. → PASS.
- [ ] **Paso 5: explicación de Javier.**
  - ¿Para qué sirve el `.venv`?
  - ¿Por qué `.env` no se sube a Git y `.env.example` sí?
  - ¿Qué hace `pip install -e`?
- [ ] **Paso 6:** `git add -A && git commit -m "chore: project skeleton and settings"`.

---

### Tarea 2: Estado de cada entorno, para el panel en vivo

**Ficheros:** crear `src/oncall_agent/kube.py`, `src/oncall_agent/status.py`, `tests/factories.py` y `tests/test_status.py`.

**Interfaces que consume:** `Settings` y `ENVIRONMENTS` (tarea 1).

**Interfaces que produce:**
- `ClusterUnavailable(Exception)`, con un mensaje en español.
- `KubeClients`: dataclass con `core: CoreV1Api`, `apps: AppsV1Api` y `custom: CustomObjectsApi`.
- `kube_clients(settings: Settings) -> KubeClients`:
  - carga el kubeconfig con `context=settings.kube_context` y guarda los clientes en caché;
  - si falla la carga, lanza `ClusterUnavailable`.
- `call_kube(fn, *args, **kwargs)`: convierte `urllib3`/`MaxRetryError`/`ConnectionError` en `ClusterUnavailable("No puedo conectar con el clúster: ¿está Docker arrancado?")`.
- `Health(StrEnum)`: `HEALTHY="Sano"`, `STUCK="Actualización atascada"`, `FAILING="Con fallos"` y `UNREACHABLE="Sin conexión"`.
- `FAILURE_REASONS = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "CrashLoopBackOff", "CreateContainerConfigError", "CreateContainerError"}`.
- `EnvironmentStatus`: dataclass con `environment: str`, `health: Health`, `ready_pods: int`, `total_pods: int` y `detail: str`. `detail` tiene la forma `"1 de 2 pods listos"`.
- `pod_failure_reason(pod: V1Pod) -> str | None`: devuelve el `waiting.reason` del contenedor si está en `FAILURE_REASONS`, o `"OOMKilled"` si `last_state.terminated.reason` es `OOMKilled`.
- `classify(pods: list[V1Pod]) -> tuple[Health, int, int]`. Reglas:
  - ignorar los pods con `deletion_timestamp` o en fase `Succeeded`;
  - listo = condición `Ready` a `True`;
  - `FAILING` si hay pods pero ninguno listo;
  - `STUCK` si algún pod tiene `pod_failure_reason` y hay al menos uno listo;
  - `HEALTHY` en el resto de casos.
- `get_environment_status(environment: str, clients: KubeClients) -> EnvironmentStatus`: lista los pods del namespace del entorno.
- `get_all_statuses(clients_factory: Callable[[], KubeClients]) -> list[EnvironmentStatus]`: devuelve dev, prod y monitoring. Si salta `ClusterUnavailable`, los tres quedan `UNREACHABLE` con `detail="No puedo conectar con el clúster"`.

**Nota sobre el diseño:** el diseño dice que el panel usa el Deployment, sus ReplicaSets y sus pods. Durante una actualización atascada conviven en el namespace los pods de la ReplicaSet vieja (listos) y los de la nueva (con su motivo de fallo), así que listar los pods basta para distinguir *Sano*, *Actualización atascada* y *Con fallos*. El Deployment lo lee el agente en la tarea 3. El uso de CPU que aparece en la maqueta (`CPU 1 %`) pasa a la fase 2, junto con las métricas.

- [ ] **Paso 1:** en `tests/factories.py`, escribir `make_pod(name, ready=True, waiting_reason=None, last_terminated_reason=None, image="ghcr.io/javiermolero12/sample-api:ed01aa4", deleting=False, restarts=0, node="k3d-gitops-demo-agent-0")`, que construye un `V1Pod` real con `kubernetes.client.models`.
- [ ] **Paso 2: tests que fallan.**
  ```python
  def test_healthy_when_all_pods_ready():            assert classify([make_pod("a"), make_pod("b")]) == (Health.HEALTHY, 2, 2)
  def test_stuck_when_new_pod_cannot_pull_but_old_serves():
      assert classify([make_pod("old"), make_pod("new", ready=False, waiting_reason="ImagePullBackOff")]) == (Health.STUCK, 1, 2)
  def test_failing_when_no_pod_ready():
      assert classify([make_pod("a", ready=False, waiting_reason="CrashLoopBackOff")])[0] == Health.FAILING
  def test_oomkilled_is_a_failure_reason():
      assert pod_failure_reason(make_pod("a", ready=False, waiting_reason="CrashLoopBackOff", last_terminated_reason="OOMKilled")) in {"CrashLoopBackOff", "OOMKilled"}
  def test_terminating_pods_are_ignored():           assert classify([make_pod("a"), make_pod("b", ready=False, deleting=True)]) == (Health.HEALTHY, 1, 1)
  def test_detail_text_in_spanish():                 # get_environment_status con clientes falsos → detail == "1 de 2 pods listos"
  def test_all_unreachable_when_cluster_down():      # factory que lanza ClusterUnavailable → 3 estados UNREACHABLE
  ```
  → FAIL.
- [ ] **Paso 3:** implementar `kube.py` y `status.py`. → PASS.
- [ ] **Paso 4:** probarlo contra el clúster real: `.venv\Scripts\python -c "from oncall_agent.config import load_settings; from oncall_agent.kube import kube_clients; from oncall_agent.status import get_all_statuses; s=load_settings(); print(get_all_statuses(lambda: kube_clients(s)))"`. Resultado esperado: los tres entornos en `Sano`.
- [ ] **Paso 5: explicación de Javier.**
  - ¿Por qué una actualización atascada mantiene un pod listo? (*rolling update*)
  - ¿Qué es `ImagePullBackOff`?
  - ¿Por qué ignoramos los pods que se están borrando?
- [ ] **Paso 6:** commit `feat: environment health for the live panel`.

---

### Tarea 3: Herramientas de lectura de Argo CD, Deployment y pods

**Ficheros:** crear `src/oncall_agent/tools/__init__.py`, `src/oncall_agent/tools/cluster.py` y `tests/test_tools_cluster.py`.

**Interfaces que consume:** `KubeClients`, `call_kube`, `pod_failure_reason`, `ENVIRONMENTS` y `Settings`.

**Interfaces que produce:** todas devuelven un `dict` con una clave `summary` (frase corta en español para el panel de pasos). Aceptan `environment` solo en `{"dev", "prod"}`; con otro valor lanzan `ValueError("Entorno desconocido: X. Usa dev o prod.")`.
- `get_argocd_app(environment, clients, settings) -> dict`. Claves: `environment`, `app`, `sync_status`, `health_status`, `revision`, `repo_path` y `summary`. `sync_status` y `health_status` llevan el valor original de Argo CD (`Synced`, `Degraded`…), porque la tarea 9 los compara. Solo `summary` va traducido.
  - Ejemplo de `summary`: `"Sincronizado · Degradado"`.
  - Traducción fija: `Synced`→`Sincronizado`, `OutOfSync`→`Desincronizado`, `Healthy`→`Sano`, `Progressing`→`En progreso`, `Degraded`→`Degradado`, `Missing`→`No encontrado`. Un estado desconocido se muestra tal cual.
- `get_deployment(environment, clients) -> dict`. Claves: `name`, `image`, `replicas`, `ready_replicas`, `updated_replicas`, `available_replicas`, `conditions` (`[{type, status, reason, message}]`) y `summary`.
  - Ejemplo de `summary`: `"Imagen ...:v9.9.9 · 1/1 listos · actualización sin completar"`.
- `list_pods(environment, clients) -> dict`. Claves: `pods` (`[{name, ready, phase, restarts, image, failure_reason, node, created}]`) y `summary`.
  - Ejemplo de `summary`: `"1 de 2 pods listos · sample-api-x en ImagePullBackOff"`.

- [ ] **Paso 1: tests que fallan,** con clientes falsos que devuelven objetos de `kubernetes.client.models` y, para Argo CD, un `dict` como el real:
  ```python
  def test_argocd_summary_translates_states():   assert get_argocd_app("dev", fake(sync="Synced", health="Degraded"), S)["summary"] == "Sincronizado · Degradado"
  def test_list_pods_reports_failure_reason():   r = list_pods("dev", fake_pods(...)); assert r["pods"][1]["failure_reason"] == "ImagePullBackOff"; assert "1 de 2 pods listos" in r["summary"]
  def test_deployment_exposes_image_tag():       assert get_deployment("prod", fake_dep(image="…:ed01aa4"))["image"].endswith(":ed01aa4")
  def test_unknown_environment_rejected():       with pytest.raises(ValueError, match="Usa dev o prod"): list_pods("monitoring", clients)
  ```
  → FAIL.
- [ ] **Paso 2:** implementar `tools/cluster.py`. Argo CD se lee con `custom.get_namespaced_custom_object("argoproj.io", "v1alpha1", settings.argocd_namespace, "applications", app)`. → PASS.
- [ ] **Paso 3:** probarlo contra el clúster real con `python -c` sobre las tres funciones y `environment="dev"`. Resultado esperado: `Sincronizado · Sano` y 1 pod listo.
- [ ] **Paso 4: explicación de Javier.**
  - ¿Qué diferencia hay entre *sync status* y *health* en Argo CD?
  - ¿Por qué el agente lee Argo CD a través de la API de Kubernetes?
- [ ] **Paso 5:** commit `feat: read-only cluster tools`.

---

### Tarea 4: `get_pod_events` (**la escribe Javier**)

**Ficheros:** crear `tests/test_tools_events.py` (Claude). Modificar `src/oncall_agent/tools/cluster.py` (Javier).

**Interfaces que produce:** `get_pod_events(environment: str, pod_name: str, clients: KubeClients) -> dict`.
- Devuelve `events` (`[{type, reason, message, count, last_seen}]`, del más reciente al más antiguo, como mucho 10) y `summary`.
- Ejemplo de `summary`: `"3 eventos · último: Failed: Failed to pull image …"` (mensaje cortado a 80 caracteres). Sin eventos: `"Sin eventos recientes"`.
- Usa `core.list_namespaced_event(namespace, field_selector=f"involvedObject.name={pod_name}")`.

- [ ] **Paso 1 (Claude):** escribir los tests:
  - `test_events_sorted_newest_first_and_capped_at_10`
  - `test_summary_mentions_last_reason`
  - `test_no_events_summary`
  - `test_unknown_environment_rejected`

  → FAIL.
- [ ] **Paso 2 (Javier):** implementarla siguiendo el patrón de `list_pods`. Claude solo da pistas si Javier se atasca.
- [ ] **Paso 3:** → PASS. Claude revisa el código con Javier: nombres, casos límite, por qué `field_selector`.
- [ ] **Paso 4: explicación de Javier.** Explica su propio código línea a línea y qué evento aparecerá cuando rompamos la imagen.
- [ ] **Paso 5:** commit `feat: pod events tool (written by Javier)`.

---

### Tarea 5: Cliente de GitHub y herramientas de lectura de Git

**Ficheros:** crear `src/oncall_agent/github.py`, `src/oncall_agent/tools/gitops.py`, `tests/test_github.py` y `tests/test_tools_gitops.py`.

**Interfaces que produce:**
- `GitHubError(Exception)`, con un mensaje en español que incluye el código HTTP.
- `GitHubClient(repo: str, token: str | None, transport: httpx.BaseTransport | None = None)`. Es síncrono, usa `https://api.github.com` y las cabeceras `Accept: application/vnd.github+json` y `X-GitHub-Api-Version: 2022-11-28`, más `Authorization: Bearer` si hay token. Métodos:
  - `list_commits(path: str, limit: int = 5) -> list[dict]`: devuelve `sha`, `short_sha` (7), `message` (primera línea), `author` y `date`.
  - `get_commit(sha: str) -> dict`: devuelve `sha`, `message` y `files: [{filename, patch}]`.
  - `get_file(path: str, ref: str = "main") -> tuple[str, str]`: devuelve el texto y el `blob_sha`.
  - `branch_head(branch: str) -> str`.
  - `create_branch(name: str, from_sha: str) -> None`.
  - `put_file(path: str, content: str, message: str, branch: str, blob_sha: str) -> str`: devuelve el sha del commit.
  - `create_pull_request(title: str, body: str, head: str, base: str) -> dict`: devuelve `number` y `url` (`html_url`).
  - `get_pull_request(number: int) -> dict`: devuelve `number`, `url`, `state`, `merged` y `merge_commit_sha`.
- En `tools/gitops.py`:
  - `list_recent_commits(path: str, github: GitHubClient, limit: int = 5) -> dict`: devuelve `commits` y `summary`. Ejemplo: `"Último cambio: 3f1c2a5 «chore(dev): bump sample-api to v9.9.9»"`.
  - `get_commit_diff(sha: str, github) -> dict`: devuelve `sha`, `message`, `files` y `summary`. Ejemplo: `"Cambia apps/overlays/dev/kustomization.yaml"`.
  - `read_manifest(path: str, github, ref: str = "main") -> dict`: devuelve `path`, `content` y `summary`.

- [ ] **Paso 1: tests que fallan,** con `httpx.MockTransport`:
  - `test_list_commits_shapes_response`
  - `test_get_file_decodes_base64`
  - `test_http_error_becomes_github_error_in_spanish`: un 404 da un `GitHubError` cuyo mensaje contiene `"404"`.
  - `test_list_recent_commits_summary`
  - `test_commit_diff_summary_lists_files`

  → FAIL.
- [ ] **Paso 2:** implementar. → PASS.
- [ ] **Paso 3:** probarlo contra el GitHub real (repo público, sin token): `list_recent_commits("apps/overlays/dev", ...)` devuelve los commits reales.
- [ ] **Paso 4: explicación de Javier.**
  - ¿Qué es un *blob sha* y para qué lo pide GitHub al actualizar un fichero?
  - ¿Por qué el historial de Git es la pista clave del fallo 1?
- [ ] **Paso 5:** commit `feat: GitHub client and git read tools`.

---

### Tarea 6: Propuesta de arreglo y apertura del PR (la única escritura)

**Ficheros:** modificar `src/oncall_agent/tools/gitops.py` y crear `tests/test_tools_write.py`.

**Interfaces que consume:** `GitHubClient` (tarea 5) y `WRITABLE_PREFIX` (tarea 1).

**Interfaces que produce:**
- `ensure_writable(path: str) -> str`:
  - normaliza la ruta a formato POSIX;
  - rechaza `..`, rutas absolutas y cualquier ruta fuera de `apps/overlays/dev/` con `ValueError("Solo puedo proponer cambios en apps/overlays/dev/ …")`.
- `propose_fix(file_path: str, new_content: str, title: str, explanation: str, github) -> dict`. **No tiene efectos.**
  - Valida la ruta y que el fichero exista.
  - Rechaza un contenido idéntico al actual con `ValueError("La propuesta no cambia nada")`.
  - Devuelve `file_path`, `new_content`, `title`, `explanation`, `diff` (`difflib.unified_diff` con `a/` y `b/`), `base_blob_sha` y `summary` (`"Propuesta: cambiar apps/overlays/dev/kustomization.yaml"`).
- `open_pull_request(file_path, new_content, title, explanation, base_blob_sha, github, now: Callable[[], datetime] = …) -> dict`:
  - valida la ruta;
  - si el `blob_sha` actual de `main` no es `base_blob_sha`, lanza `ValueError("El fichero ha cambiado en main desde la propuesta. Vuelve a pedir el diagnóstico.")`;
  - crea la rama `oncall-agent/fix-<AAAAMMDD-HHMMSS>` desde `main`, ejecuta `put_file` en esa rama y abre el PR contra `main`, con `explanation` en el cuerpo y una nota de que lo ha abierto el agente;
  - devuelve `number`, `url`, `branch` y `summary` (`"PR #7 abierto en gitops-manifests"`).

- [ ] **Paso 1: tests que fallan,** con `MockTransport` que registra las peticiones:
  ```python
  def test_rejects_paths_outside_dev():   for p in ["apps/overlays/prod/kustomization.yaml", "apps/overlays/dev/../prod/x", "/etc/passwd", "argocd-apps/dev-app.yaml"]: with pytest.raises(ValueError): ensure_writable(p)
  def test_propose_fix_has_no_side_effects():   # MockTransport: solo peticiones GET
  def test_propose_fix_rejects_unchanged_content():
  def test_open_pr_never_targets_main():        # el PUT de contents usa branch="oncall-agent/fix-…", el POST de pulls usa base="main", head=esa rama
  def test_open_pr_refuses_when_main_changed(): # get_file devuelve otro blob_sha → ValueError "ha cambiado en main", y ninguna petición POST/PUT
  ```
  → FAIL.
- [ ] **Paso 2:** implementar. → PASS.
- [ ] **Paso 3: explicación de Javier.**
  - ¿Por qué `propose_fix` no escribe nada?
  - ¿Por qué comprobamos `base_blob_sha`?
  - ¿Qué impide que el agente toque prod?
- [ ] **Paso 4:** commit `feat: fix proposal and guarded pull request`.

---

### Tarea 7: Servidor MCP

**Ficheros:** crear `src/oncall_agent/mcp_server.py` y `tests/test_mcp_server.py`.

**Interfaces que consume:** todas las funciones de las tareas 2 a 6.

**Interfaces que produce:**
- `build_server(settings: Settings | None = None, clients_factory=None, github_factory=None) -> FastMCP`, con el nombre `"oncall-agent"`. Los clientes se crean **la primera vez que se usan**, para que el servidor arranque aunque el clúster esté apagado.
- `main()` ejecuta el servidor por stdio. Se lanza con `python -m oncall_agent.mcp_server`.
- Herramientas, con su título en español y sus anotaciones:

  | Nombre | `title` | `readOnlyHint` |
  |---|---|---|
  | `get_environment_status(environment)` | Ver estado del entorno | True |
  | `get_argocd_app(environment)` | Consultar Argo CD | True |
  | `get_deployment(environment)` | Ver despliegue | True |
  | `list_pods(environment)` | Ver pods | True |
  | `get_pod_events(environment, pod_name)` | Leer eventos del pod | True |
  | `list_recent_commits(path, limit=5)` | Revisar historial de Git | True |
  | `get_commit_diff(sha)` | Ver cambios de un commit | True |
  | `read_manifest(path)` | Leer manifiesto | True |
  | `propose_fix(file_path, new_content, title, explanation)` | Proponer arreglo | True |
  | `get_pull_request(number)` | Consultar Pull Request | True |
  | `open_pull_request(file_path, new_content, title, explanation, base_blob_sha)` | Abrir Pull Request | **False** (`destructiveHint=False`) |

- Cada descripción explica en una frase cuándo usar la herramienta, que es lo que lee el modelo. La de `propose_fix` dice que no aplica nada y que el humano decide.
- `ClusterUnavailable`, `GitHubError` y `ValueError` se devuelven como error de herramienta con su mensaje en español.

- [ ] **Paso 1: tests que fallan,** usando el transporte en memoria del SDK (`mcp.shared.memory.create_connected_server_and_client_session`; pasar `server._mcp_server` si la versión instalada lo requiere) y factorías falsas:
  - `test_lists_all_tools_with_spanish_titles`: nombres y títulos exactos de la tabla.
  - `test_only_open_pull_request_is_not_read_only`.
  - `test_tool_error_is_reported_in_spanish`: con la factoría que lanza `ClusterUnavailable`, `call_tool("list_pods", {"environment": "dev"})` devuelve `isError` y un texto que contiene `"¿está Docker arrancado?"`.

  → FAIL.
- [ ] **Paso 2:** implementar. Si `@mcp.tool` no acepta `title=` ni `annotations=ToolAnnotations(...)`, actualizar `mcp`. → PASS.
- [ ] **Paso 3:** prueba manual: `npx @modelcontextprotocol/inspector .venv\Scripts\python -m oncall_agent.mcp_server`, o registrarlo en Claude Code. Llamar a `list_pods` con dev contra el clúster real.
- [ ] **Paso 4: explicación de Javier.**
  - ¿Qué es MCP y por qué separar las herramientas del agente?
  - ¿Para qué sirve `readOnlyHint`?
- [ ] **Paso 5:** commit `feat: MCP server exposing the tools`.

---

### Tarea 8: Bucle del agente

**Ficheros:** crear `src/oncall_agent/toolbox.py`, `src/oncall_agent/agent.py`, `tests/test_toolbox.py` y `tests/test_agent.py`.

**Interfaces que produce:**
- En `toolbox.py`:
  - `ToolInfo(name, title, description, input_schema, read_only: bool)`.
  - `ToolOutcome(ok: bool, data: dict | None, text: str)`.
  - `ToolBox(session: ClientSession)`, con estos métodos:
    - `async refresh() -> list[ToolInfo]`;
    - `model_tools() -> list[dict]`: **solo** las de solo lectura, con el formato de Anthropic `{name, description, input_schema}`;
    - `title(name) -> str`;
    - `async call(name, arguments) -> ToolOutcome`: `data` se toma de `structuredContent` o, si no hay, del texto en JSON.
  - `connect_toolbox() -> AsyncContextManager[ToolBox]`: lanza `sys.executable -m oncall_agent.mcp_server` por stdio y ejecuta `refresh()`.
- En `agent.py`:
  - Eventos (dataclasses):
    - `StepStarted(index: int, tool: str, title: str, arguments: dict)`
    - `StepFinished(index: int, tool: str, title: str, ok: bool, summary: str)`
    - `AssistantMessage(text: str)`
    - `FixProposed(proposal: Proposal)`
    - `AgentError(message: str)`
  - `Proposal(id: str, file_path: str, new_content: str, title: str, explanation: str, diff: str, base_blob_sha: str, pr: dict | None = None)`.
  - `Conversation`: con `messages: list[dict]` y `proposals: dict[str, Proposal]`.
  - `SYSTEM_PROMPT: str`, en español. Reglas que debe contener:
    - responder siempre en español;
    - investigar con las herramientas antes de afirmar nada;
    - citar la evidencia de cada conclusión;
    - decir claramente cuando no hay evidencia suficiente;
    - comparar con prod cuando sirva para encontrar la última versión buena;
    - proponer arreglos **solo** con `propose_fix` y **solo** en `apps/overlays/dev/`;
    - nunca afirmar que ha aplicado nada: el humano aprueba con el botón y fusiona en GitHub;
    - si el usuario pide abrir el PR, contestar que pulse el botón *Abrir Pull Request con el arreglo*.
  - `async run_turn(conversation, user_text: str, toolbox: ToolBox, llm, model: str, max_steps: int) -> AsyncIterator[AgentEvent]`. Bucle:
    1. Llamar a `llm.messages.create(model=, max_tokens=2048, system=SYSTEM_PROMPT, tools=toolbox.model_tools(), messages=)`.
    2. Por cada bloque `tool_use`: emitir `StepStarted`, ejecutar `toolbox.call`, emitir `StepFinished` (el `summary` sale de `data["summary"]` o del texto de error) y añadir un `tool_result` con el JSON recortado a 8000 caracteres y `is_error` si falló.
    3. Si la herramienta es `propose_fix` y fue bien, crear la `Proposal` con un id `uuid4().hex[:8]`, guardarla y emitir `FixProposed`.
    4. Si `stop_reason != "tool_use"`, emitir `AssistantMessage` con el texto y terminar.
    5. Tras `max_steps` rondas sin terminar, emitir `AssistantMessage("He llegado al límite de {max_steps} pasos sin una conclusión segura. …")`.
    6. Si `llm` lanza una excepción, emitir `AgentError("No he podido hablar con el modelo: …")` y **restaurar `conversation.messages`** a como estaban antes del turno.

- [ ] **Paso 1: tests que fallan,** con un LLM falso con guion (una lista de respuestas con `content` y `stop_reason`) y una ToolBox falsa:
  - `test_model_only_receives_read_only_tools`: `open_pull_request` no aparece en `tools`.
  - `test_tool_steps_emit_started_and_finished_with_spanish_title`.
  - `test_final_text_is_emitted_and_history_kept`.
  - `test_propose_fix_creates_proposal_event`.
  - `test_stops_at_max_steps_with_spanish_message`.
  - `test_llm_failure_rolls_back_conversation`: después, otro `run_turn` con un LLM que funciona va bien.
  - En `test_toolbox.py`, `test_model_tools_filters_by_read_only_hint` contra el servidor en memoria de la tarea 7.

  → FAIL.
- [ ] **Paso 2:** implementar. → PASS.
- [ ] **Paso 3:** prueba real, con `ANTHROPIC_API_KEY` en `.env`; **Javier crea la clave**. Un script de una línea ejecuta `run_turn` con *"¿Cómo está sample-api en dev?"* contra el clúster sano. Resultado esperado: usa 2 o 3 herramientas y responde en español que está sano.
- [ ] **Paso 4: explicación de Javier.**
  - Dibujar el bucle: modelo → herramienta → resultado → modelo.
  - ¿Por qué hay un límite de pasos?
  - ¿Qué pasa si el modelo se inventa una causa, y cómo lo frena el *system prompt*?
- [ ] **Paso 5:** commit `feat: hand-written agent loop over MCP tools`.

---

### Tarea 9: Vigilante de recuperación tras el PR

**Ficheros:** crear `src/oncall_agent/recovery.py` y `tests/test_recovery.py`.

**Interfaces que produce:**
- `RecoveryUpdate(stage: str, message: str, done: bool)`. Etapas: `waiting_merge`, `merged`, `synced`, `healthy`, `closed` y `timeout`.
- `async watch_recovery(pr_number: int, environment: str, get_pr, get_app, get_status, poll_seconds: float = 5.0, timeout_seconds: float = 900.0, sleep=asyncio.sleep, clock=time.monotonic) -> AsyncIterator[RecoveryUpdate]`:
  - `get_pr`, `get_app` y `get_status` son síncronas y se llaman con `asyncio.to_thread`;
  - cada etapa se emite **una sola vez**, al cambiar a ella.
- Mensajes fijos:
  - `waiting_merge`: `"Esperando a que revises y fusiones el PR #{n} en GitHub."`
  - `merged`: `"PR #{n} fusionado. Esperando a que Argo CD lo aplique…"`
  - `synced`: `"Argo CD ha aplicado el commit {sha[:7]}."` (cuando `get_app(env)["revision"] == merge_commit_sha` y `sync_status == "Synced"`)
  - `healthy`: `"Resuelto. Argo CD ha aplicado el cambio y {env} vuelve a estar sano."` (`done=True`; cuando `get_status(env).health == Health.HEALTHY`)
  - `closed`: `"Has cerrado el PR #{n} sin fusionarlo. No se ha aplicado nada."` (`done=True`)
  - `timeout`: `"Han pasado {min} minutos y {env} no se ha recuperado. Revisa Argo CD."` (`done=True`)

- [ ] **Paso 1: tests que fallan,** con funciones falsas que avanzan por fases, `sleep` falso y reloj falso:
  - `test_full_happy_path_emits_each_stage_once`
  - `test_closed_pr_stops`
  - `test_timeout_stops`
  - `test_waits_for_matching_revision_not_any_sync`

  → FAIL.
- [ ] **Paso 2:** implementar. → PASS.
- [ ] **Paso 3: explicación de Javier.**
  - ¿Por qué esperamos a que la `revision` de Argo CD coincida con el commit del merge?
  - ¿Qué pasaría si fusionas otro commit encima?
- [ ] **Paso 4:** commit `feat: recovery watcher after the fix PR`.

---

### Tarea 10: Backend web (FastAPI)

**Ficheros:** crear `src/oncall_agent/web/__init__.py`, `src/oncall_agent/web/app.py`, `src/oncall_agent/web/__main__.py` y `tests/test_web.py`.

**Interfaces que consume:** `get_all_statuses`, `connect_toolbox`/`ToolBox`, `run_turn`, `Conversation`, `watch_recovery`, `GitHubClient.get_pull_request`, `get_argocd_app` y `get_environment_status`.

**Interfaces que produce:**
- `create_app(settings=None, toolbox_factory=connect_toolbox, llm_factory=None, statuses=None, recovery_deps=None) -> FastAPI`.
  - El `lifespan` abre la ToolBox una sola vez.
  - El estado vive en `app.state`: una `Conversation` y un `asyncio.Lock` para el chat.
- Rutas:
  - `GET /`: `static/index.html`. `/static` sirve los ficheros estáticos.
  - `GET /api/status`: `{"environments": [{environment, health, ready_pods, total_pods, detail}]}`.
  - `POST /api/chat`, con cuerpo `{"message": str}`: responde en `text/event-stream` con los eventos `step_started`, `step_finished`, `assistant_message`, `fix_proposed` (`{id, file_path, title, explanation, diff}`), `error` y `done`, cada uno con `data:` en JSON.
    - Sin `ANTHROPIC_API_KEY`: un único evento `error` con `"Falta ANTHROPIC_API_KEY en el fichero .env"` y después `done`.
    - Si el lock está ocupado: un evento `error` con `"Ya estoy respondiendo a otra pregunta"`.
  - `POST /api/conversation/reset`: empieza una conversación nueva.
  - `POST /api/proposals/{id}/pull-request`: llama por MCP a `open_pull_request` con los datos **guardados** de la propuesta y devuelve `{number, url}`.
    - Sin `GITHUB_TOKEN`: 400 con `"Falta GITHUB_TOKEN en el fichero .env"`.
    - Propuesta desconocida: 404.
    - **Si `proposal.pr` ya existe, devuelve ese PR sin llamar a GitHub.**
    - Un error de la herramienta devuelve 409 con su mensaje.
  - `GET /api/proposals/{id}/watch`: SSE con eventos `recovery` (`{stage, message, done}`).
- `python -m oncall_agent.web` arranca uvicorn en `127.0.0.1:8765`.

- [ ] **Paso 1: tests que fallan,** con `TestClient` y dependencias falsas:
  - `test_status_endpoint_shape`
  - `test_chat_streams_agent_events`
  - `test_chat_without_api_key_reports_spanish_error`
  - `test_concurrent_chat_is_rejected`
  - `test_open_pr_uses_stored_proposal`: los argumentos de la llamada MCP coinciden con la propuesta.
  - `test_open_pr_twice_returns_same_pr`: `open_pull_request` se llama una sola vez.
  - `test_open_pr_without_token_is_400`
  - `test_watch_streams_recovery_updates`

  → FAIL.
- [ ] **Paso 2:** implementar. → PASS.
- [ ] **Paso 3: explicación de Javier.**
  - ¿Qué es SSE y por qué no esperamos a la respuesta completa?
  - ¿Por qué el PR lo abre el backend con la propuesta guardada y no el modelo?
- [ ] **Paso 4:** commit `feat: web backend with streaming chat`.

---

### Tarea 11: Interfaz de chat (según la maqueta aprobada)

**Ficheros:** crear `src/oncall_agent/web/static/index.html`, `app.js` y `styles.css`. Modificar `tests/test_web.py`.

**Interfaces que consume:** las rutas de la tarea 10.

**Qué decide el plan:** la estructura y los textos de la maqueta `2026-11-04-oncall-agent-mockup.html`.
- Barra superior: *Agente de guardia · clúster gitops-demo*.
- Chat a la izquierda: `#chat-log`, `#chat-form` y `#chat-input` con el placeholder *"Escribe una pregunta…"*.
- A la derecha:
  - `#status-panel` con el título *Estado del clúster · en vivo*, que consulta `/api/status` cada 3 s;
  - `#steps-panel` con el título *Pasos del agente*.
- Etiquetas de salud y su color: *Sano* en verde, *Actualización atascada* en ámbar, *Con fallos* en rojo y *Sin conexión* en gris.
- El chat usa `fetch` y lee el stream (EventSource no admite POST).
- `fix_proposed` muestra el `diff` en un `<pre>` y el botón **"Abrir Pull Request con el arreglo"**. Al pulsarlo:
  1. añade la burbuja del usuario *"Abrir Pull Request"*;
  2. desactiva el botón;
  3. hace la llamada POST;
  4. muestra una tarjeta *#número · título* con el enlace *Ver en GitHub*;
  5. abre un `EventSource` contra `/watch`, que añade cada actualización como paso y la final como mensaje del agente.
- Botón *Nueva conversación*, que llama a `/api/conversation/reset` y limpia la pantalla.

- [ ] **Paso 1: test que falla.** `test_index_served_in_spanish`: `GET /` es 200 y contiene `"Agente de guardia"`, `"Estado del clúster"` y `"Pasos del agente"`. → FAIL.
- [ ] **Paso 2:** implementar los tres ficheros. → PASS.
- [ ] **Paso 3:** verificación visual. Arrancar `python -m oncall_agent.web`, abrir `http://127.0.0.1:8765` en el navegador y comprobar:
  - el panel muestra los tres entornos *Sano*;
  - una pregunta muestra los pasos con sus títulos en español;
  - la página se parece a la maqueta.
- [ ] **Paso 4: explicación de Javier.** ¿Cómo llega un paso del agente desde Python hasta la pantalla?
- [ ] **Paso 5:** commit `feat: chat UI per approved mockup`.

---

### Tarea 12: Scripts de la demo (preparación, romper y restaurar)

**Ficheros:** crear `scripts/setup_argocd_polling.py`, `scripts/demo_faults.py` y `tests/test_demo_faults.py`.

**Interfaces que produce:**
- `scripts/setup_argocd_polling.py`:
  - hace un *merge-patch* de `argocd-cm` (namespace `argocd`) con `timeout.reconciliation: "30s"`;
  - reinicia el StatefulSet `argocd-application-controller` y el Deployment `argocd-repo-server` con la anotación `kubectl.kubernetes.io/restartedAt`;
  - al terminar, imprime el valor nuevo.

  Antes de ejecutarlo, confirmar que `argocd-cm` **no** está gestionado desde `gitops-manifests`. En el árbol de ficheros revisado el 2026-11-04 no aparece.
- `scripts/demo_faults.py`:
  - `set_image_tag(kustomization_text: str, tag: str) -> str`: sustituye solo el valor de la línea `newTag:`, conservando las comillas y los comentarios. Si no encuentra la línea, lanza `ValueError`.
  - Comandos:
    - `python scripts/demo_faults.py break image` pone `newTag` a `v9.9.9` en `apps/overlays/dev/kustomization.yaml` con un commit **directo a `main`** y el mensaje `chore(dev): bump sample-api to v9.9.9`.
    - `python scripts/demo_faults.py restore` restaura `newTag` con la etiqueta de la imagen que **corre ahora en prod** (`get_deployment("prod")["image"]`) y el mensaje `fix(dev): restore sample-api image tag (demo reset)`.

  Los scripts son herramientas de Javier, no del agente, y usan `GitHubClient.put_file` con `branch="main"`.

- [ ] **Paso 1: tests que fallan:**
  - `test_set_image_tag_replaces_only_tag` (conserva el comentario sobre `newTag` y las comillas)
  - `test_set_image_tag_without_newtag_raises`

  → FAIL.
- [ ] **Paso 2:** implementar. → PASS.
- [ ] **Paso 3:** ejecutar `setup_argocd_polling.py` contra el clúster y comprobar el valor con `kubectl -n argocd get cm argocd-cm -o jsonpath="{.data.timeout\.reconciliation}"`. Resultado esperado: `30s`.
- [ ] **Paso 4: explicación de Javier.**
  - ¿Por qué romper con un commit y no con `kubectl`?
  - ¿Por qué bajamos el sondeo de Argo CD y qué implica en un clúster de verdad?
- [ ] **Paso 5:** commit `feat: demo fault scripts and Argo CD polling setup`.

---

### Tarea 13: Demo completa contra el clúster real, README y repositorio

**Ficheros:** crear `README.md` y `docs/demo-fase-1.md` (guion de la demo).

- [ ] **Paso 1:** comprobar la preparación:
  - `.env` tiene `ANTHROPIC_API_KEY` y `GITHUB_TOKEN`;
  - el token es *fine-grained*, solo para `JavierMolero12/gitops-manifests`, con Contents y Pull requests en lectura y escritura. **Lo crea Javier**, guiado por Claude.
- [ ] **Paso 2:** ensayo completo:
  1. `python scripts/demo_faults.py break image`;
  2. esperar hasta ~1 min a que el panel marque dev como *Actualización atascada*;
  3. preguntar *"¿Qué le pasa a sample-api en dev?"*.

  Lo esperado es que el agente:
  - use Argo CD, los pods, los eventos y Git;
  - nombre `v9.9.9`, el commit culpable y la etiqueta buena de prod;
  - aparezca el botón.

  Después: pulsar el botón, ver el PR en GitHub, **Javier lo fusiona** y el chat muestra *Resuelto* mientras el panel vuelve a *Sano*.
- [ ] **Paso 3:** repetir el ensayo dos veces más y apuntar en `docs/demo-fase-1.md` los tiempos y cualquier fallo. Si el agente falla en alguna, se ajusta el *system prompt* o la descripción de la herramienta, se añade un test si aplica y se repite.
- [ ] **Paso 4:** `README.md` en inglés:
  - qué es;
  - un diagrama del flujo;
  - el modelo de seguridad: solo lectura, el modelo sin herramientas de escritura, el doble control humano y **la limitación del token documentada** con la alternativa de producción (GitHub App y reglas en `main`);
  - instalación en Windows;
  - preparación de Argo CD;
  - cómo ejecutar la demo;
  - aviso de no hacer push a `sample-api` durante la demo, porque su CI reescribe `newTag`.
- [ ] **Paso 5: explicación de Javier.** Hace la demo entera **en voz alta, como en la entrevista**, sin ayuda.
- [ ] **Paso 6:** commit `docs: README and phase 1 demo script`.
- [ ] **Paso 7: con el OK explícito de Javier**, crear el repositorio público `JavierMolero12/oncall-agent` y subir la rama: `gh repo create JavierMolero12/oncall-agent --public --source . --push`.
