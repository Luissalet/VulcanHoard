---
name: "Vulcan's Hoard — comparación de revisiones"
description: "Sistema local del comparador dentro de la ficha de un modelo."
colors:
  accent: "#e66d40"
  accent-hover: "#eb8964"
  accent-ink: "#1b1513"
  paper: "#1c1614"
  sunken: "#161210"
  surface: "#241e1b"
  elevated: "#2f2825"
  line: "#433935"
  line-hover: "#6a5d58"
  ink: "#ece4e1"
  muted: "#beb7b4"
  text-dim: "#a69e9b"
  warn-ink: "#e0bb62"
  danger-ink: "#ee8b80"
  viewer-ground: "#fbf9f6"
  reference-wire: "#38607b"
  candidate-solid: "#b56f39"
  deviation-point: "#ac2f28"
typography:
  title:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.65
    letterSpacing: "-0.015em"
  body:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "13px"
    lineHeight: 1.65
  label:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.65
  help:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "12px"
    lineHeight: 1.65
  button:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: "18px"
rounded:
  control: "8px"
  viewer: "8px"
spacing:
  control-gap: "8px"
  field-gap: "12px"
  section-gap: "16px"
  result-gap: "24px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  button-secondary-hover:
    backgroundColor: "{colors.elevated}"
  button-view-selected:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  field:
    backgroundColor: "{colors.sunken}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "8px 11px"
    width: "100%"
  viewer:
    backgroundColor: "{colors.viewer-ground}"
    rounded: "{rounded.viewer}"
    width: "100%"
---

# Design System: Vulcan's Hoard — comparación de revisiones

## Overview

**Creative North Star: "La fragua"**

Se conserva el nombre conceptual ya presente en el sistema de Vulcan. La autoridad visual actual para esta extensión es la implementación oscura de `client/src/index.css` y la paleta Vulcan de `client/src/hoard-theme.css`: fondos marrón carbón, texto cálido y acción cobre. Este documento tiene alcance local; el `DESIGN.md` raíz describe una versión clara anterior y permanece intacto. No existe `PRODUCT.md` en este proyecto.

El comparador pertenece a la ficha del modelo. Se abre en línea, conserva el visor y los editores existentes debajo, y organiza selección, geometría y medidas en ese orden. El modo Operate y el contrato de esta superficie están en [el brief](../client-src-components-modelcomparison-jsx.md). No se introduce una identidad nueva ni material gráfico: la geometría se dibuja con WebGL a partir de los modelos.

**Key Characteristics:**

- Controles oscuros de altura mínima accesible, con etiquetas y unidades explícitas.
- Comparación 3D sobre fondo claro: A en alambre azul, B en sólido cobre y muestras seleccionadas en rojo.
- Distancias originales y alineadas legibles junto a los límites del muestreo.
- Una columna móvil y dos columnas de parámetros desde el corte mediano.

La evidencia visual histórica del acabado F1 es `.impeccable/review/desktop.jpg`, `mobile.jpg` y `user-1920.jpg`; su manifiesto es `capture-metadata.json`. Son JPEG nativos sin redimensionado: píxeles físicos 1430 × 2383, 380 × 2923 y 1910 × 857; viewports CSS 1440 × 2400, 390 × 3000 y 1920 × 861, todos desde `scrollY: 0`. Esta diferencia está declarada; no se usan los PNG históricos rechazados. El último veredicto visual `ship` solo puntuó F1 (precisión numérica) como resuelto. Esas fotos preceden a la integración combinada y no documentan el estado completo de Main actual ni constituyen una nueva aprobación visual global.

La revisión formal de código 2016r1 respondió `CHANGES` únicamente por P2: se perdía la última operación al editar. La corrección conserva esa operación aparte del borrador y del resultado visible; 2016r2 recibió `APPROVE`. Las fuentes aprobadas `2e570027` y `564d4118` están integradas en Main y en este worktree, cuyo snapshot de código documentado es `1535d51f6540a1fa16fdafddd1ac0b505f5d147a`. La combinación conservó las aportaciones de las tareas 2020/2021. La QA de integración comunicada por el responsable registra 128 tests pytest, seis tests Node y build satisfactorios, además de paridad real API/MCP sobre Main con Trimesh 5.1.1. La aprobación 2016r2 y esta documentación tienen alcance de comparación: no validan las funciones ajenas de la tarea 2020, cuya revisión pidió cambios por LOD. No se atribuye un nuevo veredicto visual a la integración ni a la corrección de contexto.

