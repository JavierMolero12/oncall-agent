# Diseño: oncall-agent, un agente de guardia para el clúster gitops-demo

Fecha: 2026-11-04 · Autor: Javier Molero (con Claude) · Estado: borrador pendiente de aprobación

## 1. Intención y porqué

Javier quiere presentarse a la vacante **AI & Platform Engineer** de AstroKube (Ciudad Real, híbrido). El puesto consiste en construir agentes de IA que automatizan la ingeniería y la operación de plataformas cloud native. En el proceso de selección hay una **charla técnica sobre algo que hayas construido** y un **ejercicio práctico** (construir un agente o una automatización pequeña), revisado por dos ingenieros.

Javier ya tiene una plataforma GitOps funcionando en local (repos `sample-api` y `gitops-manifests`, clúster k3d `gitops-demo`, Argo CD, Prometheus y Grafana). Le falta la parte de IA.

`oncall-agent` es esa parte: un **agente de guardia** que diagnostica fallos en el clúster y propone el arreglo como un Pull Request, respetando GitOps. Se enseñará en directo en la entrevista.

**Condición principal:** Javier tiene que **entender el proyecto lo suficiente para explicarlo y defender cada decisión** ante dos ingenieros. Ya le pasó con la plataforma GitOps: la tiene creada, pero no la entiende. Esto no se puede repetir. Por eso la forma de construirlo (sección 7) forma parte de los requisitos, no es un detalle.

La candidatura se enviará **cuando el proyecto esté acabado y Javier lo entienda**, no antes. No hay fecha límite fija.

## 2. Qué es (la demo)

1. Javier rompe a propósito `sample-api` en el entorno **dev**, con un commit en `gitops-manifests`, usando un script.
2. Argo CD aplica el cambio (en dev la sincronización es automática). La **nueva versión no consigue arrancar**, mientras la anterior sigue atendiendo gracias a la actualización progresiva (*rolling update*): la actualización se queda **atascada**. *(Decisión de Claude tras la revisión: así se comporta Kubernetes de verdad con 1 réplica y la estrategia por defecto, y además sirve para explicar por qué existen las actualizaciones progresivas.)*
3. En el chat del agente Javier pregunta, en español: *"¿Qué le pasa a sample-api en dev?"*.
4. El agente investiga solo con sus herramientas: estado en Argo CD, pods, eventos, logs, historial de Git y, si sirve, métricas. Cada paso aparece en el panel de pasos.
5. El agente explica en español **qué falla, por qué y en qué evidencias se basa**, y propone un arreglo concreto.
6. Javier pulsa *"Abrir Pull Request con el arreglo"* (o se lo pide en el chat). El agente abre un PR en `gitops-manifests` y avisa de que no se aplicará hasta que Javier lo apruebe.
7. Javier revisa y fusiona el PR **en GitHub**, no desde el chat.
8. Argo CD aplica el arreglo. El agente lo detecta y confirma en el chat que dev vuelve a estar sano. El panel de estado pasa de rojo a verde.

## 3. Objetivos

- **Tres tipos de fallo** diagnosticados de principio a fin, con su arreglo por PR:

  | # | Fallo | Cómo se rompe (commit en `gitops-manifests`, solo dev) | Síntoma en Kubernetes | Dónde está la pista clave | Arreglo propuesto |
  |---|---|---|---|---|---|
  | 1 | Imagen que no existe | `newTag` inventado en `apps/overlays/dev/kustomization.yaml` | `ImagePullBackOff` / `ErrImagePull` | Eventos del pod + historial de Git | Volver a la última etiqueta que funcionaba (p. ej. la que corre en prod) |
  | 2 | La app se cae al arrancar | `LOG_LEVEL` con un valor inválido (p. ej. `verbose`) como variable `env` del contenedor, mediante un patch del **Deployment** en el overlay de dev (no editando el ConfigMap: un cambio en un ConfigMap no reinicia los pods y el fallo no aparecería) | `CrashLoopBackOff` | Logs del contenedor (traza de error de uvicorn) + historial de Git | Quitar o corregir esa variable |
  | 3 | Se queda sin memoria | Límite de memoria muy bajo para dev (p. ej. `16Mi`) mediante un patch en el overlay de dev | `OOMKilled` / `CrashLoopBackOff` | Estado del contenedor (`lastState.terminated.reason`) + recursos del Deployment + historial de Git | Volver a un límite razonable (el de la base, `128Mi`) |

