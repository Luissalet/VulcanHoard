---
name: Vulcan's Hoard
description: Una biblioteca personal de modelos para impresión 3D, con medidas, miniaturas y fichas para la tienda.
colors:
  accent: "#b4471f"
  accent-hover: "#8f3717"
  accent-soft: "#f6e4da"
  ink: "#2b2523"
  muted: "#7a6f6b"
  paper: "#fbf9f6"
  white: "#ffffff"
  line: "#e9e2dc"
  soft: "#f3eeea"
  sidebar: "#f5f0eb"
  nav-active: "#f1ddd0"
  nav-active-ink: "#8f3717"
  nav-hover: "#eee7e1"
  field-line: "#d9cfc7"
  field-ink: "#2f2826"
  placeholder: "#9a8f89"
  supporting-ink: "#6e645f"
  focus: "#c9683f"
  button-line: "#ddd3cb"
  panel: "#f6f2ee"
  ok-bg: "#e5efe4"
  ok-ink: "#2f5f3a"
  warn-bg: "#f8ecd2"
  warn-ink: "#7a5a17"
  danger-bg: "#f9e8e6"
  danger-ink: "#8a2f26"
  danger-line: "#eac6c1"
  bar-bg: "#e8dfd8"
  highlight: "#f7e7a7"
typography:
  headline:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.35
  body:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    lineHeight: 1.65
  dims:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontVariantNumeric: "tabular-nums"
  path:
    fontFamily: "Consolas, monospace"
    fontSize: "12px"
  button:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: "18px"
  label:
    fontFamily: "Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
  code:
    fontFamily: "Consolas, monospace"
    fontSize: "12px"
rounded:
  badge: "5px"
  field: "6px"
  control: "7px"
  panel: "8px"
  search: "9px"
spacing:
  control-gap: "8px"
  action-gap: "10px"
  page-gutter: "40px"
  page-gutter-mobile: "16px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.white}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  button-secondary:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    typography: "{typography.button}"
    rounded: "{rounded.control}"
    padding: "8px 15px"
  field:
    backgroundColor: "{colors.white}"
    textColor: "{colors.field-ink}"
    rounded: "{rounded.field}"
    padding: "8px 11px"
  search-field:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.search}"
    padding: "12px 16px"
    fontSize: "16px"
  card:
    backgroundColor: "{colors.white}"
    rounded: "{rounded.panel}"
    thumb: "square, object-fit contain, soft radial paper background"
    padding: "10px 12px"
  viewer:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.panel}"
    aspect: "4:3, max-height 70vh"
  filter-sidebar:
    width: "220px"
    sticky: "top 16px on desktop; full-screen overlay under 768px"
  chip:
    typography: "10px / 600 / uppercase"
    rounded: "{rounded.badge}"
    padding: "2px 6px"
---

# Design System: Vulcan's Hoard

## Overview

**Creative North Star: "La fragua"**

