# Estado del lote — 25 de septiembre de 2026

La función está implementada. El lote completo todavía no está terminado.

Se han entregado propuestas para **Keaton, Pierce, Quinn, Sterling, Axel y Octavian**. En la carpeta de cada personaje están `<Nombre>_card_pose.blend`, `<Nombre>_card_pose.png` y `<Nombre>_card_pose.report.json`. Los informes verifican que el hash del original no ha cambiado. Son propuestas para revisar; ropa, piernas, manos y expresión se conservan.

El inventario desde Keaton contiene 263 entradas: 257 tienen escena y carta. Faltan escenas preparadas para Cece, Tulin, Viche, Mineru, Celeste y Blathers. Hay 14 escenas de más de 160 MiB que el lote inicial aparta: Blaire, Cally, Caroline, Filbert, Hazel, Mint, Nibbles, Peanut, Pecan, Ricky, Sally, Sheldon, Static y Tasha.

La cuota principal alcanzó un 90 % de uso durante el trabajo; no se ha lanzado un lote masivo con Codex. Las pruebas locales de Qwen3-VL 8B en CPU no alcanzaron calidad suficiente: una copió la calibración y otras devolvieron coordenadas fuera del contrato. Se añadieron controles de rechazo y esos resultados no se entregaron. No queda un lote automático ejecutándose.

El proceso está preparado y probado: `scripts/pose_batch.py`. Requiere proveedor explícito, no cambia automáticamente a uno remoto, conserva originales, rechaza sobrescrituras y reanuda omitiendo entregas verificadas. El siguiente paso es validar un proveedor local fiable o disponer de margen de cuota para continuar. No hay que volver a generar los seis ya entregados.
