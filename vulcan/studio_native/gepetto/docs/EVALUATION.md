# Evaluación del flujo con Figure Tools

12 de septiembre de 2026. Pruebas locales con Blender 5.0, el complemento instalado y el modelo de visión local de 27B. Las escenas y texturas privadas se conservan en `data/`, fuera de Git. Ningún resultado se considera una certificación de impresión.

## Qué está probado

- Bake de las UV, interpretación de materiales con referencias visuales, exportación nativa 4K/8K y cálculo real de Figure Tools.
- Revisión visual por IA del relieve frontal y a tres cuartos frente a un control uniforme. Un segundo pase revisa el primer plano facial o la espalda de la ropa; las discrepancias de altura entre vistas se conservan como incidencias y no se ejecutan como cambios contradictorios.
- Recarga de imágenes y reevaluación de Subdivision, conservando los nodos originales del complemento.
- Órdenes reales al modelo local para hundir ojos, elevar pecas, igualar alturas, unir grupos y separarlos. Igualar a otro grupo conserva la altura del grupo de referencia.
- Exportación del ZIP e importación en Blender con cuatro materiales, Scale 0,2 y Subdivision 4. Las imágenes mantienen su resolución.
- Extracción de una textura empaquetada en un `.blend` de prueba sin modificar el archivo original.
- 67 pruebas automatizadas de API, exportación, planificación, comandos, continuidad, transparencia del control, regiones espaciales, precisión de 16 bits y selección de iteraciones. Compilación de producción y comprobación de primeros planos en escritorio y móvil completadas.

## Personajes y resultados

Las cifras comparan **el mismo pipeline preparado** con altura uniforme y con los mapas del candidato. Un recuento idéntico no descarta autointersecciones, pliegues ni problemas ya presentes en el control.

| Personaje | Resultado de la revisión supervisada | Bordes abiertos, control → candidato | Bordes con más de dos caras, control → candidato |
|---|---|---:|---:|
| Zucker | Ojos hundidos, seis pecas elevadas, cobertura ondulada y detalles de tentáculos. Candidato disponible en la app. | 536 → 536 | 416 → 416 |
| Yuka | Pupilas y pestañas recuperadas tras una revisión del usuario. Blanco 64, pupila 104, piel 128, párpado 160, pestaña 192; mechón 144. Candidato disponible en la app. | 803 → 803 | 0 → 0 |
| Wade | No aprobado: aparecen defectos junto a ojos y pico. | 1140 → 1161 | 1680 → 1680 |
| Winnie | No aprobado: la forma del hocico requiere revisar también la geometría de origen. | 0 → 0 | 3104 → 3104 |
| Wolfgang | No aprobado: se conservaron las pruebas de geometría y los intentos fallidos. | Véanse los informes de escena | Véanse los informes de escena |
| Willow | Capas del ojo recuperadas. Pendiente de separar una línea de la frente que comparte clase con las cejas. | 1986 → 1986 en el candidato de capas | 2 → 2 |

Se separó `mTops` al probar los cuerpos. La ropa se procesa después como pieza independiente, con sus propios mapas y vistas. Yuka y Zucker cuentan ahora con evaluación conjunta, preservando la pose y los modificadores originales de ambas piezas. El selector permite comparar la versión editable y una unión final con la prenda cerrada.

| Ropa (`mTops`) | Inspección supervisada | Bordes abiertos, control → candidato | Bordes con más de dos caras |
|---|---|---:|---:|
| Zucker | Motivo rojo posterior, emblemas frontales, panel interior y franja de cintura visibles. | 636 → 628 | 0 → 0 |
| Yuka | La textura original es marrón uniforme. Conservar altura 128 es correcto; el cuello y las mangas ya tienen geometría. | 774 → 774 | 0 → 0 |
| Wade | El primer plan dejaba casi invisible el estampado. El ajuste supervisado recupera su contorno, cara frontal y silueta posterior; los detalles siguen suaves. | 896 → 896 | 0 → 0 |

