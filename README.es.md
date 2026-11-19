# Vulcan's Hoard

Vulcan's Hoard es un estudio 3D local: una biblioteca con búsqueda de tus modelos imprimibles y editores de relieve, imagen a 3D, siluetas, CAD medido y terreno.

[English](README.md)

## Qué es

Vulcan indexa los archivos STL, 3MF, OBJ, GLB, glTF y FBX que guardas en tu PC. Mide cada modelo, genera una miniatura en la CPU, encuentra duplicados y guarda etiquetas, notas y una ficha de marketplace por modelo. Un asistente accede a la misma biblioteca por MCP, de modo que puede encontrar un modelo, dar sus dimensiones reales y redactar su ficha sin inventar nada.

Alrededor de la biblioteca hay seis módulos que se activan por separado: la propia biblioteca (Vulcan), relieve editable de figuras y proyectos de Blender (Gepetto), generación y refinado de imagen a 3D (Daedalus), composición y extrusión de siluetas (Plato), CAD medido con ensamblajes (Heron) y terreno y ciudades imprimibles (Gulliver). Todos empiezan apagados en el primer uso: abre Vulcan, activa la función que necesitas y páralo desde la misma pantalla. Listar las herramientas MCP no arranca nada. Los proyectos compartidos conservan los identificadores y revisiones nativos y las referencias a los archivos exportados con SHA-256, así que un STL exportado se puede indexar en la biblioteca sin convertir el proyecto nativo.

La biblioteca se queda en la máquina: SQLite con FTS5 y miniaturas WebP, sin cuenta.

## Qué hace

**Biblioteca**
- Las carpetas raíz las eliges tú, con globs de inclusión y exclusión, política de miniaturas por raíz y tamaño mínimo de archivo. Los escaneos son incrementales (tamaño y fecha, luego SHA-256), usan un pool de procesos, muestran progreso y tiempo estimado y vigilan los cambios en las carpetas.
- Geometría con `trimesh`: triángulos, caja envolvente en mm, volumen, superficie, estanqueidad, número de cuerpos y estimación de unidades. GLB y glTF se miden como escenas; FBX pasa por Blender si está instalado.
- Miniaturas en Python puro (numpy y Pillow, sin GPU ni OpenGL) y un visor three.js con órbita y rejilla de cama de impresión.
- Búsqueda con SQLite FTS5 (sin distinguir acentos, por prefijo) sobre nombre, etiquetas, notas, colección y texto de la ficha, con filtros por raíz, formato, etiqueta, colección, álbum, estanqueidad, ficha, duplicados, estado, tamaño, rango de tamaño registrado y rango de triángulos.
- Duplicados exactos por hash y casi duplicados por triángulos, volumen y extensiones, con una página de revisión para ambos.
- Colecciones por carpeta y álbumes que armas a mano; etiquetas y notas sobreviven a los reescaneos.
- Comparación de revisiones de dos STL, OBJ o 3MF en unidades físicas, con alineación rígida opcional y vista de desviación.
- Variantes LOD con desviación máxima medida, renders multivista, fotos de ficha renderizadas con Blender, exportación GLB para web, validación glTF con el validador de Khronos y descubrimiento del laminador.
- Texturizado con IA por proyección de vistas (Blender y ComfyUI), una mesa 3D con muchos modelos mediante impostores octaédricos y habitaciones construidas desde un boceto o un plano fotografiado hasta un GLB texturizado.

**Fichas de marketplace**
- Una ficha por modelo (título, descripción en markdown, etiquetas, categoría, precio orientativo, idioma) y un botón de copiar ficha.
- Fichas de carpeta para productos que son una carpeta entera: `cults3d.json` con título, descripción en inglés y exactamente 20 etiquetas, validada contra una plantilla opcional, con estados ninguna, borrador, comprobada y aprobada, redacción con modelo local, un modo esqueleto determinista para todas las carpetas a la vez y exportación a CSV, Markdown o JSON.
- Organizar una colección: pegas una lista de grupos y miembros y Vulcan planifica, aplica y deshace los movimientos de carpetas sin borrar ni sobrescribir nada.
- Exportación de catálogo combinado que Mercator puede importar.