- **Orden:** primero el fallo 1 entero (fase 1). Después los fallos 2 y 3 (fase 2).
- **Interfaz web de chat en español**, aprobada en maqueta (`2026-11-04-oncall-agent-mockup.html`, en esta misma carpeta; es la referencia visual): chat a la izquierda; a la derecha arriba, **estado del clúster en vivo** (dev, prod y monitoring, con pods listos); a la derecha abajo, **pasos del agente** (cada herramienta usada, con nombre en español, y lo que encontró).
  - Estados de cada entorno *(decisión de Claude tras la revisión)*: **Sano**; **Actualización atascada** (la versión nueva no arranca y la anterior sigue atendiendo); **Con fallos** (no queda ningún pod listo). El panel calcula el estado a partir del Deployment, sus ReplicaSets y sus pods, no solo de la salud que informa Argo CD, porque Argo CD tarda en marcar un despliegue atascado como *Degraded*.
- **Respuestas con evidencia:** cada afirmación del diagnóstico se apoya en algo que el agente ha visto con una herramienta y que aparece en el panel de pasos.
- **Scripts para romper y restaurar** cada fallo, para que la demo sea repetible y no dependa de la suerte.
- **Repositorio público** `oncall-agent` en el GitHub de Javier, con un README en inglés que explique qué hace, cómo funciona y cómo reproducir la demo.
- **Material para la entrevista:** un documento con las preguntas que probablemente harán los ingenieros (cómo razona el agente, por qué solo lee, qué pasa si el modelo se equivoca, seguridad del token, cómo lo llevarías a producción…) y respuestas que Javier haya escrito o validado.

## 4. Fuera de alcance (no objetivos de la v1)

- Tocar **prod**: ni para romper ni para arreglar. El agente puede *leer* prod (para comparar versiones), pero sus PRs solo modifican el overlay de dev.
- Que el agente **fusione o apruebe** PRs. Nunca.
- Que el agente **cambie el clúster directamente** (`kubectl apply/patch/delete` o equivalentes).
- Ejecutar el agente **dentro del clúster** (queda como mejora futura).
- Login o autenticación en la interfaz web: solo se usa en local.
- Más clústeres, otras apps aparte de `sample-api`, o más tipos de fallo.
- Historial de conversaciones persistente.
- Interfaz en inglés.

## 5. Lo que haría que fracasara aunque funcione (anti-objetivos)

- **Que Javier no pueda explicarlo.** Es el fracaso principal.
- Que el agente **arregle cosas saltándose Git**: rompería la idea central del proyecto.
- Que el agente **se invente una causa** y la presente como un hecho. Si no encuentra evidencia suficiente, debe decirlo.
- Una demo frágil: que falle en directo por depender de tiempos, de suerte o de pasos manuales delicados.
- Código "mágico" escondido detrás de un framework de agentes que Javier no pueda explicar.

## 6. Restricciones y entorno real (comprobado el 2026-11-04)

- **Máquina:** portátil Windows 10 con 16 GB de RAM y Docker Desktop (WSL2). Python 3.13.1 en Windows. `gh` CLI autenticado como `JavierMolero12`. El clúster se arranca con `docker start` de los contenedores `k3d-gitops-demo-*` (la CLI `k3d` no está instalada en Windows). Contexto de kubectl: `k3d-gitops-demo`.
- **Clúster:** k3s v1.35.5 con 2 nodos. Namespaces `argocd`, `monitoring`, `sample-api-dev`, `sample-api-prod`. Los namespaces de la app aplican Pod Security `restricted`.
- **Argo CD:** App-of-Apps (`root` → `sample-api-dev`, `sample-api-prod`, `monitoring`). Las apps leen de `https://github.com/javiermolero12/gitops-manifests.git` (rama `main`) y tienen `automated` con `prune: true` y `selfHeal: true`. Consecuencia: un commit en `main` llega solo al clúster, y un cambio manual en el clúster se revierte.
- **`gitops-manifests`** (público, `main` sin protección de rama): `apps/base/` (deployment, service, configmap, ingress), `apps/overlays/dev/` (`kustomization.yaml` con `images.newTag`, `patch-env.yaml`), `apps/overlays/prod/` (`hpa.yaml`, `patch-resources.yaml`), `argocd-apps/`, `platform/monitoring/`. Tiene un workflow `validate.yaml` (kustomize + kubeconform + políticas). **La CI de `sample-api` reescribe `newTag` de dev con un commit automático en cada push a `main` de `sample-api`.**
- **`sample-api`:** FastAPI. Expone `/healthz`, `/api/v1/info` y `/api/v1/metrics`. Lee `PORT` y `LOG_LEVEL` del entorno al arrancar (`app/__main__.py`), con la configuración en el ConfigMap `sample-api-config` (`envFrom`). Recursos base: requests 50m / 64Mi, limits 250m / 128Mi. Tiene probes de startup, readiness y liveness sobre `/healthz`. Imagen distroless que no corre como root.
- **Prometheus** (`monitoring/prometheus:9090`) solo recoge métricas propias y de `sample-api` (`sample_api_http_requests_total`, `sample_api_http_request_duration_seconds_*`) y la métrica `up`. **No hay métricas de memoria de contenedores** (ni cAdvisor ni kube-state-metrics). En el clúster sí está `metrics-server` (de k3s), que ofrece el uso actual de CPU y memoria.
- **Presupuesto:** API de Claude de pago, con unos pocos dólares cargados. Javier crea la clave y el token de GitHub él mismo; nadie más los maneja.

