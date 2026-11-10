# Demo de la fase 1: imagen inexistente en dev

Guion para enseñar el agente de principio a fin, en voz alta y como en la entrevista. Dura unos
5 minutos. Cada paso tiene **qué haces**, **qué deberías ver** y **qué dices**.

## Antes de empezar (5 minutos antes)

- [ ] Docker Desktop arrancado y el clúster `gitops-demo` en marcha. Si está parado:
      `k3d cluster start gitops-demo` (si falla, mira la tabla del final).
- [ ] Las cuatro apps de Argo CD en *Synced / Healthy*:
      `kubectl --context k3d-gitops-demo -n argocd get applications`
- [ ] Argo CD mira Git cada 30 s: `kubectl --context k3d-gitops-demo -n argocd get cm argocd-cm -o jsonpath="{.data.timeout\.reconciliation}"` devuelve `30s`.
      Si no, `.venv\Scripts\python scripts\setup_argocd_polling.py`.
- [ ] `.env` tiene `ANTHROPIC_API_KEY` y `GITHUB_TOKEN`.
- [ ] El servidor está arrancado (`.venv\Scripts\python -m oncall_agent.web`) y
      <http://127.0.0.1:8765> abierto en el navegador.
- [ ] GitHub abierto en otra pestaña, en los Pull Requests de `gitops-manifests`.
- [ ] **Nadie hace push a `sample-api`** durante la demo: su CI reescribe `newTag` en dev.

## El guion

### 1. Presentar (30 s)

- **Ves:** el panel de estado con dev y prod en verde, *Sano*.
- **Dices:** «Es un agente de guardia para mi clúster GitOps. Investiga con herramientas de solo
  lectura, me explica la causa con evidencias y me propone el arreglo como Pull Request. Nada se
  aplica sin que yo lo apruebe dos veces.»

### 2. Romper dev con un mal commit (1 min)

- **Haces:** `.venv\Scripts\python scripts\demo_faults.py break image`
- **Ves:** «Hecho: commit … en main». En un minuto, más o menos, dev pasa a *Actualización atascada*.
- **Dices:** «Simulo un error humano: alguien sube a `main` un tag de imagen que no existe. Lo
  rompo con un commit y no con kubectl porque en GitOps la verdad es Git: si lo cambiara a mano,
  Argo CD lo desharía. El pod nuevo no puede descargar la imagen, pero el viejo sigue sirviendo,
  así que la app no se cae: la actualización se queda atascada.»

### 3. Preguntar al agente (1-2 min)

- **Haces:** escribir «¿Qué le pasa a sample-api en dev?».
- **Ves:** los pasos en vivo (Argo CD, despliegue, pods, eventos, historial de Git, manifiesto) y
  una respuesta que nombra `v9.9.9`, el commit culpable y el tag bueno (el del pod viejo, que es
  el mismo que corre en prod). Debajo, el diff y el botón *Abrir Pull Request con el arreglo*.
- **Dices:** «Cada paso es una herramienta del servidor MCP. El modelo decide cuál usar, pero solo
  tiene herramientas de lectura. Fíjate en que cita la evidencia: el evento del pod y la línea
  del commit. El arreglo de momento es solo una propuesta: un diff.»

### 4. Abrir el PR (30 s)

- **Haces:** pulsar el botón.
- **Ves:** «He abierto el Pull Request. No se aplicará hasta que tú lo apruebes.» y la tarjeta
  del PR con el enlace a GitHub.
- **Dices:** «Primer control humano. El PR no lo abre el modelo: lo abre el backend con la
  propuesta que guardó, en una rama nueva y solo en el overlay de dev.»

### 5. Fusionar y ver la recuperación (1-2 min)

- **Haces:** en GitHub, revisar el diff y fusionar.
- **Ves:** en el chat, «PR #… fusionado. Esperando a que Argo CD lo aplique…», después «Argo CD
  ha aplicado el commit …» y por último «Resuelto…», en verde. El panel vuelve a *Sano*.
- **Dices:** «Segundo control humano: el merge. Argo CD aplica `main` y el agente lo comprueba en
  tres pasos: el PR está fusionado, Argo CD ha sincronizado justo ese commit y dev está sano.»

## Si algo se tuerce

| Qué pasa | Qué haces |
|---|---|
| El agente no llega a una conclusión o se equivoca | Repetir la pregunta; si sigue, `demo_faults.py restore` y explicar qué haría para mejorarlo |
| «No puedo conectar con el clúster» | Docker o el clúster están parados: arrancar Docker Desktop, `k3d cluster start gitops-demo` y esperar a que vuelva el panel |
| `k3d cluster start` falla con «ports are not available … permisos de acceso» | Windows ha reservado el puerto de la API del clúster (se ve con `netsh interface ipv4 show excludedportrange protocol=tcp`). En PowerShell como administrador: `net stop winnat`, `k3d cluster start gitops-demo` y `net start winnat` |
| Error de GitHub al abrir el PR | Revisar `GITHUB_TOKEN` (permisos y caducidad) |
| Hay que dejarlo todo como estaba | `.venv\Scripts\python scripts\demo_faults.py restore` |

## Registro de ensayos

Se rellena en cada ensayo. Si el agente falla, se ajusta el *system prompt* o la descripción de
la herramienta, se añade un test si aplica y se repite.

| Ensayo | Fecha | Romper → *Atascada* | Pregunta → propuesta | Merge → *Sano* | Fallos y notas |
|---|---|---|---|---|---|
| 1 | 2026-11-10 | 64 s | < 2 min | 18 s | Bien a la primera (PR #2): 7 herramientas de lectura, citó el evento `Failed to pull image` y el commit `42da8d0` y propuso `ed01aa4`, la imagen de prod. Antes de empezar, Docker estaba parado y Windows había reservado el puerto del clúster (ver la tabla de arriba) |
| 2 | pendiente | | | | |
| 3 | pendiente | | | | |