**Estudio**
- De texto o especificación a CAD paramétrico en milímetros: primitivas, booleanas, patrones, bocetos acotados, extrusiones, revoluciones, parámetros con nombre, informes de intención de diseño, ajuste de una receta a una malla medida, regeneración y variantes.
- Bocetos con restricciones, operaciones sobre caras con nombre, ensamblajes con relaciones y comprobaciones de interferencia y holgura de rosca (Heron).
- Modelado con código: un programa corto escrito por el modelo de la familia, ejecutado en un proceso aislado sobre Blender y comprobado contra las medidas de la descripción.
- Inspección y preparación para un destino (configuradores web, impresión FDM) y para activos animados (comprobación de rig y clips, recorte, remuestreo, importación y exportación FBX), siempre escribiendo un archivo nuevo con recibo.
- Imagen a 3D con reconstrucción multivista conjunta y recorte de fondo uniforme. El generador distribuye las etapas entre las GPU reservadas. También incluye relieve editable de figuras, extrusión de siluetas, terreno y ciudades imprimibles.
- Análisis espacial: dónde colocar sedes de servicio para cubrir la demanda dentro de un tiempo de viaje y cribado de parcelas por pendiente, acceso y restricciones.

La interfaz está en español.

## Cómo ejecutarlo

Requisitos: Windows 10/11 (también funciona en Linux y macOS), Python 3.11 o superior con SQLite FTS5, Node 22 solo para compilar el cliente. La biblioteca va solo con CPU. Blender, ComfyUI y el entorno GPU de Daedalus son opcionales y solo los usan las operaciones que los necesitan.

```bat
git clone <este repo> vulcan-hoard
cd vulcan-hoard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install
npm run build
venv\Scripts\python -m vulcan
```

Abre http://127.0.0.1:5186, ve a Carpetas y añade una carpeta. `python scripts/launch.py` lo arranca en un puerto libre y abre el navegador; `python scripts/dev.py` lanza los servidores de desarrollo; `python scripts/selftest.py <carpeta>` escanea una carpeta en un directorio de datos temporal e imprime un informe. Las interfaces del estudio se compilan con `python scripts/build_studio.py` (ver `docs/STUDIO.md`).

| Variable | Por defecto | Significado |
|---|---|---|
| `VULCAN_PORT` / `PORT` | 5186 | Puerto preferido (`PORT_STRICT=1` lo fija) |
| `VULCAN_DATA_DIR` | `<repo>/data` | Base de datos, `mcp-token`, miniaturas, planes guardados |
| `VULCAN_ALLOWED_HOSTS` | | Nombres de host extra aceptados tras un túnel, separados por comas |
| `VULCAN_THUMBS` | 1 | 0 omite las miniaturas |
| `VULCAN_THUMB_SIZE` | 512 | Lado de la miniatura en píxeles |
| `VULCAN_MAX_FILE_MB` | 300 | Los archivos mayores se listan como omitidos |
| `VULCAN_SCAN_WORKERS` | CPU menos 2 | Procesos que calculan hash, analizan y renderizan |
| `VULCAN_SKIP_SMALL_BYTES` | 0 | Tamaño mínimo por defecto de las raíces nuevas |
| `VULCAN_WATCH` | 1 | 0 desactiva la vigilancia de carpetas |
| `VULCAN_AUTOSTART` | 0 | 1 reescanea todas las raíces al arrancar |
| `VULCAN_FAUSTUS_URL` | | Endpoint de Faustus (también `HOARD_FAUSTUS_URL`) |
| `VULCAN_BLENDER` | la instalación más reciente | Ejecutable de Blender |

El servidor escucha en 127.0.0.1 y solo responde a hosts locales; para entrar desde el móvil por un túnel, lista los nombres de host en `VULCAN_ALLOWED_HOSTS`. La redacción con modelo local usa Hoard Link, que encuentra el registro de Faustus o un servidor llama.cpp u Ollama en loopback; fija uno con `<DATA_DIR>/backend.json` o las variables `HOARD_LLM_*`.