## Correcciones que explican el resultado

**Zucker:** la cobertura a 176 introducía 10 bordes abiertos adicionales en los laterales de la cabeza. Bajándola a 160, con piel a 128, se conserva su lectura y desaparece esa regresión. Ojos a 80, pecas a 176 y detalles de tentáculos a 96. Esta elección requirió inspección supervisada, no fue un éxito autónomo del primer plan.

**Yuka:** el mismo color de la superficie facial tenía distintas alturas a ambos lados de un borde entre materiales. La restricción usa el borde real y sus UV para mantener continuidad. No iguala de forma global partes independientes que comparten color.

La primera valoración de Yuka fue demasiado favorable: el usuario señaló que no veía pupilas ni pestañas. Las pestañas estaban al nivel de la piel y una clase negra mezclaba pupilas con fosas nasales. Se separaron sus componentes espaciales, se verificó una orden real que modificó solo las dos pupilas y se ajustó la jerarquía según la preferencia explícita del artista. Las fosas nasales conservaron su altura. Las pruebas anteriores, incluida una que abrió 90 bordes al modificar también la nariz, siguen conservadas.

**Inspección:** la apariencia cuadriculada de los primeros planos se debía en parte al sombreado plano original. Un último nodo de sombreado suave mejora la lectura; se verificó que no altera ninguna de las 436.865 posiciones evaluadas de Yuka. Los mapas nativos ahora conservan 16 bits para evitar perder precisión en las transiciones. El ZIP de Yuka se volvió a importar con éxito en los cuatro materiales reales de Figure Tools.

**Revisor visual:** con siete imágenes juntas llegó a afirmar que faltaba el motivo trasero de Wade, pese a ser visible. La comparación aislada de la espalda sí lo reconoció. Ahora se revisan las vistas por separado; esto reduce ese fallo observado, pero no demuestra que el revisor sea fiable en todos los casos. Las sugerencias que conservan exactamente la misma altura ya no provocan otra iteración inútil.

**Willow:** el modelo identificó iris y esclerótica pero los dejó a la altura de la piel. Después aprobó el render alegando ver unas capas que no existían. Una revisión dirigida recuperó esas capas. También se detectaron tonos de transición confundidos con superficies semánticas; las máscaras siguen necesitando revisión cuando una clase de color mezcla funciones.

## Tiempo y reproducibilidad

Los cuatro mapas nativos de Zucker tardaron 20,71 s en una exportación sin caché. Una edición aislada de la boca de Yuka tardó 1,32 s y reutilizó el cuerpo 8K en 0,01 s. Tres vistas de inspección de Zucker tardaron 8,15 s. El ensayo inicial completo de Willow tardó 258,06 s, incluida la interpretación local; no equivale a un tiempo de edición con caché.

Cada iteración guarda su plan, alturas, PNG, escena `.blend`, renders, tiempos y respuesta del modelo. La selección de candidato prioriza evitar nuevas regresiones geométricas antes de considerar la opinión visual de la IA. Las correcciones manuales se evalúan sin modificarlas automáticamente.

Los ensayos guiados y ajustes supervisados no se deben presentar como una tasa de éxito de generación autónoma. Tampoco se han validado todavía personajes de Pokémon Masters en este flujo nuevo.

## Valor como proyecto de ingeniería de IA

El trabajo demostrable es integrar percepción multimodal, operaciones semánticas validadas, procesamiento de imágenes grandes, restricciones geométricas y una evaluación con evidencias reproducibles. No se ha entrenado un modelo propio. El siguiente problema de calidad es localizar superficies semánticas que comparten color y mejorar un revisor visual que todavía da falsos positivos y negativos.

## Unión de cuerpo y ropa y controles operativos

