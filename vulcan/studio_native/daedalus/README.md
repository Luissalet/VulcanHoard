# Daedalus's Hoard

Generación local de modelos 3D desde imágenes, con refinado, reparación para impresión y optimización para juegos opcionales.

Importa PNG, JPG o WebP, genera a 512 o 1024 y conserva el modelo completo. El visor permite inspeccionar materiales o geometría, comparar un refinado con su original con cámaras enlazadas y descargar GLB, STL o PLY indicando el tamaño del lado mayor. La biblioteca conserva trabajos, errores y versiones entre sesiones. Cancelar un trabajo no borra sus originales.

El refinado crea otra geometría y puede perder huecos o detalles. Permite elegir UltraShape o DetailGen3D. UltraShape admite 2.048, 8.192 o 32.768 latentes; DetailGen mantiene 2.048 y 8.192. Los pasos y el giro del frente se pueden ajustar: se alinea una copia para la referencia y la salida vuelve a la orientación original. Los trabajos anteriores mantienen sus parámetros. En el ejemplo mecánico, DetailGen sigue perdiendo huecos incluso con el frente corregido. UltraShape a 8.192 conserva mejor la estructura y limpia algunas superficies, pero también cambia detalles y profundidad; no se ha acreditado una mejora global sobre el original. Las pasadas se conservan para elegir por comparación. Los materiales del refinado se transfieren de la generación si se selecciona «Conservar aspecto»; no son una nueva síntesis de texturas.

«Preparar una versión» añade dos pasadas independientes sobre cualquier resultado terminado. **Impresión** permite reconstruir el volumen a128/256/384 celdas o reparar la superficie con limpieza y relleno de bordes; informa del cierre, orientación, piezas y aristas abiertas. La reconstrucción puede suavizar detalles o unir piezas próximas. Por defecto no elimina las piezas menores. Puede limitar el detalle de trabajo o conservarlo completo. **Juegos** reduce los triángulos al presupuesto solicitado, crea UV y hornea color, transparencia, metal y rugosidad en texturas de 512 a 4096. Si no alcanza el presupuesto, lo indica. Ambas trabajan en CPU, conservan originales y versiones, permiten cancelar y añaden un informe descargable. No añaden rigging ni retopología de animación. El cierre no verifica grosor mínimo, soportes ni parámetros del laminador. Véase `docs/POSTPROCESSING.md`.

## Reconstrucción multivista

La ruta conjunta local recibe varias vistas ordenadas del mismo objeto y produce geometría 512. El formulario, la API y MCP conservan cada referencia y reservan una GPU. Usa el alfa de recorte de la imagen. En imágenes opacas o casi opacas, puede recortar un fondo uniforme conectado al borde. La salida conserva la geometría y la procedencia de las vistas. Preparación, alcance y licencias en [docs/multiview.md](docs/multiview.md).

## Ejecución

En este equipo, abre `launch.cmd` o Daedalus en Hoard Hub. Puerto predeterminado: http://127.0.0.1:8830. El entorno propio está en `D:/LocalAI/HoardStorage/daedalus-runtime`; los pesos, en `D:/LocalAI/HoardStorage/daedalus-models`. La carpeta puede cambiarse en «4 gráficas». El programa no descarga pesos al generar.

TRELLIS distribuye cada etapa entre dos y cuatro GPU CUDA detectadas. Antes de cargar los modelos, reserva memoria de cada tarjeta en el Hub y usa sus UUID para mantener el mismo reparto en el motor. La reconstrucción multivista y los borradores usan una tarjeta. Los refinadores mantienen su reparto de cuatro tarjetas. Si una reserva está ocupada, el trabajo espera y se puede cancelar.

Para preparar otro entorno, instala Python 3.13, PyTorch CUDA 12.8 y las dependencias de `requirements.txt` (el índice oficial de PyTorch debe proporcionar los paquetes `+cu128`). `hoard-link` es la biblioteca compartida de la familia. Coloca los archivos con la estructura descrita en `docs/MODELS.md`. `tools/prepare_models.py` es una ayuda de instalación desde los archivos locales de investigación de este equipo; no forma parte de la ejecución del motor.

La interfaz utiliza el tema oscuro común de los Hoards, Cinzel y Source Sans 3 autoalojadas y el icono de dragón turquesa con cubo y calibre dorados. La biblioteca, las versiones, el visor y las exportaciones siguen disponibles también en móvil.

## Archivos y unidades

Cada trabajo guarda parámetros, progreso, geometría original, materiales, vista simplificada y resultado en una carpeta independiente. La base SQLite está en `data/daedalus.db`. `DAEDALUS_DATA_DIR` permite instancias de pruebas separadas; no compartas la misma carpeta con dos servidores. Los trabajos activos se marcan interrumpidos al recuperar una sesión; los que aún estaban en cola vuelven a la cola.

El tamaño no se deduce de una imagen. El lado mayor indicado al exportar se escribe en metros para GLB y en milímetros para STL/PLY. GLB permite elegir la vista con materiales o la geometría completa. La copia completa conserva geometría y color de vértices; el aspecto PBR detallado está en la vista con materiales. STL no contiene color. PLY conserva el color de vértices. Escalar una exportación no altera los archivos originales.

## API y MCP

`python -m daedalus` inicia la aplicación. `python -m daedalus.mcp` expone 14 herramientas por stdio contra esa misma API: importar imagen, generar, ampliar a 1024, refinar, reparar, optimizar para juegos, listar/leer/cancelar trabajos, consultar versiones, renombrar, exportar, consultar motores y configurar su carpeta. Las pasadas devuelven un ID de trabajo asíncrono; `job_get` lee el estado real. Configuración MCP en `faustus-plugin.json`; `DAEDALUS_URL` selecciona la instancia y `DAEDALUS_TOKEN_FILE` su token local. Los agentes y la interfaz utilizan la misma cola y los mismos datos.

Los pesos de Ultra figuran como Apache-2.0, pero declaran una base Hunyuan3D-2.1 y el repositorio de referencia tiene restricciones geográficas. Esa discrepancia está conservada en `docs/ULTRA-PROVENANCE.md`; no se afirma permiso de redistribución sin restricciones.

Código GPL-3.0; los pesos y componentes tienen sus propias licencias. Véase `THIRD_PARTY.md` antes de redistribuirlos.

La ampliación opcional a 1024 reutiliza el latente de una generación original de 512 conservada y ejecuta la segunda etapa entrenada de TRELLIS.2, seguida de materiales nuevos. No vuelve a sortear la estructura de 512 ni sustituye el original. API `POST /api/jobs/{id}/upscale`, MCP `model_upscale`.

El visor conserva una vista ligera por defecto. «Vista completa» carga la geometría íntegra, con material uniforme para comparar detalle sin la simplificación de la vista previa; «Ver color» muestra su color de vértices. Los trabajos devuelven enlaces directos `viewer_url`, `comparison_url` y `full_comparison_url`. La primera carga completa puede tardar y utiliza más memoria gráfica.
