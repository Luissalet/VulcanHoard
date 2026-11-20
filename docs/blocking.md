# Bloqueo de primitivas · Primitive blocking

## Español

En la biblioteca 3D, `blocking_build` genera en CPU una toma de una figura con cabeza, torso, capa corta, piernas, brazos y manos independientes. Los pivotes de hombro, codo, muñeca, cadera, rodilla y tobillo quedan en el rig. Las piezas se mueven de forma rígida con su hueso. Los accesorios opcionales son `sheet`, `tube` y `stairs`. Requiere Blender 4.3 o posterior instalado. La escena se construye sin abrir ventanas.

Prueba `{"gesture":"unroll"}`, `{"gesture":"flip"}` y `{"gesture":"pass"}`. Duran dos segundos a 24 fps, con mapas en los fotogramas 0, 24 y 48. En «desenrollar», la izquierda guarda el tubo mientras la derecha tira de la hoja. En «voltear», la derecha mantiene el borde y gira la hoja. En «pasar», las dos manos coinciden en la clave central antes de que la derecha suelte.

Para una toma propia, elige `gesture: "custom"` con 2 a 8 `poses`, con fotogramas crecientes desde 0 y hasta 96. Cada clave admite `sheet` y `tube` como posiciones en metros, `sheet_rotation` y `tube_rotation` como ángulos XYZ en grados, y `sheet_width` en metros. Para cada mano usa una posición o `rest`, `sheet_left`, `sheet_right` o `tube`. Los nombres de lado se refieren al personaje. Un agarre repetido en dos claves acompaña al accesorio entre ambas. La figura mira hacia −Y, con +Z arriba. Los brazos mantienen sus longitudes y un objetivo fuera de su alcance devuelve un error. Las piernas quedan en una pose de apoyo fija.

`files.scene` conserva la toma y su cámara editables. `files.mesh` es el GLB con rig, cámara y clip que puede leer el siguiente puesto. Usa `keyframes` para elegir los PNG de una clave. Los cinco `anchors` entregan el GLB, la receta y los tres PNG de la primera clave. Para repetir el encuadre, conserva `camera` y `size`. Los PNG comparten proyección ortográfica y píxeles. La profundidad es gris de 16 bits, con 0 para el fondo y valores de 1 a 65535 de lejos a cerca. Las normales RGB codifican −1 a +1 en los ejes de cámara derecha, arriba y hacia la cámara. Su fondo es negro.

`blocking_get` vuelve a leer un paquete por `id`. `blocking_undo` lo retira y devuelve la llamada inversa para restaurarlo. Si el resultado ya pasó a otro puesto, deshacer retira el paquete local de Vulcan.

## English

In the 3D Library, `blocking_build` creates a CPU-only take with separate head, torso, short cape, arms, hands and legs, rigidly bound to a rig with named joints. Props are optional: `sheet`, `tube` and `stairs`. It requires Blender 4.3 or later and runs headlessly.

Choose `unroll`, `flip` or `pass` for a two-second take at 24 fps. Unroll parks the tube in the left hand while the right draws the paper out. Flip rotates the paper around the right-hand grip. Pass places both hands on the paper at the middle key before the right releases. Each preset returns maps at frames 0, 24 and 48.

Choose `custom` with two to eight `poses` to set your own keys, from frame 0 up to 96 in ascending order. Each key accepts `sheet` and `tube` positions in metres, XYZ `sheet_rotation` and `tube_rotation` in degrees, and `sheet_width` in metres. Give each hand a position or a grip from `rest`, `sheet_left`, `sheet_right` and `tube`. Left and right are relative to the character. A grip held through adjacent keys travels with the prop. The character faces −Y with +Z up. Arms keep fixed lengths and reject unreachable targets. Legs stay in a fixed support pose.

`files.scene` gives the editable take and camera. `files.mesh` gives the rigged GLB with its camera and clip. Open `keyframes` for the PNG maps belonging to a specific frame. The five `anchors` point to the GLB, the recipe and the first key's three PNGs. Preserve `camera` and `size` for matched framing. Each map uses the same orthographic projection and pixel grid. The 16-bit grayscale depth uses 0 for background and 1 to 65535 from far to near. RGB normals encode −1 to +1 for camera right, up and toward the camera, with black background.

`blocking_get` reads the package by `id`. `blocking_undo` withdraws it and returns the inverse call to restore it. This operates on the package retained by Vulcan.
