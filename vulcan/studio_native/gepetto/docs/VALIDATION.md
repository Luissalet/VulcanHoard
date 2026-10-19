# Validación local · 12 de septiembre de 2026

## Ampliación · 13 de septiembre: Blender y escritorio

Keaton se importó desde su `Untitled.blend`, con la malla visible que incluye la ropa y las cuatro imágenes realmente aplicadas de `upscaled_chain`: cuerpo 8192², ojos 4096×2048, pico 4096² y camiseta 5120². La importación completa por API tardó 7,14 s (4,09 s en la extracción de Blender). Son tiempos de importación, sin inferencia ni preparación de alturas. Se inspeccionó la figura completa en pantalla y se verificaron las dimensiones nativas.

La aplicación de escritorio se probó con Electron real: arranque automático del servicio, ventana propia, selector nativo conectado a la importación y recuperación del modelo con ropa. La elección del archivo en el diálogo se automatizó con una ruta conocida. Se comprobó Nuevo proyecto, recarga y apertura posterior de otro `.blend`; el proyecto vacío no recupera por accidente los modelos del anterior. También se verificó el flujo de navegador a 1440 px y 390 px, sin errores JavaScript ni desbordamiento horizontal.

Dos escenas sintéticas adicionales probaron rutas relativas mediante el selector de escritorio e imágenes empaquetadas mediante subida de archivo. Ambos archivos fuente conservaron su hash. Las cuatro copias nativas de Keaton son idénticas byte a byte a las imágenes aplicadas originales. El lanzador `.vbs` de la carpeta y la reutilización de una única ventana se ejecutaron en Windows.

Las 86 pruebas Python pasan, incluida la importación transaccional, deshacer, conservación del proyecto ante una textura ausente, conflictos de edición y prioridad del `.blend` sobre archivos antiguos sueltos. La compilación de producción pasa. Estas comprobaciones validan apertura, resolución de recursos y navegación; no aprueban la calidad semántica de los heightmaps de Keaton.

## Alcance

Se utilizó Zucker, un aldeano con `.blend` y sin STL en su carpeta al seleccionar el caso. El archivo original se abrió en Blender y se guardó una copia de trabajo bajo `data/validation/`. Todas las modificaciones y el STL de prueba se guardaron allí. No se ejecutaron escrituras sobre el archivo fuente.

Entorno observado: Windows, Python 3.13, Blender 5.0.0, instalación existente de Figure Tools y Ollama. El equipo dispone de dos RTX 5060 Ti y una RTX 4070 Ti; no se midió la distribución exacta de carga entre dispositivos ni se atribuye el tiempo a una GPU concreta. El modelo local se llama `qwen3.8:27b-q4_K_M` (familia reportada qwen35, 27B).

## Datos y tiempos

Preparación real con cinco clases de color y lado de trabajo 768, incluyendo la respuesta HTTP:

| Textura | Original | Regiones | Tiempo |
| --- | --- | ---: | ---: |
| Cuerpo | 8192×8192 | 18 | 1,804 s |
| Ojos | 4096×2048 | 8 | 0,390 s |
| Pico | 4096×4096 | 12 | 0,700 s |
| Ropa | 5120×5120 | 30 | 1,264 s |

Exportar los ocho PNG a tamaño original y su ZIP: **20,036 s**, archivo de aproximadamente 4,98 MB para estos mapas planos. Se comparte la clasificación entre mapa de altura y recolor. Los tiempos no incluyen inferencia semántica. Pruebas sintéticas previas están conservadas aparte y no se presentan como mediciones de este personaje.

Enviar la copia del proyecto desde el operador real de Blender: **5,43 s**, cuatro texturas con dimensiones originales, GLB generado y proyecto creado en el servidor. Se verificaron objetos, materiales, tamaños de imágenes, selección y objeto activo idénticos antes y después. El complemento quedó instalado y activado en las preferencias de Blender 5.0. La importación del ZIP mediante el operador completo `relief.import_maps`, incluida extracción y vinculación, pasó en **2,83 s**.

## Figure Tools real

El test invocó `figure_tools.auto_populate_displacement`, vinculó las cuatro texturas por material, adaptó copias de nodos y evaluó la malla con fuerza 0 y 0,0015, subdivisión 2.

