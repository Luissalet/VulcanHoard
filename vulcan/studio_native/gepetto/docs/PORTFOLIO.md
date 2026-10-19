# Sculptor’s Hoard · proyecto de ingeniería de IA

## Problema

Preparar figuras exigía recolorear texturas a mano, trasladarlas a Figure Tools y repetir la inspección del relieve. La dificultad no era convertir color a gris: había que distinguir piel, pupila, párpado, pestaña y prendas superpuestas, conservando los detalles de texturas 4K/8K.

## Solución

Un taller local que relaciona modelo 3D, UV y regiones de textura. La visión reconoce superficies, un perfil de estilo asigna alturas y Blender evalúa el desplazamiento real. La aplicación conserva las decisiones y sus evidencias, permite editarlas por texto y mantiene proyectos, versiones y lotes recuperables.

## Qué demuestra

- Percepción multimodal conectada a acciones verificables sobre un entorno 3D.
- Separación entre interpretación probabilística y restricciones deterministas.
- Tratamiento de regiones del mismo color con funciones distintas.
- Exportación de imágenes grandes con memoria acotada, precisión de 16 bits y caché.
- Evaluación de errores reales: capas oculares omitidas, costuras UV, analogías anatómicas equivocadas y falsos positivos del revisor.
- Producto completo: interfaz 3D, edición, persistencia, recuperación y observabilidad de GPU/modelos.

## Texto breve para CV

Desarrollé Sculptor’s Hoard, un taller local de IA multimodal para generar y editar mapas de relieve de figuras 3D. Integré visión sobre modelo y UV, restricciones de alturas, exportación RGBA de 16 bits a 4K/8K y un ciclo de evaluación real en Blender/Figure Tools. Implementé proyectos con recuperación transaccional, undo/redo y procesamiento por lotes; medí 20,28 s para exportar un mapa 8192² con padding UV en el equipo de desarrollo.

## Demo

Mostrar el original y su atlas, una región con función física reconocida, el mapa y el desplazamiento real. A continuación, editar una altura por texto, comparar la versión anterior y recuperar un punto guardado. La pantalla de producción muestra el modelo cargado, las GPU y el progreso real de la cola.

Separar los resultados supervisados de las primeras propuestas automáticas. No presentar el tiempo de exportación como tiempo de generación completa, ni la aprobación del modelo como garantía de impresión. No se ha entrenado un modelo propio. Utilizar material propio o autorizado para las demos públicas; los assets privados de los ensayos no se redistribuyen.