La primera unión de ropa perdió su transformada de rig y apareció en el suelo; se corrigieron las relaciones de parentesco y la matriz de mundo al importar la pieza. Otro ensayo voxel eliminó una prenda sin espesor pese a dar cero bordes abiertos: demuestra por qué esa cifra sola no valida el resultado. La versión actual cierra cuello, mangas y bajo dentro del cuerpo antes de unir los volúmenes. La inspección frontal, posterior y a tres cuartos conserva ropa y detalles en Yuka y Zucker.

En `data/complete-aligned`, los resultados finales de ambos personajes tienen cero bordes abiertos y cero bordes con más de dos caras. El cierre no demuestra ausencia de todas las autointersecciones ni sustituye una revisión de impresión. Las líneas del borde de las orejas y otros pliegues ya presentes en el control no se atribuyen automáticamente a UV.

- Ciclo real del modelo local Q4: liberar memoria 1,86 s, cargar 8,94 s; ambos estados comprobados mediante `/api/ps`.
- Cola real, ropa uniforme de Yuka 5120²: 98,39 s hasta proyecto guardado. No es una medida de una figura completa.
- Primer lote completo: fallo recuperable al exceder el contexto (21.605 tokens frente a 16.384). El inventario compacto conserva todos los IDs y redujo el caso comprobado a 11.266 tokens.
- Reintento del lote completo: 382,18 s de cálculo automático. Terminó, pero la interpretación de los ojos aún requirió corrección; no se presenta como éxito autónomo.
- Guardado, versiones, undo/redo con máscaras, detección de ediciones obsoletas y contraste comprobados con pruebas de API y persistencia. El historial usa SQLite WAL y archivos locales; no es una copia de seguridad fuera del equipo.
- Cola persistente, deduplicación y reutilización de fases tras fallo comprobadas. Se descargó Qwen3-VL 30B A3B Q8 desde el repositorio oficial de Qwen en Hugging Face mediante Ollama, con verificación de certificado e integridad. La descarga del registro de Ollama falló por certificado; no se desactivó TLS. Se comprobaron el proyector de visión y la carga/descarga reales, y se rechazó esta variante como opción preferida por su peor interpretación en los ensayos.

No se ha medido todavía una sesión humana completa de menos de cinco minutos para cualquier personaje. El objetivo de reducir intervención se evalúa separadamente de los minutos de inferencia automática.


## Ensayos de la primera propuesta

Se ha añadido una observación visual previa a la asignación de regiones, roles semánticos con una paleta inicial para los ojos y las marcas faciales, y una comprobación más explícita de los detalles visibles en cada revisión. La continuidad distingue una superficie identificada de un grupo genérico: puede extender un mechón o una fosa nasal a la textura adyacente sin cambiar toda la piel. En el ensayo de Yuka redujo una regresión de 306 bordes abiertos a 12; un contraste automático de 0,85 la redujo a 6. Estos ensayos siguen sin equivaler a un resultado autónomo aprobado.

Se compararon Qwen3.8 27B Q4 y Q8. El modo de razonamiento de Q4 tardó 283,74 s en un plan de cuerpo y aún omitió detalles; aumentar precisión o razonamiento no garantiza una interpretación correcta. Q8 se cargó y descargó usando los controles reales de memoria. La plantilla de conversación usa un renderer nativo: una prueba controlada confirmó que el modelo sí recibe instrucciones de sistema. Otra prueba identificó correctamente dos imágenes distintas; no se ha demostrado un fallo general de transporte multimodal.

La proyección de IDs sobre el modelo usa UV, triángulos y un búfer de profundidad; tarda aproximadamente un segundo en el caso de Zucker. Incluir todas las piezas del contexto evita etiquetar regiones tapadas por la ropa. Las etiquetas del atlas se sitúan ahora dentro de la región, incluso si su centro cae en un hueco. Estas mejoras de evidencia no bastaron por sí solas para aprobar la primera propuesta de los modelos probados.


## Reconocimiento separado del estilo y padding UV