- Vinculación: **2,653 s**, cuatro mapas, atributo UVMap.
- Geometría evaluada: **82.325 vértices**, **82.224 caras**.
- **11.693 vértices** cambiaron de posición con las regiones no neutras.
- Desplazamiento máximo observado: **0,000563707 unidades Blender**.
- Deriva máxima de un mapa neutro: **3,725 × 10⁻⁹**, inferior al umbral de prueba 10⁻⁶.
- STL diagnóstico: **8.222.484 bytes**; escena editable y render Cycles de 1000² guardados.
- Ejecución completa de ese script, con render: **12,31 s**.

Esto valida integración, transferencia numérica y generación de geometría. **No valida que cada altura sea artísticamente correcta ni que el STL esté cerrado o listo para imprimir.** La propuesta inicial se aplicó para probar la ruta de datos y no se marcó como ejemplo aprobado.

## IA: resultados y errores observados

Dos ejecuciones completas del modelo local tardaron **306,44 y 308,18 segundos** para 68 regiones. La primera inventó una referencia inexistente; quedó registrada y se añadió validación que la descarta con aviso y enumera las claves admitidas en el esquema de salida. Se conservó la respuesta original y se reprodujo el caso para comprobar la corrección sin simular una nueva inferencia.

La segunda incorporó muestras 3D vinculadas por UV y ya distinguió pico/ojos como geometría existente, con relaciones desactivadas para evitar duplicar volumen. Propuso una relación de relieve para un emblema de la ropa. También describió erróneamente al personaje como un tipo de «chocobo» y algunas asociaciones de regiones siguieron siendo dudosas. No hay una tasa de precisión validada ni se considera resuelta la comprensión semántica del personaje.

Una tercera ejecución con láminas emparejadas original/IDs de 1024×540 superó el límite HTTP de **600 segundos** y no produjo una propuesta válida. Se conservó ese fallo y la propuesta anterior. La entrada habitual volvió a atlas de IDs más las vistas originales, manteniendo las láminas emparejadas solo como evidencia de inspección. La configuración final conserva también memoria de nombres/alturas realmente revisados. No se le atribuye una precisión no medida. No se ha evaluado aún una figura de Pokémon Masters de extremo a extremo.

## Pruebas automáticas y visuales

- **9 pruebas Python** superadas: orden y ciclos del grafo, neutralidad determinista, alpha, bordes a resolución nativa, salida PNG incremental, paleta reversible, referencias inválidas, flujo API y rechazo de entradas/orígenes indebidos.
- **3 pruebas de navegador** superadas: selección 3D/UV con editor visible y cambio/deshacer persistente, ancho móvil 390 px sin desbordamiento horizontal, y petición real a IA con cuatro vistas y muestras 3D presentes.
- Compilación TypeScript y producción Vite superadas. Queda un aviso de tamaño del paquete principal, sin impedir la compilación.
- Comprobación de la aplicación compilada con el proyecto inicial y el enviado por el puente: cuatro superficies con UV/texturas vinculadas en ambos, sin errores JavaScript. Detectó y permitió corregir los sufijos `.001` que Blender añade a copias de materiales y los nombres de imagen sin extensión de GLB.
- Capturas de escritorio, selección, UV, interpretación y móvil inspeccionadas. Revisión visual de tres incidencias: selección visible, editor accesible al seleccionar y contraste de texto secundario. El detector de diseño solo tuvo análisis por expresiones regulares; no equivale a una auditoría completa de accesibilidad.

## Reproducir

Los scripts de integración usan rutas privadas de este equipo; ajusta los archivos fuente para otro entorno. Ejecuta Blender en segundo plano con la copia `.blend` y `--python scripts/test_figure_tools.py` o `--python scripts/test_blender_send.py`. El segundo requiere el servidor activo; el primero necesita el ZIP exportado descomprimido en `data/validation/maps` y Figure Tools instalado.

Evidencias locales: `data/validation/project.json`, `export-report.json`, `figure-tools-report.json`, `blender-send-report.json`, `Zucker-ReliefStudio-test.blend`, `Zucker-ReliefStudio-test.stl` y `Zucker-Blender-validation.png`. Las entradas y salidas originales de IA están en `data/<project>/analyses/`. Todo este material privado está excluido del repositorio.
