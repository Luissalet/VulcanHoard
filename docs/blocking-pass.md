# Detailed sheet handoff / Pase de hoja en un modelo detallado

## Español

`blocking_pass` parte de la escena editable y autónoma que devuelve
`articulation_build`. Fija `source` con `source_sha256` y elige
`source_frame` en la animación de origen. Los puntos se dan en metros,
con Z hacia arriba. Los lados son los del personaje. Indica la malla
`sheet` y un punto sobre cada canto en `left_edge` y `right_edge`.

- Marca hombro, codo, muñeca, agarre de reposo (`bind_contact`) y dirección
  del codo (`pole`) de cada brazo. Para una manga integrada en la malla,
  indica sus radios de brazo, antebrazo y mano en `skin`. Para piezas
  separadas, usa `forearm_parts` y `hand_parts`.
- `start` (f0) y `overlap` (f24) dirigen la mano que entrega. En f24 la
  segunda mano alcanza el otro canto. `finish` (f48) dirige la mano que
  recibe. En cada clave, `contact` sitúa el agarre y `rotation` da el giro
  XYZ de la hoja en grados respecto a su posición de origen. `grip_rotation`
  ajusta el giro de cada mano respecto a la hoja.
- `approach_contact` dirige la mano que recibe en f12. La hoja sigue en la
  mano que entrega. `review_camera` añade una vista por clave para cotejar
  el codo, la manga y el agarre. `sheet_art` admite un PNG o JPEG con su
  huella y la dirección que corresponde a la parte superior del dibujo.

El resultado incluye la escena editable, un GLB animado, los controles
para repetir la toma, vistas de color por clave y la posición de codos,
muñecas y hoja por fotograma. El recibo de cada clave mide la distancia de
una muestra de la mano al canto y a la superficie de la hoja.

`blocking_freeze {id, frame:24}` entrega un GLB estático del doble agarre.
`blocking_guide {id, include_end:false}` entrega un MP4 de dos segundos
con 48 fotogramas a 24 fps. Retira o restaura cada salida con la llamada
`undo` que devuelven `blocking_pass`, `blocking_freeze` y `blocking_guide`.

## English

`blocking_pass` takes an editable, self-contained scene from
`articulation_build`. Pin `source` with its `source_sha256` and choose a
`source_frame`. Coordinates are metres, Z up. Left and right refer to the
character. Name the `sheet` mesh and supply `left_edge` and `right_edge` points.

- Mark each shoulder, elbow, wrist, rest contact (`bind_contact`) and elbow
  bend direction (`pole`). Supply arm, forearm and hand influence radii in
  `skin` for sleeves in a larger mesh. Use `forearm_parts` and `hand_parts`
  for separated pieces.
- `start` (f0) and `overlap` (f24) direct the giving hand. At f24 the
  receiving hand reaches the opposite edge. `finish` (f48) directs the
  receiving hand. Each `contact` sets the grip position. `rotation` is the
  sheet's source-relative XYZ rotation in degrees. `grip_rotation` adjusts
  the hand's orientation against the sheet.
- `approach_contact` directs the receiving hand at f12 while the sheet
  follows the giving hand. `review_camera` adds a key view of the elbow,
  sleeve and grip. `sheet_art` accepts a pinned PNG or JPEG and a direction
  pointing towards the top of the artwork in source space.

The take includes an editable scene, an animated GLB, replayable controls,
key colour views and per-frame elbow, wrist and sheet locations. The key
receipts measure each hand's mesh sample against the edge and sheet surface.

`blocking_freeze {id, frame:24}` bakes the two-handed pose to static GLB.
`blocking_guide {id, include_end:false}` renders a two-second MP4 with
48 frames at 24 fps. Each export returns an `undo` call that can withdraw or
restore it.