## Conexión con Faustus y el Hoard Hub

- Vulcan es un plugin de Faustus (`faustus-plugin.json`, puerto 5186) y miembro de la familia del Hoard Hub; lleva Hoard Link copiado en `vulcan/hoard_link/`.
- El botón flotante de Faustus abre el panel copiloto compartido en cada vista y le pasa el modelo, la ficha de carpeta, la raíz o el álbum, los filtros, la selección y el borrador del formulario actuales. La conversación y la ejecución son de Faustus. Ver `docs/copilot.md`.
- Abierta junto a un chat de Faustus, la página registra `vulcan_project`, `vulcan_filter`, `vulcan_module` y `vulcan_view` mediante el App Dock.
- MCP: `mcp_server.py` es un puente stdio hacia `GET /api/agent/tools` y `POST /api/agent/call` con el token de `<DATA_DIR>/mcp-token` (`VULCAN_URL`, `VULCAN_TOKEN_FILE`). El catálogo tiene más de doscientas herramientas, agrupadas en: biblioteca (búsqueda, información, fichas, etiquetas, notas, borrado, estadísticas, duplicados, raíces, reescaneo, importación, comparación), fichas de carpeta y organización, CAD (esquema, desde texto, regenerar, intención de diseño, ajuste), inspección y preparación para destino, animación, validación glTF, texturizado, modelado con código, impostores, habitaciones, ensamblajes Heron, análisis espacial y las herramientas propias de los módulos. Los nombres largos tienen alias cortos. Las instrucciones de las herramientas piden al asistente describir los modelos solo a partir de datos medidos.
- Skills de la biblioteca de la familia para los agentes conectados: `print-ready-3d-model`, `web-3d-asset-pipeline`, `blender-asset-production` y `verification-loop`.
- Las identidades antiguas de MCP y Hub siguen funcionando mediante adaptadores de compatibilidad en frío (`docs/NATIVE-CONNECTIONS.md`).

Las rutas REST cuelgan de `/api/` (salud, estado, raíces, modelos, búsqueda, colecciones, fichas de carpeta, organizar, CAD, inspección de activos, animación, copiloto, agente); la lista completa está en `docs/API.md`.

## Estructura

| Ruta | Contenido |
|---|---|
| `vulcan/` | Paquete Python: escáner, geometría, miniaturas, base de datos, API, CAD, herramientas MCP |
| `vulcan/studio_native/` | Los módulos del estudio (Daedalus, Gepetto, Gulliver, Heron, Plato) |
| `vulcan/hoard_link/` | Hoard Link copiado |
| `client/` | Interfaz React de la biblioteca |
| `scripts/` | Lanzador, servidor de desarrollo, autoprueba, compilación del estudio, ayudas en Node |
| `docs/` | Guías: estudio, ajuste CAD, comparación, herramientas de malla, habitaciones, análisis espacial, copiloto |
| `tests/` | Tests de pytest y Node |
| `data/` | Base de datos, token, miniaturas, exportaciones (no se versiona) |

## Tests

```bat
venv\Scripts\python -m pytest -q
```

## Límites

- La biblioteca escanea STL, 3MF, OBJ, GLB, glTF y FBX; STEP, AMF y PLY no se escanean.
- El volumen de una malla abierta es una estimación por volumen con signo. Los grupos de casi duplicados son sugerencias, no prueba de copias.
- El generador CAD escribe STL (sin STEP) y su motor booleano integrado es Python puro, así que las especificaciones muy pesadas tardan segundos.
- El grosor mínimo de pared es una estimación por muestreo, no una cota.
- Los bocetos de habitación producen una escena paramétrica con muros verticales, no medidas certificadas.

## Licencia

GPL-3.0 para el estudio combinado. Las concesiones de los componentes originales se conservan en [NOTICE-STUDIO.md](NOTICE-STUDIO.md).
