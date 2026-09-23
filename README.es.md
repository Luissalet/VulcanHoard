# Vulcan's Hoard

Tu biblioteca personal de modelos para impresión 3D, indexada en tu propio ordenador. Señala las carpetas donde guardas tus STL, 3MF y OBJ y la aplicación mide cada modelo (medidas en mm, triángulos, volumen, superficie, si es estanco, cuántos cuerpos tiene), genera una miniatura en la CPU, encuentra duplicados y modelos casi iguales, y guarda por modelo etiquetas, notas y una ficha para la tienda (título, descripción, etiquetas, categoría). Un asistente accede a la misma biblioteca por MCP: puede encontrar «aquel busto de dragón de la primavera pasada», citar sus medidas reales y redactar la ficha por ti sin inventarse nada que la geometría no diga.

Todo se queda en tu máquina: SQLite con FTS5, miniaturas en WebP, sin cuentas y sin red. La aplicación nunca llama a un modelo de lenguaje.

## Qué hace

- **Carpetas raíz** = carpetas que elegís, con globs de inclusión/exclusión (por defecto `**/*.stl, **/*.3mf, **/*.obj`; se ignoran `.git`, `node_modules` y las carpetas ocultas), activar/desactivar y vigilancia opcional de cambios (reescanea sola).
- **Escaneo incremental**: tamaño y fecha primero, después SHA-256; solo se vuelve a leer lo que cambia; lo borrado se purga; un archivo tocado pero idéntico no se relee. Leer, medir y renderizar corre en varios procesos a la vez (`VULCAN_SCAN_WORKERS`, por defecto núcleos − 2); el hilo del escáner solo recorre la carpeta, decide qué ha cambiado y escribe los resultados, así que la base de datos sigue teniendo un único escritor. Progreso por carpeta: archivos hechos/total, archivos por segundo, tiempo restante, archivo actual y errores. Los archivos de más de `VULCAN_MAX_FILE_MB` (300) se listan como «sin analizar» con una nota en vez de cargarse en memoria.
- **Opciones por carpeta** para árboles generados enormes (un programa que exporta miles de mallas de capas): `Miniaturas` = todos | solo carpeta raíz y primer nivel | ninguna; `ignorar archivos menores de N bytes` (no se listan); y los globs de exclusión, que son la herramienta adecuada para saltarse una carpeta generada por su nombre (por ejemplo `**/export_job_*/**` o `**/normal_registered/**`), un patrón por carpeta con `**` a ambos lados.
- **Geometría** con `trimesh`: triángulos, vértices, caja envolvente en mm, volumen (cm³), superficie (cm²), estanco, número de cuerpos, y un aviso de unidades cuando el tamaño hace improbable que sean milímetros (`pulgadas` por debajo de 5 mm, `metros` por debajo de 1 mm, `demasiado grande` por encima de 1,5 m). Las unidades del 3MF se convierten a mm.
- **Miniaturas** en Python puro (numpy + Pillow, sin OpenGL: funcionan en cualquier Windows sin depender del controlador gráfico): cámara ortográfica desde delante-derecha-arriba con Z arriba (orientación de impresión), sombreado plano con una luz fija, resolución de profundidad por píxel sobre los triángulos ordenados de lejos a cerca, supermuestreo 2×, WebP de 512 px con fondo transparente, guardadas en `<DATA_DIR>/thumbs/<sha256>.webp` (los archivos idénticos comparten una). Las mallas de menos de 5 000 triángulos salen a 384 px con supermuestreo 1,5×, las demás a 512 px con 2×; las de más de 300 000 triángulos se simplifican solo para la miniatura, y las mallas cerradas y bien orientadas se pintan sin sus caras traseras. Una malla de capa de 300 triángulos tarda ~60 ms y un escaneo de 300 000 triángulos ~0,9 s; si borráis la carpeta de miniaturas se regeneran en el siguiente escaneo sin volver a medir.
- **Nombres**: el nombre del archivo limpiado (`dragon_bust-v2` → `Dragon bust v2`), editable. **Colección** = la carpeta inmediata por defecto, editable. **Etiquetas** (en minúsculas, sin repetidas) y **notas** por modelo, que sobreviven a los reescaneos.
- **Fichas**: título, descripción (markdown), etiquetas, categoría, precio orientativo e idioma, con origen (`manual` | `asistente`) y fecha. El botón «Copiar ficha» produce el texto plano listo para pegar en el formulario de la tienda.
- **Búsqueda** (FTS5, sin distinguir tildes, prefijo de cada palabra) sobre nombre, etiquetas, notas, carpeta y texto de la ficha, con filtros por carpeta raíz, formato, etiqueta, colección, álbum, estanco, con ficha, solo duplicados, estado, tamaño, medidas y triángulos; orden por nombre, fecha, tamaño, triángulos o relevancia. Paginada.
- **Duplicados**: exactos = mismo SHA-256; parecidos = mismos triángulos, volumen ±1 % y medidas ±1 % (una exportación ASCII y otra binaria del mismo modelo, una copia reescalada, el mismo modelo en dos formatos), agrupados y ofrecidos como sugerencia.
- **Colecciones**: las carpetas agrupan solas; los **álbumes** los montáis a mano (nombre + modelos).
- **Interfaz**: Galería, Modelo (visor three.js con órbita, ajuste automático y rejilla en la base; tabla de geometría; etiquetas, notas y colección; editor de ficha con «Copiar ficha»; duplicados), Colecciones, Carpetas, Estadísticas y Ajustes. Funciona en el móvil y se puede instalar como aplicación web.

