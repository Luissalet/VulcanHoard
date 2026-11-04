# Ajustar una receta CAD a una malla

En la ficha de una pieza generada con CAD, abre **Consultar receta CAD → Ajustar la receta a una malla**. Introduce el ID de la malla de referencia ya indexada. Ambos modelos deben usar los mismos ejes; la traslación no afecta a las medidas. Si un STL no trae unidad, confirma que está en mm o elige la unidad real.

Selecciona hasta cuatro parámetros numéricos independientes y sus intervalos. Cada intervalo debe incluir el valor guardado y respetar los límites de la receta. Activa las medidas observables: ancho, fondo, alto o volumen. Cada tolerancia es absoluta, en mm o mm³. Para medir volumen, la malla debe estar cerrada, orientada coherentemente y tener volumen positivo. Las expresiones de la receta se conservan.

Por ejemplo, en una placa con un agujero pasante, ancho y alto pueden fijar el tamaño exterior, mientras que el volumen ayuda a recuperar el diámetro si el fondo y las demás dimensiones ya están definidos. Desplazar el agujero dentro de la placa deja esas medidas iguales: el ajuste indica ambigüedad.

La tabla muestra medidas iniciales, medidas objetivo, valores ajustados y residuos. Si encajan y los parámetros son distinguibles localmente, **Pasar al borrador CAD** carga los valores sin guardar. Usa **Crear una variante sin reemplazar el original** para guardar otra pieza. Compara sus superficies con la referencia si necesitas verificar también su forma.

## API y herramienta

POST `/library/api/cad/fit` en el estudio (`/api/cad/fit` dentro de la biblioteca); MCP `cad_fit_parameters`.

```json
{
  "model_id": 10,
  "reference_id": 14,
  "reference_units": "mm",
  "parameters": {
    "width": {"min": 36, "max": 55},
    "thickness": {"min": 3, "max": 12},
    "diameter": {"min": 3, "max": 15}
  },
  "tolerances": {"bbox_x_mm": 0.05, "bbox_z_mm": 0.05, "volume_mm3": 0.5}
}
```

`recipe_sha256` es opcional: usa la huella de CAD info para rechazar una receta distinta de la que leíste. El resultado incluye esa huella, el SHA de la malla, sus unidades y el residuo de cada medida. Estados: `fit`, `ambiguous`, `outside_tolerance` o `incomplete`. Si una geometría de prueba es inválida o se agota el presupuesto de búsqueda, el resultado incompleto puede incluir la mejor prueba válida observada.

La búsqueda local parte de la receta guardada y evalúa hasta 96 compilaciones. El rango local estima cuántas combinaciones de parámetros afectan a las medidas a la escala de sus tolerancias. Un ajuste conforme no demuestra unicidad global, correspondencia de superficies ni idoneidad para fabricar. Una búsqueda fuera de tolerancia tampoco prueba que no exista otra solución.
