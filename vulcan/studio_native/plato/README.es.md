# Plato's Hoard

[English](README.md)

Editor local para convertir imágenes 2D en siluetas editables y relieve 3D imprimible. Sube un PNG, ajusta las capas en el editor del navegador y exporta la silueta negra, un SVG vectorial o un STL extruido. El conversor original de una sola operación sigue disponible en `/legacy`.

## Arranque local

Necesita Python 3 y las dependencias de `requirements.txt`.

```sh
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
```

Abre [http://127.0.0.1:5000/editor](http://127.0.0.1:5000/editor). En otros sistemas, usa `.venv/bin/python`. Flask guarda documentos y trabajos del editor bajo `data/` y limita las subidas a 16 MB.

## Faustus y Hoard Hub

[`faustus-plugin.json`](faustus-plugin.json) permite al Hub descubrir y arrancar la app. Faustus accede a la API REST del editor mediante el puente MCP REST genérico indicado en el manifiesto. Plato **no** implementa `/api/agent/call`: el puente y el Hub se ocupan del descubrimiento y arranque.

### Eventos de exportación

Con `python app.py` en marcha, cuando termina un trabajo de exportación que incluye STL, Plato descomprime cada STL del paquete en `data/exports/<id del trabajo>/<familia>/` y anuncia cada uno en el bus de la familia como `plato.export.done {path, ref, title, format, job_id, document_id, layer_id, family}`; `ref` es `hoard://plato/export/<id del trabajo>` y `title` el nombre del documento y de la capa. La regla recomendada del Hub entrega el `path` a la biblioteca de modelos (`model_import_file`). La exportación no tiene formato 3MF, así que solo se anuncia STL. Los eventos de un mismo paquete se envían con 5,5 s de separación (`PLATO_EVENT_SPACING_S`) porque esa regla ignora una repetición dentro de su enfriamiento de 5 s. Plato escribe `data/mcp-token` al arrancar (el Hub identifica al emisor por él). Un vigilante en segundo plano detecta que el trabajo ha terminado, sin esperar a que el editor lo pregunte. Sin el Hub no cambia nada: los STL se descomprimen igualmente y el evento se descarta. La vista previa heredada `/api/process` no es una exportación y no emite nada.

`hoard_link/` es la biblioteca compartida de la familia, incluida sin cambios; solo se cargan sus módulos de biblioteca estándar (`family`, `atomic`, `tokens`, `guard`), con un nombre de paquete privado en `plato_family.py`, así que los requisitos de Plato no cambian. De ella toma la guardia de peticiones (cada petición se comprueba: `Host` local, `Origin` del mismo sitio y Fetch Metadata; `PLATO_ALLOWED_HOSTS` abre a propósito un nombre de red local o de tailnet), la escritura atómica de los documentos del editor (fichero temporal, fsync, reemplazo con reintentos en Windows) y el fichero de token. El servidor escucha ahora en `127.0.0.1` (`PLATO_HOST` y `PLATO_PORT` lo cambian; antes escuchaba en todas las interfaces).

## Copiloto de Faustus

Todas las páginas de la app (el editor de capas en `/editor` y la página heredada en `/legacy`) tienen un botón flotante **Faustus**. Abre un panel lateral con el chat de Faustus, para pedir cosas mientras miras la placa: el agente ve qué documento y qué capa tienes abiertos y los lee o cambia con la misma API REST del editor que expone el puente MCP REST. La conversación, el modelo, las herramientas y la memoria viven en Faustus; Plato no guarda ningún bucle de chat ni historial.

Lo que el panel envía en cada turno (`window.hoardCopilot.context()` en [`static/editor/hoard-copilot.js`](static/editor/hoard-copilot.js), con las funciones auxiliares en [`copilot_context.mjs`](static/editor/copilot_context.mjs)):

- `project`: el documento abierto (`external_project_id` = id del documento, `title` = su nombre). Una conversación por documento: al abrir otro documento se muestra la conversación de ese documento.
- `active_objects`: el documento (`kind: "document"`: id, nombre, revisión, lienzo en mm, número de capas y el árbol de capas con id / título / padre) y, si hay una capa seleccionada, esa capa (`kind: "layer"`: id, título, padre, visible, bloqueada, asset, pose). El documento se lee del servidor en el momento del turno, así que un cambio anterior del agente ya está dentro. Sin documento (la página heredada, un documento que ya no existe) es `[]`.
- `view.editor`: ancho, alto y márgenes del lienzo que has escrito y todavía no has aplicado (`{kind: "canvas", dirty: true, fields}`), solo cuando los hay.

Al terminar un turno el editor recarga el documento si su revisión ha cambiado (lo cambió el agente), lo redibuja, quita la selección de una capa que ya no existe y conserva lo que estabas escribiendo en los campos del lienzo. El historial de deshacer empieza de nuevo en ese punto, porque sus instantáneas son anteriores al cambio del agente. El botón flotante queda abajo a la derecha del área del lienzo, a la izquierda del inspector, y nunca tapa sus campos.

Rutas añadidas por [`plato_copilot.py`](plato_copilot.py), un blueprint pequeño de Flask sobre las piezas del copiloto de Hoard Link copiadas en el proyecto (`CopilotRelay`, `multipart_context`, `inject_launcher`, `LAUNCHER_JS`), con las mismas cuatro rutas que el copiloto de Hoard Link para servidores FastAPI y Node:

| Ruta | Qué hace |
|---|---|
| `GET /api/copilot/info` | id y nombre de la app, un id de espacio de datos estable (`data/copilot-space-id`) y de dónde viene el panel |
| `GET /api/copilot/panel.js` | el elemento `<faustus-chat>`, servido por Faustus a través del relé |
| `POST /api/copilot/proxy` | cada petición del panel; los turnos de chat van como Server-Sent Events sin búfer; el token de Faustus se queda en el servidor, `origin.app_id` / `data_space_id` se fuerzan y solo se alcanzan las rutas de copiloto de Faustus (`/api/hoard-copilot/`, `/api/chat/`, `/api/session(s)`, `/api/chat_stream`) |
| `GET /api/copilot/launcher.js` | el script del botón y el panel, añadido a cada página HTML que sirve la app (nunca a las respuestas de `/api/`) |

Faustus se encuentra con `PLATOS_FAUSTUS_URL` o `HOARD_FAUSTUS_URL` (más `HOARD_FAUSTUS_TOKEN`), o con la configuración de Hoard Link, y si no, `127.0.0.1:7000` / `7001`. **Para desactivarlo** arranca la app con `HOARD_COPILOT=0`: sin rutas, sin botón y sin nada inyectado. `faustus-plugin.json` lo declara en `x-family.copilot`.

Los campos pendientes del lienzo siguen en el contexto aunque el servidor tenga una revisión nueva. El refresco conserva los campos modificados, también los vaciados y lo escrito mientras llega la respuesta; los campos sin modificar siguen la versión nueva del documento.

## Verificación

```sh
python -m pytest tests/editor -q
python -m pytest tests -q
```

`implementation_status.json` documenta las tareas del editor terminadas y los resultados de prueba conocidos. La suite completa histórica registró un fallo previo de IoU para una estrella; comprueba la ejecución actual antes de afirmar que pasa. `npm test` es un marcador, no la suite Python.