## Colors

### Primary

El cobre `accent` identifica «Comparar superficies» y la vista pulsada; `accent-hover` aclara la acción primaria al pasar el ratón. El texto de esos controles usa `accent-ink`.

### Neutral

`paper` sostiene la ficha; `surface` los botones secundarios y paneles vecinos; `sunken` los campos. `elevated` marca el hover secundario. `ink` es el texto principal y `muted` las ayudas. Los separadores usan `line`; el hover de los botones emplea `line-hover`. Los campos de este comparador refuerzan su contorno con `text-dim`, también utilizado por las cabeceras de tabla.

### Geometry and status

El fondo `viewer-ground` se conserva claro. `reference-wire` pertenece al alambre A (opacidad 0,5); `candidate-solid` al sólido B (opacidad 0,82). `deviation-point` señala muestras seleccionadas fuera de tolerancia, con puntos de pantalla de 9px. Estos son colores de materiales 3D, no variantes de la acción primaria. `warn-ink` escribe las unidades asumidas; `danger-ink` los errores.

**The Geometry Roles Rule.** La geometría mantiene los papeles A/alambre, B/sólido y puntos/desviación, acompañados de texto; el color no sustituye las etiquetas.

## Typography

**Body Font:** Source Sans 3 con la pila de sistema indicada en los tokens. El comparador hereda esta familia; la marca y los títulos principales de la aplicación conservan la serif del chrome existente, sin trasladarla a los datos.

### Hierarchy

- **Title:** títulos de sección y pareja comparada, seminegrita; heredan el interlineado del cuerpo.
- **Body:** instrucciones, resultados y tabla; las instrucciones y los límites se acotan a 72ch.
- **Label:** etiqueta encima del campo, con 4px de separación; la ayuda usa `help`.
- **Button:** seminegrita compacta, interlineado propio del control.

Las cifras utilizan números tabulares. `comparison-format.js` formatea en español con hasta ocho cifras significativas; usa notación científica para magnitudes positivas menores de 0,0001 o mayores o iguales a 100000000. Los valores ausentes o no finitos se muestran como raya. Es el mismo formateador para tolerancia, distancias, transformación, volúmenes y coordenadas.

**The Measured Precision Rule.** Una distancia positiva pequeña conserva su magnitud visible; no se redondea a cero para simplificar la tabla.

## Layout

La sección está insertada antes de la cuadrícula existente de visor y ficha. Dos líneas horizontales, relleno vertical de 16px y margen inferior de 20px la separan del resto. La cabecera, búsqueda, conmutadores y herramientas del visor permiten saltos de línea.

Los parámetros usan una cuadrícula de una columna; desde 768px son dos columnas iguales con separación de 12px. El buscador ocupa el espacio flexible y el botón Buscar conserva su ancho. La barra de cámara se coloca al extremo derecho desde 640px cuando cabe. En móvil vuelve a la siguiente línea. Los bloques flexibles llevan ancho mínimo cero para permitir la contracción.

El visor mide 320px de alto en móvil y 420px desde 768px. No hereda la proporción del visor original: su marco local tiene altura explícita y se ajusta mediante ResizeObserver. La tabla ocupa todo el ancho dentro de su propio contenedor con desplazamiento horizontal. Los textos de coordenadas pueden partir líneas. Los controles mantienen altura mínima de 44px; esa cota no se aplica a los disclosures nativos.

## Elevation & Depth

El comparador no tiene sombras. Líneas, tonos y espacio separan funciones; el visor claro es el plano de inspección. La profundidad visible del modelo procede de iluminación y geometría 3D, no de tarjetas elevadas. No se exporta aquí la sombra del toast global, que no forma parte de esta superficie.

## Shapes

Botones y campos usan el radio común de control; el marco 3D conserva su propio radio de visor. Los bordes son finos (1px), sin ornamentos nuevos. El panel exterior queda abierto, limitado arriba y abajo. Los desplegables conservan el selector nativo y los detalles usan el triángulo nativo del navegador.