La generación actual recibe una hoja con el original, el material seleccionado y el atlas. Primero asigna roles físicos sin alturas; después aplica el perfil del artista. Los valores válidos del esquema también aparecen en el texto de la petición: restringir JSON por sí solo no garantiza que el modelo conozca los nombres exactos de las categorías. Se conserva un método de inspección de detalles que quedaron agrupados con superficies más amplias.

En una prueba de Yuka, el reconocimiento separado identificó correctamente esclerótica, pupilas, párpados y pestañas, pero agrupó las fosas nasales con el hocico. Una inspección automática de esos dos componentes recuperó su función. Otros ensayos del auditor interpretaron mal sombras de pelo, por lo que no se considera infalible ni se presenta una prueba puntual como fiabilidad general.

La exportación extiende la altura real fuera de la cobertura UV y utiliza el margen completo del filtro. Dos regresiones sintéticas verifican que el fondo no altera el borde elevado y que las bandas producen el mismo resultado que el filtrado de una imagen completa. En Yuka: cuerpo 8192², 20,28 s; ojos 4096×2048, 2,13 s; boca 4096×2048, 1,94 s; ropa 5120², 6,67 s. Total de exportación sin caché: 31,02 s. La ejecución con renders y unión final tardó 77,81 s. Esa prueba reutiliza decisiones semánticas supervisadas; no mide la generación inicial de IA.

La unión de esa prueba conserva 1.325.322 vértices, cero bordes abiertos y cero bordes no manifold. Se inspeccionaron frente y tres cuartos; la línea física del bajo y los pliegues originales de las orejas siguen visibles.

El primer ensayo completo Q8 anterior tardó 1.209,39 s en Zucker mientras competía con otras pruebas de inferencia. No es un benchmark aislado ni un resultado autónomo aprobado. La versión de producción posterior difiere: aplaza las revisiones intermedias de cuerpo/ropa y revisa la unión acabada con su control equivalente.

## Ensayos completos desde la cola

Una pasada Q8 sin otras inferencias de prueba tardó 952,90 s en Zucker y 616,38 s en Yuka. Zucker seguía confundiendo parte de las pecas con ojos y aplanaba las ventosas. En Yuka se recuperaron pupilas, párpados, pestañas y fosas nasales, pero el mechón superior seguía plano. Ambos resultados tenían cero bordes abiertos y cero bordes no manifold; esa cifra no ocultó sus defectos semánticos en la evaluación.

La observación del original completo, seguida de los IDs proyectados sobre su geometría coloreada, recuperó las pecas y ventosas de Zucker. El ensayo completo de esa versión tardó 832,16 s, pero produjo surcos incorrectos en el hocico. El revisor lo aprobó erróneamente, otro falso positivo conservado en las evidencias privadas. Se limitó después la inspección de recortes a detalles pequeños y se añadió una comparación específica de piezas ya modeladas. La primera formulación de esa comparación interpretaba mal el objetivo de conservar dibujos planos; explicitar la figura impresa sin color distinguió el hocico ya modelado de los ojos pintados ausentes en el control.

Las pruebas de interfaz verifican además Ctrl+S desde un campo de superficie aún enfocado: la versión espera al guardado pendiente. El contraste utiliza la revisión confirmada, y undo/redo sigue disponible tras recargar. La caché de exportación distingue IDs espaciales aunque compartan color y se reordenen sus alturas.


## Contraste de estampados y comprobación de la propuesta actual

La siguiente generación completa de Yuka tardó 478,83 s, sin asignaciones manuales. Conservó pupilas, párpados, pestañas, fosas nasales, mechón y ropa en las vistas frontal, posterior y a tres cuartos. La unión tenía 1.332.330 vértices, cero bordes abiertos y cero bordes no manifold. Persistían errores de nombres: zonas de las plantas se etiquetaron como cejas. Eso también extendía incorrectamente el encuadre facial hasta los pies; el selector de detalle ahora ancla el encuadre a esclerótica, iris o pupila y descarta etiquetas oculares alejadas.

