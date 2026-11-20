# Blocking video guide / Vídeo guía del bloqueo

Build a blocking with `blocking_build`, then call `blocking_guide` with its
`id`. The tool returns an H.264 MP4 at the same frame rate and with the same
camera as the blocking. It evaluates the scene on the CPU at every frame,
including the final key. The receipt gives the animated duration and, separately,
the MP4 duration including the last frame. Import the returned `path` as a video
asset and use its ID as the shot's control asset. To withdraw or restore the
export, use its returned `undo` call.

Crea el bloqueo con `blocking_build` y llama a `blocking_guide` con su
`id`. La herramienta devuelve un MP4 H.264 con la misma cámara y frecuencia
que el bloqueo. Evalúa la escena en CPU en cada fotograma, incluida la última
clave. El recibo indica la duración animada y también la duración del MP4 con
el último fotograma. Importa el `path` devuelto como vídeo y usa su ID como
recurso de control del plano. Para retirar o restaurar la exportación, usa
la llamada `undo` devuelta.
