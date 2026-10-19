# Posar desde una imagen (experimental)

Gepetto’s puede interpretar una carta o fotografía de referencia y guardar una propuesta de pose en una copia `.blend`. La primera versión utiliza rigs de Animal Crossing: New Horizons con los nombres de huesos originales. No es un posador universal para cualquier esqueleto.

## Uso

En **Posar desde imagen**, selecciona una escena Blender o usa la del proyecto actual. Adjunta PNG, JPG o WebP; si no adjuntas nada, se busca `card.png` junto al archivo `.blend`. Elige Codex con sesión iniciada o un modelo visual local de Ollama. Genera, compara referencia y propuesta y descarga la copia. El historial conserva los resultados y permite cancelar un trabajo.

En Figure Tools, **Pose from card.png** interpreta directamente la carta de la carpeta del personaje. Guarda un análisis específico de carta y rig en `card_pose.auto.json`; repetir el botón reutiliza ese análisis y la pose de referencia, evitando consumo y deriva acumulativa. No requiere preparar `card_pose.json` a mano. El operador manual anterior sigue disponible para ajustes expertos.

Solo se ajustan `Spine_1`, `Neck`, `Arm_1_L`, `Arm_2_L`, `Arm_1_R` y `Arm_2_R`. Las piernas, pies, manos, ropa y expresión conservan su estado original. No se crean animaciones. Una imagen no determina una pose 3D única: la salida es una propuesta, no una reproducción garantizada ni una comprobación de colisiones o imprimibilidad.

## Interpretación y ajuste

El modelo recibe la referencia y una vista del rig real. Devuelve observaciones estructuradas de articulaciones; un ajuste numérico calcula cámara y rotaciones. Se validan coordenadas, confianza, cobertura de articulaciones y error de ajuste antes de aplicar. Para aves se pueden observar planos de las alas y evitar que una superficie ancha quede de canto.

La cámara usa elevación real; las restricciones de giro siguen el eje anatómico del rig. La pose base persiste para que repetir el proceso no acumule rotaciones. El modo de ajustar solo cámara utiliza la pose real sin resolver articulaciones imaginarias.

Codex utiliza `codex exec`, la sesión existente, un esquema JSON y un entorno temporal sin herramientas de escritura. Envía las dos imágenes al proveedor elegido. Ollama solo se usa al seleccionarlo expresamente y libera el modelo al acabar. `FIGURE_TOOLS_POSE_CPU=1` fuerza ejecución local por CPU. El ajuste geométrico y Blender se ejecutan localmente.

## Archivos e integración

El motor compartido está en `pose_engine/`. `scripts/sync_pose_addon.py <carpeta-addon>` actualiza únicamente sus cuatro archivos dentro de una instalación existente de Figure Tools; el registro del operador y el botón pertenecen al addon.

La API ofrece `/api/pose-capabilities`, `/api/pose-runs`, consulta individual, cancelación, descarga de referencia/vista/copia/informe y exportación a una ruta nueva. Las propuestas se guardan en `data/pose-runs/<id>`. El original se abre como entrada, nunca se guarda sobre él. La exportación rechaza sobrescrituras. Las texturas externas conservan rutas absolutas: la copia necesita esos recursos en el equipo; no es un paquete autocontenido.

## Verificación realizada

- 19 pruebas del ajuste: cámara, eje anatómico, FK, planos de alas y entradas inválidas.
- Blender real con Keaton: repetir aplicación sin deriva, huesos ajenos intactos y proyección coherente con Blender; rigs de Axel y Octavian comprobados.
- Botón automático real con Axel y Octavian: una interpretación por carta, segunda aplicación desde caché, sin modificar archivos originales.
- API de Gepetto’s → Blender → Codex → copia y vista previa completada con Axel; hash SHA-256 del original idéntico antes y después.
- Pruebas HTTP de validación, cancelación, recuperación y exportación sin sobrescritura; compilación de interfaz y revisión en escritorio/móvil.

La calidad de un modelo local debe comprobarse con ejemplos antes de ejecutar un lote. El error de articulaciones no mide por sí solo la fidelidad de silueta ni la plausibilidad de partes ocultas. Se rechazan análisis que copian las coordenadas de calibración. El proveedor local recibe únicamente la imagen objetivo para evitar confundirla con la calibración.

## Lotes

`scripts/pose_batch.py` procesa un inventario con entradas `name`, `source` y `row` en `eligible`, en orden, con proveedor explícito y tres intentos por defecto. `--limit` permite ampliar el lote y `--cpu` reserva las gráficas. No cambia de proveedor automáticamente. Guarda `<Nombre>_card_pose.blend`, una vista PNG y un informe en la carpeta del personaje; rechaza sobrescrituras y verifica el hash del original. Un informe central registra fallos y omisiones. Los resultados verificados se omiten al reanudar. Los archivos de más de 160 MiB requieren revisión separada en esta primera versión.