## Components

### Buttons

La primaria inicia la comparación. Las secundarias abren o cierran el panel, buscan y manejan la cámara. Las dos vistas usan botones independientes con `aria-pressed`; la pulsada aplica cobre, borde cobre y texto oscuro mediante estilo local. «Alineación estimada» queda deshabilitada cuando no se solicitó alineación. No se añade animación al pulsar.

El hover secundario cambia fondo y borde; el primario cambia al cobre claro. La transición heredada afecta fondo y color durante 0,16s. El foco visible común de botones usa contorno cobre de 2px con separación de 3px. Los botones deshabilitados bajan a opacidad 0,45. La preferencia de movimiento reducido elimina transiciones y animaciones CSS; no desactiva los controles de órbita.

### Inputs / Fields

Fondos hundidos, ancho completo, relleno horizontal y vertical corto. La altura mínima es 44px y el borde local usa `text-dim`. Las etiquetas están asociadas mediante `htmlFor`. La búsqueda es independiente del formulario. Mientras se compara, el fieldset se deshabilita; editar parámetros invalida la respuesta anterior y anuncia que hay que comparar de nuevo. El foco del campo cambia la regla CSS de borde a cobre y elimina el outline; el borde local en línea sigue siendo `text-dim`. Este documento registra esa precedencia, sin prometer un halo de foco inexistente.

### Comparison viewer

La leyenda identifica A y B encima del marco; Acercar, Alejar y Encajar proporcionan cámara sin depender de la rueda. El canvas admite foco, tiene nombre accesible y OrbitControls escucha sus teclas. La cámara mantiene Z arriba y encuadra el conjunto; la rotación usa amortiguación. No hay giro automático. El estado escrito explica carga, interacción, error o falta de WebGL; las medidas siguen debajo si el visor falla. El snippet del sidecar representa el marco y su estado escrito, sin fingir una escena 3D estática.

### Measurements and disclosures

La tabla tiene caption con mm, cabeceras alineadas con los números y separadores por fila. Siempre muestra Original; añade Alineada si se solicitó registro rígido. Sin alineación solicitada, el texto confirma que se conserva la colocación original. Los volúmenes se acompañan de mm³; cuando faltan, la ayuda explica su omisión. Repetibilidad y puntos fuera de tolerancia se expanden con detalles nativos. Cada dirección conserva su nombre y el marco de coordenadas activo.

Los errores usan `role="alert"` y los anuncios `role="status"`. La selección nueva y el cambio de referencia invalidan el resultado visible anterior. Las unidades asumidas y los límites del muestreo aparecen como texto junto a la comparación.

El contexto opcional de Faustus separa el borrador actual (`fields`, `dirty_fields`, `phase`, `last_error`) de la última operación terminada (`last_operation`). El estado local `lastCompleted` conserva las fuentes, parámetros y medidas de esa operación histórica aunque se edite el borrador, empiece otra petición o falle. Sin un éxito previo, `last_operation` es nulo; un nuevo éxito vigente lo sustituye. La fase describe el trabajo actual, no el éxito histórico, y el resultado visible obsoleto sigue vacío hasta obtener una comparación nueva. `tests/test_comparison_context.mjs` ejercita éxito → edición → petición pendiente → error → éxito; esta corrección está aprobada en la revisión formal 2016r2 e integrada. Su evidencia de comportamiento es independiente del veredicto visual F1.

## Do's and Don'ts

### Do:

- **Do** conservar juntos los nombres A/B, las unidades y el marco de coordenadas de las medidas.
- **Do** mantener visibles las advertencias de unidades asumidas y los límites del muestreo junto al resultado.
- **Do** reutilizar el formateador de precisión del comparador para todas sus magnitudes.
- **Do** conservar la ficha y el visor originales al abrir o cerrar este panel en línea.

### Don't:

- **Don't** presentar un registro estimado o la ausencia de diferencias muestreadas como identidad probada.
- **Don't** convertir el visor algorítmico en una imagen raster del producto.
- **Don't** aplicar este documento local como sustitución de la identidad global de Vulcan.