En Zucker, limitar la auditoría de recortes evitó los surcos del hocico, pero el modelo dejó varios colores de los estampados a la misma altura. El pase de contraste de decoración separa el fondo del tejido y los trazos internos, sin aplicar esa regla a piel u ojos. La inspección del render real recuperó los cuadros de la cintura, los emblemas frontales y los trazos del motivo trasero, manteniendo ojos, seis pecas y ventosas. Los pliegues de hocico y entre pies también existen en el control uniforme.

`scripts/replay_surface_postprocessing.py` permite medir este cambio reutilizando las respuestas iniciales del modelo: vuelve a ejecutar comprobaciones focales, estilo, continuidad, exportación, revisión visual y Figure Tools. No utiliza alturas corregidas por el artista ni asignaciones manuales. Es una reproducción controlada de respuestas previas, no una medición de generación inicial independiente. Los tiempos de esa reproducción no deben compararse con un arranque completo.


Las reproducciones finales conservaron 1.413.822 vértices en Zucker y 1.332.286 en Yuka, ambas con cero bordes abiertos y cero bordes no manifold. La revisión de cuerpo y ropa utiliza ahora una tercera comparación de la espalda, encuadrada a partir de la geometría real de la prenda y aplicada por igual al original, control y candidato. En la vista completa el revisor llegó a decir que faltaban cuadros que sí estaban presentes: ese fallo se conserva y no se convierte en una corrección arbitraria de alturas. También pidió hundir más las pupilas de Yuka; el guardián rechazó una propuesta que invertía el orden ocular del artista. Los primeros planos, el control y las incidencias permanecen accesibles para decidir, sin ocultar los desacuerdos del modelo.


El encuadre posterior permitió al modelo reconocer los cuadros, pero siguió señalando bordes suaves e irregulares en el emblema y el bajo. No se eliminaron esas incidencias para obtener una aprobación: ambos proyectos conservan su estado de revisión pendiente. El resultado actual conserva los detalles reconocibles, pero todavía no alcanza fiabilidad autónoma total. El taller comprobó además la apertura de la malla de Yuka generada desde los triángulos de origen: cuatro materiales con UV vinculadas, pose correcta y texturas externas; los lotes ya no requieren importar un GLB adicional.


## Comprensión visual y vistas activas

El contrato visual de Yuka, con vista frontal y posterior del original y del control, tardó 182,72 s con razonamiento activado (2.246 tokens de salida totales). Distinguió la nariz modelada de ojos y mechón pintados, pero no eliminó todas las ambigüedades. Asignar también las 64 regiones UV con razonamiento extendido tardó 435,02 s (5.623 tokens; 10.589 tokens de entrada) y siguió llamando párpados a manchas situadas en la planta de los pies. Se conserva como fallo: más razonamiento no sustituyó la evidencia que faltaba.

La asignación compacta posterior, guiada por el contrato y sin razonamiento extendido durante el reparto de IDs, tardó 68,70 s. Dejó esas manchas al nivel de piel, aunque todavía con un nombre demasiado amplio. Un selector geométrico de seis cámaras encontró 0 píxeles visibles de las manchas en las vistas frontal, posterior, laterales y superior, y 132 desde abajo. Se añadió una inspección del original desde ese ángulo, con los IDs reales y su control uniforme; su resultado debe evaluarse por separado del éxito al localizar la cámara.


La orientación frontal con marcadores vinculados a posiciones 3D reales y una vista inferior oblicua identificó los pies de Yuka en 55,48 s, mientras que una vista inferior aislada había confundido esas manchas con pupilas y agotado su respuesta. El selector actual compara siete direcciones y puede ampliar la superficie seleccionada con el mismo encuadre aplicado al control.