## Requisitos

- Windows 10/11 (también Linux/macOS), Python 3.11 o superior (3.13 va bien), Node 22 solo para construir el cliente.
- Solo CPU. Sin OpenGL ni GPU: las miniaturas se rasterizan en numpy. El visor del navegador usa WebGL como cualquier página web.
- El `sqlite3` de Python debe tener FTS5 (las versiones oficiales de Windows lo traen). Si falta, la aplicación avisa al arrancar.

## Instalar y arrancar (Windows)

```bat
git clone <este repositorio> vulcan-hoard
cd vulcan-hoard
python -m venv venv
venv\Scripts\pip install -r requirements.txt
npm install
npm run build
venv\Scripts\python -m vulcan
```

Abre http://127.0.0.1:5186, entra en **Carpetas** y añade una carpeta. El primer escaneo de una biblioteca grande tarda un rato (medir más una miniatura por archivo, entre 0,1 y 2 s por modelo según su tamaño); **Carpetas** muestra el progreso y los siguientes escaneos solo leen lo que ha cambiado.

- `python scripts/launch.py` arranca en un puerto libre y abre el navegador.
- `python scripts/dev.py` lanza uvicorn con recarga y el servidor de Vite.
- `python scripts/selftest.py <carpeta> [--no-thumbs] [--query "..."]` escanea una carpeta en un directorio temporal e imprime recuentos, tiempos, totales por formato, errores, grupos de parecidos y resultados de búsqueda.

## Configuración (variables de entorno)

| Variable | Por defecto | Significado |
| --- | --- | --- |
| `VULCAN_PORT` / `PORT` | `5186` | Puerto preferido; `PORT_STRICT=1` lo fija, si no se usa el primero libre. |
| `VULCAN_DATA_DIR` | `<repo>/data` | Base de datos, `mcp-token`, `thumbs/`. |
| `VULCAN_ALLOWED_HOSTS` | | Nombres de host adicionales aceptados detrás de un túnel (ver más abajo). |
| `VULCAN_THUMBS` | `1` | `0` no genera miniaturas (solo medidas). |
| `VULCAN_THUMB_SIZE` | `512` | Lado de la miniatura en píxeles (64–2048). |
| `VULCAN_MAX_FILE_MB` | `300` | Los archivos mayores se listan sin analizar. |
| `VULCAN_SCAN_WORKERS` | núcleos − 2 (mín. 1) | Procesos que leen, miden y renderizan; `1` lo hace todo en el hilo del escáner. |
| `VULCAN_SKIP_SMALL_BYTES` | `0` | Tamaño mínimo por defecto para las carpetas nuevas (0 = listar todo). |
| `VULCAN_WATCH` | `1` | `0` desactiva la vigilancia de carpetas. |
| `VULCAN_AUTOSTART` | `1` | `0` no reescanea las carpetas al arrancar. |