## 7. Cómo se construye (decidido)

### Decisiones de Javier
- **El "cerebro" es la API de Claude** (de pago, poco gasto).
- **Interfaz: chat web**, con el diseño B+C de la maqueta y **todo en español**.
- **Se construye por piezas, despacio, entendiendo cada una** antes de pasar a la siguiente. Al terminar cada pieza, Javier tiene que poder explicarla con sus palabras y la sesión de construcción incluye esa explicación. Prima entender sobre la velocidad.
- **Quién escribe el código: modo mixto.** Claude escribe cada pieza **en trozos pequeños** y explica cada trozo antes de seguir. **Javier escribe él mismo una herramienta por fase**, siguiendo el patrón de las de Claude (por ejemplo, en la fase 2, la herramienta que lee los logs del contenedor para el fallo 2). Claude revisa ese código con él. **Una pieza solo se da por terminada cuando Javier la explica con sus palabras**: qué hace, por qué está hecha así y qué pasaría si fallara. Por eso el trabajo se hace en sesión con Javier y no se delega en subagentes que construyan piezas sin él.
- **Conceptos de la plataforma GitOps** *(decisión de Claude tras la revisión)*: cuando una pieza del agente toque un concepto de la plataforma existente (overlays de Kustomize, `selfHeal`, App-of-Apps, actualizaciones progresivas, la CI que reescribe `newTag`), la sesión lo explica en ese momento. El estudio completo de la plataforma va por otra vía (curso LFS158 y sesiones de exploración del clúster) y no forma parte de este plan.

### Decisiones de Claude (Javier las ha aceptado, pero son de Claude)
- **Python** para todo el backend. **FastAPI** como servidor web, como en `sample-api`.
- **Frontend sin framework ni paso de compilación:** HTML, CSS y JavaScript servidos por FastAPI. Los pasos del agente llegan en tiempo real al navegador (mecanismo concreto a criterio del constructor; SSE es la opción natural).
- **Bucle del agente escrito a mano** con el SDK oficial de Anthropic (bucle de uso de herramientas): el modelo pide una herramienta, el código la ejecuta, le devuelve el resultado y se repite hasta que responde. Sin frameworks de agentes que lo oculten. Con un **límite de pasos** por pregunta para controlar el gasto.
- **Modelo:** Claude Sonnet 5.5 (`claude-sonnet-5-5`), configurable.
- **Herramientas en un servidor MCP** (SDK oficial de MCP para Python). El backend del chat se conecta a él como cliente. Así las mismas herramientas se pueden usar desde Claude Code o Claude Desktop, lo que sirve como argumento en la entrevista.
- **Acceso al clúster de solo lectura** con el cliente oficial de Kubernetes para Python y el kubeconfig local:
  - Argo CD: leyendo los recursos `Application` a través de la API de Kubernetes, sin login en Argo CD.
  - Prometheus: a través del *service proxy* de la API de Kubernetes, sin `port-forward`.
  - Uso de recursos: `metrics-server` (`metrics.k8s.io`).
  - Las herramientas no pueden escribir en el clúster.