Una primera generación completa desde geometría preparada tardó 461,30 s y perdió la boca y las fosas nasales. Presentar primero el atlas seleccionado y después su primer plano sobre el modelo recuperó la boca en una prueba de 45,18 s. La segunda pasada completa añadió comprobaciones focales contextualizadas: 579,56 s, además de los 182,72 s del contrato visual reutilizado. No utiliza alturas previas ni asignaciones manuales, pero reutiliza bakes, máscaras espaciales y geometría preparada: no es un arranque desde cero. La comparación con la referencia privada pasó de 15/20 a 18/20 direcciones y de 25/38 a 37/38 órdenes. Los dos desacuerdos de dirección restantes son una referencia 132 frente a 128 en superficies ya modeladas; no se alteraron para mejorar la puntuación.

La inspección frontal y a tres cuartos conserva pupilas, párpados, pestañas, mechón, fosas nasales, sonrisa y prenda uniforme. La malla final tiene 1.325.894 vértices, cero bordes abiertos y cero no manifold. La lectura de pies es más precisa que en la propuesta anterior. El revisor sigue dando falsos negativos sobre pupilas que deben estar sobre la esclerótica pero debajo de la piel; la preferencia se ha explicitado también en esos términos. No se cambió su estado a aprobado para ocultar el desacuerdo.

El contrato inicial de Zucker identificó seis pecas y los estampados, pero propuso dejarlos planos para pintar: contradice el objetivo del usuario. La política de impresión se separó de la percepción: la IA informa de apariencia y geometría, y el programa aplica el requisito de conservar los detalles sin color. Las respuestas originales quedan guardadas para auditar esa contradicción. Su primera pasada falló además al agotar una comprobación focal; ahora existe un reintento compacto acotado y la conservación explícita del estado anterior si tampoco concluye.


La segunda pasada preparada de Zucker terminó en 1.055,79 s, con el contrato previo de 195,24 s reutilizado. Conserva seis lunares, ojos, ventosas, cobertura superior, motivos frontales, emblema posterior y patrón del bajo en las vistas inspeccionadas. La unión tiene 1.409.642 vértices y cero bordes abiertos/no manifold. La coincidencia con la referencia es 17/21 direcciones y 24/36 órdenes, frente a 18/21 y 28/36 de la propuesta anterior conservada: no se presenta como una mejora uniforme de puntuación. Los desacuerdos incluyen estilo de ribetes y una cavidad nasal ya modelada. El revisor sigue dando incidencias discutibles sobre facetas y estampados, y conserva el estado pendiente.

Ablación con las mismas imágenes y petición guardada, cambiando solo el presupuesto de decisión local: pies de Yuka 10,90 s, tentáculos de Zucker 10,64 s y emblema posterior 7,35 s sin pensamiento extendido. Mantuvieron una identificación útil y las mismas alturas; la petición que juntaba brazos y piernas todavía produjo un nombre demasiado amplio, motivo para separar evidencia por posición. Las inspecciones activas usan ahora esa decisión compacta por defecto, mientras el contrato global conserva razonamiento extendido. La auditoría de color no se simplificó globalmente: las fosas nasales coincidieron en 6,90 s, pero en 7,86 s otra respuesta cambió una ventosa de centro a borde y alteró la altura. Esas mediciones parciales no son un nuevo tiempo completo de preparación.


La comprobación de piezas compactas, que el flujo normal ejecuta antes del reparto semántico, se reprodujo también sobre ese candidato preparado. La nariz muestra el 96,2 % de su atlas en el encuadre frontal: el modelo confirmó en 23,08 s que el hueco y los anillos ya existen, sin marcas pintadas que desaparezcan. Se restituyó su referencia uniforme 128 mediante esa decisión automática, sin asignaciones manuales. La reproducción completa con exportación y nueva revisión tardó 241,69 s y reutiliza las otras decisiones recién generadas; no es otra primera propuesta desde cero. La unión final conserva 1.409.498 vértices y cero bordes abiertos/no manifold; coincide con 18/21 direcciones y 24/36 órdenes de la referencia. Los proyectos principales guardan estas evidencias y siguen pendientes de revisión.
