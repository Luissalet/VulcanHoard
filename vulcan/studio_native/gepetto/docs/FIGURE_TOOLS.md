# Figure Tools: bake, nodos y comprobación del relieve

Revisión de la instalación local de Blender 5.0, 12 de septiembre de 2026. Se usa el complemento instalado; su código y sus bibliotecas `.blend` no se distribuyen dentro de Sculptor’s Hoard.

## Qué se ejecuta

El trabajador de Blender abre una copia de trabajo y llama a los operadores reales: UV Merger (0,001), Merger aplicado, AutoQuad (40°, comparar UV), Is Figure y Auto-Populate Displacement. El perfil observado con Luis establece fusiones inicial/final en 0,01, Scale en 0,2 y Subdivision en 4. Los materiales elegidos para separar quedan fuera del desplazamiento del cuerpo. En las pruebas se separa `mTops`.

Para la ropa, la selección `target_materials=['mTops']` conserva la prenda y separa el resto como contexto. El operador UV Merger instalado devuelve `CANCELLED` cuando no encuentra parejas que necesiten fusión: se observó este caso en prendas con UV continuas y se permite continuar. Merger y AutoQuad siguen ejecutándose.

La recarga reproduce el efecto del paso manual **Auto Reload → confirmar Subdivision → salir del campo**: recarga los píxeles, reasigna las imágenes a sus entradas y fuerza la actualización del grupo, la malla y el objeto. Cada versión guarda mapas propios y su `.blend`, por lo que una iteración posterior no cambia las imágenes de una versión anterior.

## Bake y UV

Se revisó `ops/baking.py`: el bake de color difuso desactiva luz directa e indirecta, permite fondo transparente y margen, trata el desplazamiento de la tesela UV y produce una imagen original y otra `_painted`. Su resolución sigue la de las imágenes de entrada cuando existen.

El análisis usa copias de hasta 1024 px, horneadas con ese operador. Así evita materializar la gran lista RGBA de un bake de 8K. El atlas se intersecta con triángulos UV reales; los colores fuera de la superficie no participan en el plan. Los originales conservan su resolución para exportar.

Para reconocer superficies, la IA recibe una hoja con el original completo, el atlas y una vista donde el material actual permanece en color y los otros aparecen grises. El control con altura uniforme se utiliza en la revisión del desplazamiento. Las clases de color incluyen muestras de su ubicación en la malla. Esto ayuda a distinguir una prenda de un detalle facial y una marca pintada de un volumen ya modelado.

Los componentes desconectados pueden tener IDs distintos aunque compartan el mismo color. La clasificación nativa conserva los bordes del original y consulta la ubicación UV cuando varias regiones comparten una clase de color. Esto permite editar las pupilas sin arrastrar las fosas nasales. No es una segmentación semántica infalible: la IA aún puede agrupar mal esos componentes.

La salida nativa usa PNG RGBA de 16 bits; el gris de referencia sigue siendo 128/255. El suavizado trabaja con valores fraccionarios antes de escribir el PNG, sin reducirlos primero a 8 bits.

## Nodos inspeccionados

`dynamic_displacement.py` crea el grupo dinámico; otros grupos se cargan desde `DynamicFigure.blend`. `blender/audit_nodes.py` extrae nodos, entradas, operaciones, dominios y conexiones del archivo realmente cargado.

| Grupo | Comportamiento relevante |
|---|---|
| Dynamic Multi Displacement | Fusión inicial, tratamiento por material, unión, otra fusión, subdivisión con `crease_edge`, desplazamiento por material, unión y fusión final. |
| ImageDisplacement | Muestrea imagen por UV, evalúa campos, combina normal y altura, usa alpha y la suma Scale + AddScale, permite subdivisión individual y selecciona geometría por material. |
| GrayScale | Contiene multiplicadores R=0,299, G=0,987 y B=0,114. Hay conversiones de campo aguas arriba; no se interpreta una paleta RGB arbitraria como alturas lineales. Para un gris Non-Color, la ganancia observada es 1,4. |
| OpenMerger | Selecciona bordes abiertos mediante número de caras vecinas y fusiona por distancia. |
| Manifolder | Separa caras por material y tiene una rama opcional para cerrar/invertir superficies. |
| Corrective Smooth | Suavizado posterior añadido por el operador dinámico, con límites fijados. Reduce también detalles demasiado pequeños. |

El perfil nuevo conserva esos nodos. **128 es un nivel de referencia positivo, no desplazamiento cero.** Los ZIP nuevos indican `profile: figure-tools-native`. El puente reconoce este perfil y mantiene los grupos originales. La compatibilidad con los ZIP antiguos conserva su adaptación explícita `R − 128/255`; ambos perfiles no se mezclan.

## Comprobación y límites

La IA ve renders reales de Blender con los mapas aplicados, el original y el control uniforme. Las vistas de inspección usan Workbench con sombreado de cavidades, como ayuda para leer detalles pequeños; la geometría sigue procediendo de Figure Tools. El original en color usa Cycles. El control conserva la transparencia del origen porque alpha también interviene en el desplazamiento.

Un modificador final de sombreado suave afecta únicamente a la lectura visual. Se comprobó en Yuka que activarlo no cambia ninguna posición de la malla evaluada. El primer plano facial y la espalda de la ropa tienen revisiones de IA independientes; el resultado conserva ambas opiniones y descarta cambios contradictorios o sin efecto.

Su opinión no aprueba el proyecto. El programa compara bordes abiertos y bordes con más de dos caras con el control y comprueba continuidad en los bordes compartidos entre materiales. Si una corrección añade defectos, la selección final prefiere una iteración anterior sin esa regresión. No hay una corrección silenciosa de la malla original.

Las restricciones de continuidad requieren coincidencia geométrica y colores compatibles a ambos lados del mismo borde. No igualan colores parecidos situados en partes independientes. Un cambio manual conserva la intención del usuario; una evaluación de sus cambios informa de los problemas sin corregirle las alturas automáticamente.

Un control uniforme también puede tener defectos. Wolfgang y Winnie mostraron geometría problemática antes de añadir los detalles de textura. Cambiar temporalmente el dominio de separación de ImageDisplacement en una copia de prueba no cambió los recuentos de Wolfgang, por lo que esa modificación no se incorporó al programa.

Los recuentos de bordes no detectan todas las autointersecciones ni certifican imprimibilidad. La ropa separada se ve en los renders como contexto, pero no recibe los mapas del cuerpo.