Un taller ordenado: cada modelo es una pieza sobre papel cálido, vista siempre desde la misma esquina, con sus medidas al lado. El acento es un naranja rojizo de forja (#b4471f), reservado a la acción principal, al estado activo y a las barras de progreso. Las miniaturas se renderizan en un gris cálido neutro (color PLA) sobre fondo transparente para que la cuadrícula no compita con el modelo. La interfaz está en español de España.

**Key Characteristics:**

- Una cuadrícula de miniaturas cuadradas con nombre, medidas en mm y chips de formato: la biblioteca se lee de un vistazo.
- La página de modelo pone el visor 3D en grande, con rejilla en la base de impresión (Z arriba), y la ficha para la tienda justo debajo: ver y describir a la vez.
- Los avisos de geometría se escriben (estanco, con agujeros, unidades dudosas, duplicado, error), no se dibujan con iconos.
- Todo cambio de una sola cosa se guarda solo (nombre, etiquetas, notas, ficha); los formularios solo existen para crear (carpetas, álbumes).

## Colors

### Primary

- `accent` #b4471f (forja) para el botón primario, la navegación activa, las barras y la marca «V».
- `accent-hover` #8f3717 y `accent-soft` #f6e4da (chips de formato, etiquetas).

### Neutral

- `paper` #fbf9f6 fondo; `white` tarjetas y paneles; `sidebar` #f5f0eb; `panel` #f6f2ee formularios.
- `ink` #2b2523 texto; `supporting-ink` #6e645f ayudas y medidas; `line` #e9e2dc bordes.
- Semánticos: `ok` verde apagado (estanco, con ficha, al día), `warn` ámbar (con agujeros, unidades dudosas, duplicado, sin analizar), `danger` (archivo con error, quitar).
- Material de las miniaturas: RGB (214, 205, 192) con luz fija desde arriba a la izquierda; nunca se colorea por formato.

## Typography

**Body Font:** Segoe UI (system-ui de respaldo). Consolas solo para rutas y hashes.

- **Headline:** título de página 30px (26px en móvil). El nombre del modelo es un campo editable con el mismo tamaño.
- **Title:** títulos de sección 16px seminegrita.
- **Body:** 14px; ayudas, medidas y metadatos 12px con cifras tabulares; chips 10px mayúsculas.
- **Dims:** las medidas se escriben siempre como `20 × 30 × 40 mm` con el signo ×, una décima si el valor es menor de 100.

## Layout

Escritorio: índice fijo de 224px + contenido flexible (`min-width: 0`), márgenes de 40px. La galería usa una barra lateral de filtros de 220px (pegajosa) y una cuadrícula de 2 a 5 columnas según el ancho. Modelo usa dos columnas: visor + ficha (3fr) y geometría + etiquetas + duplicados (2fr). Carpetas apila formulario y tarjetas. Estadísticas usa dos filas de cuatro cifras.

- Hasta 768px: el índice pasa a barra superior con navegación horizontal desplazable; márgenes de 16px; los filtros se abren a pantalla completa con un botón «Filtros»; la cuadrícula baja a dos columnas y el visor conserva la proporción 4:3.
- No hay desplazamiento horizontal de página a 390px: todo contenedor de cuadrícula lleva `min-width: 0` y las rutas se truncan.

## Elevation & Depth

Plano por defecto. Sombras solo en el aviso flotante (toast). La tarjeta bajo el ratón cambia el borde a `accent`, sin sombra. El visor 3D lleva borde de 1px, no marco.

## Shapes

Campos 6px, controles 7px, paneles y tarjetas 8px, el buscador principal 9px, chips 5px, etiquetas 12px (píldora). Bordes de 1px. Iconos SVG de línea; los formatos son chips de texto (STL, 3MF, OBJ) sobre `accent-soft`. Las miniaturas son la única imagen raster.

## Components

### Buttons

Primario (forja), secundario (blanco con borde), peligro (rojo suave: «Quitar», «Eliminar»). Altura mínima 38px; variante `btn-sm` de 30px para acciones de fila. Enlaces de acción (`btn-link`) en `accent`.

### Inputs / Fields

Etiqueta encima, ayuda debajo. El buscador de la galería es más grande (16px, 12px de relleno). Las etiquetas se editan como píldoras con × y un campo que añade al pulsar Intro o coma. Los campos de la ficha, las notas y el nombre se guardan solos con un retardo corto y muestran «Ficha guardada» / «Notas guardadas».

### Navigation

Cinco secciones: Galería, Colecciones, Carpetas, Estadísticas, Ajustes. Rutas por hash (`#/galeria?format=stl`, `#/modelo/12`). La activa usa `aria-current` con fondo `nav-active`. Los filtros de la galería viven en la URL para poder compartir o volver atrás.

### Chips

Formato (STL/3MF/OBJ), triángulos, «ficha» (ok), «abierto», «unidades», «duplicado» (warn), «error» (danger), fase de escaneo. Texto en mayúsculas de 10px.

### Cards and viewer

Cada tarjeta es un enlace: miniatura cuadrada, nombre truncado, medidas y chips. El visor usa three.js con controles de órbita, cámara desde delante-derecha-arriba, rejilla en el plano de la base y el modelo centrado y ajustado al abrir; una línea de ayuda abajo a la izquierda indica cómo girar, acercar y desplazar.

### Progress and errors

La carpeta muestra fase escrita, contador (archivos hechos/total y archivo actual) y barra en `accent` mientras trabaja; al terminar, resumen (leídos, eliminados, miniaturas, sin analizar, segundos). Los errores por archivo se despliegan en una lista sobre fondo `danger`.

### Empty states

Un título, una frase y una única acción («Añadir una carpeta») que lleva a Carpetas.

## Do's and Don'ts

### Do:

- **Do** mostrar siempre las medidas en mm junto al nombre; son lo que el usuario busca al elegir un modelo.
- **Do** renderizar todas las miniaturas desde la misma cámara y con la misma luz para que la cuadrícula sea comparable.
- **Do** escribir los avisos de geometría (con agujeros, unidades dudosas) donde se ve el modelo; un archivo que no se puede imprimir bien debe notarse antes de abrirlo.
- **Do** dejar la ficha para la tienda como texto que el usuario corrige; el asistente propone, el usuario publica.

### Don't:

- **Don't** convertir las tarjetas en paneles elevados ni añadir sombras al visor.
- **Don't** usar el acento para texto largo; reservarlo a acciones, estado activo y progreso.
- **Don't** colorear las miniaturas por formato o colección: el color del material es siempre el mismo.
