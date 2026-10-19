# Sculptor’s Hoard

[English](README.md)

**Convierte texturas pintadas de personajes en relieve 3D editable.** Es un espacio de trabajo local para preparar mapas de altura con Blender Figure Tools. Interpreta qué representa cada superficie del modelo antes de decidir cómo desplazarla: un párpado, una pupila, una hebilla y una sombra pintada no deben recibir el mismo tratamiento.

## Qué hace

- **Preparación semántica:** observa la figura completa y asocia partes visibles con regiones UV y posiciones 3D. Conserva evidencias por pieza para que el artista pueda revisar lo que ha interpretado.
- **Posado desde imagen de referencia, experimental:** propone una pose de rig ACNH a partir de una tarjeta de personaje u otra imagen y guarda una copia independiente de Blender. [Uso y límites](docs/REFERENCE_POSE.md).
- **Comprobación en 3D real:** Blender ejecuta los nodos instalados de Figure Tools; la revisión compara el resultado con el original y un control de altura uniforme, incluidas vistas cercanas de la cara.
- **Mapas grandes:** salida PNG RGBA a resolución nativa con alturas de 16 bits, procesamiento por filas con memoria acotada, padding UV y caché por contenido.
- **Edición útil:** ajustes en lenguaje natural, alturas vinculadas, fusión y división de grupos, contraste de relieve, guardado automático, versiones, deshacer y rehacer.
- **Producción local:** cola persistente de figuras, recuperación por fase y selección y observación de modelos locales.
- **Evidencia reproducible:** conserva entradas y respuestas de modelos, máscaras, planes, mapas, parámetros de render, mediciones de geometría y escenas Blender.

## Arranque

El entorno de producción comprobado es **Windows, Python 3.12/3.13, Node.js, Blender 5.0 con Figure Tools y Ollama**. Figure Tools es una dependencia externa; este repositorio no incluye su código ni sus recursos.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
npm ci
npm run build
.venv\Scripts\python start.py
```

También puedes abrir **Abrir Sculptors Hoard.vbs** tras instalarlo. La ventana de escritorio arranca su servicio local; al cerrarla, los trabajos ya en cola pueden continuar. Si Blender está en otra ruta, configura `SCULPTORS_HOARD_BLENDER`. El archivo `.blend` es la fuente principal de mallas visibles, pose, materiales e imágenes aplicadas. Los recursos privados y datos de trabajo generados no se publican en Git.

En **Equipo y lotes**, elige un modelo de visión instalado, escanea escenas `.blend`, prepara figuras, revisa el resultado y exporta mapas nativos, una escena editable o STL. El complemento `blender/relief_bridge.py` comunica Blender con la aplicación.

## Familia Hoard

`faustus-plugin.json` permite al Hub descubrir y arrancar la aplicación; no hay contrato `/api/agent/call`. Cuando una evaluación termina y ha producido `figure-ready.stl`, la aplicación lo anuncia en el bus de la familia como `gepetto.export.done {path, ref, title, format, project_id, job_id}` (`ref` es `hoard://gepetto/export/<id del trabajo>` y `title` el nombre del proyecto seguido de «relief»). La regla recomendada del Hub entrega el `path` a la biblioteca de modelos (`model_import_file`). La aplicación escribe `data/mcp-token` al arrancar (el Hub identifica al emisor por él); sin el Hub no cambia nada. El STL es la única malla que exporta Sculptor’s Hoard (no hay 3MF) y los paquetes de mapas PNG no se anuncian. `backend/hoard_link/` es la biblioteca compartida de la familia, incluida sin cambios; la aplicación toma de ella la guardia de peticiones (Host local, Origin y Fetch Metadata en cada petición; `GEPETTO_ALLOWED_HOSTS` abre a propósito un nombre de red local o de tailnet), el fichero de token, las escrituras atómicas de `project.json`, del historial de deshacer y de las ejecuciones de pose (`atomic`) y el ejecutor de procesos de Blender (`proc`, que mata todo el árbol de Blender al vencer el tiempo y guarda todo lo que imprimió). `/api/health` devuelve `service` junto a la clave anterior `application`.

## Copiloto de Faustus

Un botón flotante **Faustus** (abajo a la derecha) abre un panel lateral con el chat de Faustus, servido por Faustus a través del relé propio de esta aplicación (`/api/copilot/*`; el token de Faustus se queda en el servidor). La conversación, el modelo y las herramientas viven en Faustus; la aplicación no guarda bucle de chat ni historial. Se conserva una conversación por proyecto.

Cada turno lleva lo que está abierto, leído con la propia API de la aplicación: el **proyecto** (id, nombre, número de texturas y modelos, revisión, aprobado, propuesta/evaluación presentes), la **textura** abierta (id, nombre, regiones preparadas, aprobada), la **región** seleccionada (`<id de textura>:<número de región>`, nombre, altura, geometría), la **evaluación** si existe, y la página (taller, equipo y lotes, o posar desde imagen) con la superficie 3D/UV/relieve, el modo de imagen y la pestaña del inspector. Lo que está sin guardar va en `view.editor`: el nombre o la altura de una región en edición (con los valores guardados al lado) y una indicación escrita pero no enviada. Tras un turno del agente la lista de proyectos y el proyecto abierto se recargan solos; los campos que se están editando se conservan mientras los intactos siguen los valores actualizados.

Se apaga con `HOARD_COPILOT=0` (desaparecen el relé, el lanzador y el botón). Faustus se localiza con `HOARD_FAUSTUS_URL` (y `HOARD_FAUSTUS_TOKEN`) o con la configuración de Hoard Link. Los cambios de región envían sólo los campos modificados a un endpoint parcial y conservan los cambios del agente en otros campos. La guarda de revisión de la lista completa sigue disponible. El contexto se captura antes de las lecturas asíncronas del proyecto.

## Pruebas y límites

```powershell
python -m pytest -q
npm run build
```

Las pruebas deterministas cubren máscaras, salida nativa, padding UV, continuidad, comandos, conflictos de revisión, historial y recuperación de colas. Las pruebas con Blender y visión reales se documentan aparte en [evaluación](docs/EVALUATION.md). Los modelos de visión aún se equivocan en detalles semánticos; sus propuestas y la geometría resultante requieren revisión. Una malla cerrada por sí sola no demuestra que una figura esté lista para imprimir.

Código bajo [licencia MIT](LICENSE); modelos, complementos y recursos externos conservan sus licencias.