### Acceso desde el móvil (a través de un túnel)

El servidor escucha en 127.0.0.1 y solo responde a peticiones cuyo `Host` sea `localhost`, `127.0.0.1` o `[::1]`. Para entrar desde el móvil a través de un túnel que ponga la aplicación delante (una red privada, un proxy inverso), indicad los nombres de host adicionales en `VULCAN_ALLOWED_HOSTS`, separados por comas, exactos o `*.sufijo`: `VULCAN_ALLOWED_HOSTS=mi-pc.example,*.ts.net`. El puerto y las mayúsculas no importan, y el `Origin` de las llamadas a la API también tiene que corresponder a uno de esos hosts (con cualquier esquema o puerto). Las peticiones *fetch* desde otras webs se siguen rechazando; abrir la aplicación desde otra página (un enlace, un bookmarklet, el menú de compartir) es una navegación normal y funciona.

## Conectar el asistente (MCP)

`mcp_server.py` es un puente stdio: pide la lista de herramientas a la aplicación en marcha y reenvía cada llamada a `POST /api/agent/call` con el token de `data/mcp-token`. Nunca abre la base de datos. Faustus detecta la aplicación por `/api/health` y rellena la conexión con `faustus-plugin.json`.

Herramientas: `models_search` (buscar con filtros), `model_info` (todo sobre un modelo), `model_listing_get` / `model_listing_set` (leer y escribir la ficha), `model_tag` (etiquetas), `model_note` (notas), `models_stats` (estadísticas, cola, archivos/s y tiempo restante; cacheado 5 s mientras escanea), `models_dupes` (duplicados exactos o parecidos), `models_add_root` (añadir carpeta), `models_rescan` (reescanear), `models_recent` (últimos modelos). Las instrucciones que acompañan a las herramientas le dicen al asistente que describa un modelo solo con los datos de la geometría y lo que digáis vosotros, que redacte las fichas con vuestra voz cuando se lo pidáis, que mantenga las etiquetas en minúsculas y sin repetir, y que devuelva siempre el id del modelo.

Ejemplos de lo que podéis pedir: «busca mis modelos de dragón con más de 100 mm», «genera la ficha de este modelo», «¿cuántos triángulos tiene?», «¿qué duplicados tengo?», «etiqueta este modelo como soporte y cable».

## Pruebas

```bat
venv\Scripts\python -m pytest -q
```

Cubren la extracción de geometría sobre mallas generadas (cubo, esfera, caja abierta, archivo de dos cuerpos; STL binario y ASCII, OBJ, 3MF), el renderizado de miniaturas, el escaneo incremental con duplicados y parecidos, la búsqueda y los filtros, fichas/etiquetas/notas/álbumes, la API, la autenticación del asistente, la vigilancia de carpetas, el guardián de peticiones y una prueba de extremo a extremo que arranca la aplicación y habla con ella a través del puente MCP.

## Límites (v1)

- Formatos: STL, 3MF y OBJ. No se escanean STEP, AMF ni PLY.
- El volumen de una malla abierta es una estimación; tomadlo como aproximado cuando «estanco» sea no.
- La detección de parecidos compara triángulos, volumen y caja envolvente: no distingue una copia remallada de otro modelo con los mismos números, por eso son «sugerencias».
- El renderizador de miniaturas mantiene todos los triángulos en memoria; un STL de 300 MB necesita unos cientos de MB de RAM durante unos segundos.

## Licencia

MIT — Luissalet.