- **GitHub:** API REST con un **token fine-grained que solo tiene acceso a `gitops-manifests`** (Contents y Pull requests de lectura y escritura). Hay **una sola herramienta de escritura**, *abrir PR*. Crea una rama, hace un commit y abre el PR, y está **limitada a ficheros de `apps/overlays/dev/`**. No existe ninguna herramienta para fusionar, aprobar ni empujar a `main`.
- **Doble control humano:** el agente solo abre el PR cuando Javier lo pide en el chat. El PR solo se aplica cuando Javier lo fusiona en GitHub.
- **Scripts de romper y restaurar** (uno por fallo) que hacen commits directos a `main` de `gitops-manifests` en el overlay de dev, simulando un mal commit humano. El de restaurar sirve de red de seguridad si la demo se tuerce.
- **Tiempos de Argo CD en la demo** *(decisión de Claude tras la revisión)*: el clúster es local, así que los webhooks de GitHub no llegan y Argo CD solo mira Git cada pocos minutos. Eso pasaría dos veces en la demo (tras romper y tras fusionar el PR). Se baja el intervalo de sondeo de Argo CD (`timeout.reconciliation` en `argocd-cm`) a unos 30 s como paso de preparación documentado en el README. Si `argocd-cm` está gestionado desde `gitops-manifests`, se cambia allí. El agente **no** fuerza refrescos: seguiría siendo de solo lectura.
- **Límite del token de GitHub, documentado como limitación de la v1** *(decisión de Claude tras la revisión)*: el token fine-grained actúa como Javier, que es administrador de `gitops-manifests`, así que técnicamente podría hacer push a `main` o fusionar. Lo que lo impide es que el código del agente no tiene esas funciones. Se documenta en el README y en las preguntas de entrevista, junto con cómo se haría en producción (identidad propia como GitHub App, reglas en `main` y revisión obligatoria). No se implementa en la v1.
- **Secretos** (clave de Anthropic, token de GitHub) en un `.env` local que no se sube a Git, con un `.env.example` en el repo.
- **Idiomas:** código, comentarios y README en inglés (como el resto de repos de Javier). Interfaz y respuestas del agente en español.
- **Ubicación:** carpeta `C:\Users\javi\Desktop\JaviCode\oncall-agent`, que será el repositorio público `JavierMolero12/oncall-agent`.
- **Se ejecuta en el portátil**, fuera del clúster.

## 8. Fases

1. **Fase 1, fallo 1 de principio a fin.** Herramientas de lectura necesarias, bucle del agente, chat con los dos paneles, script de romper y restaurar, herramienta *abrir PR* y confirmación posterior de que dev se ha recuperado.
2. **Fase 2, fallos 2 y 3.** Las herramientas que falten (logs, estado del contenedor, recursos y métricas) y sus scripts.
3. **Fase 3, pulido para la candidatura.** ServiceAccount y ClusterRole de solo lectura para el agente, desplegados por GitOps en `gitops-manifests` (mínimo privilegio). README con una grabación o GIF de la demo. Documento de preguntas de entrevista. Entrada nueva en el portfolio (`Portfolio Javier Molero v3`).

## 9. A criterio del constructor

- La estructura de carpetas y módulos del proyecto, y cómo se reparte el código entre el servidor MCP y el backend del chat.
- El conjunto exacto de herramientas y sus nombres (en código, en inglés), siempre que cubran la tabla de fallos y que el panel muestre nombres en español.
- El texto del *system prompt* del agente, respetando: responder en español, basarse en evidencias, decir cuándo no lo sabe y no proponer cambios fuera de dev.
- Cómo se refresca el panel de estado del clúster (sondeo periódico o similar) y cómo detecta el agente que dev se ha recuperado tras el PR.
- Los valores concretos para romper (etiqueta inventada, valor inválido de `LOG_LEVEL`, límite de memoria). **Se debe comprobar en el clúster real** que cada uno produce el síntoma esperado, en particular que un `LOG_LEVEL` inválido hace caer uvicorn y que el límite elegido provoca `OOMKilled`.
- La estrategia de tests (por ejemplo, herramientas probadas contra respuestas simuladas y una prueba de extremo a extremo manual contra el clúster).
- Qué hacer si la CI de `sample-api` reescribe la etiqueta de dev a mitad de una demo (basta con no hacer push a `sample-api` durante la demo, pero conviene documentarlo).
