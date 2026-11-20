# Blocking video guide / Vídeo guía del bloqueo

Build a take with `blocking_build` or `blocking_pass`, then pass its `id` to
`blocking_guide`. The H.264 MP4 uses the take's camera and frame rate. It
uses the CPU by default and samples through the last key. The receipt gives
the animated and MP4 durations. Set `include_end:false` to match the animated
duration exactly. For a detailed take, `gpu` selects a CUDA device by index
and reserves 4 GiB of VRAM in the Hub. Import the returned `path` as a video
asset and use its ID as the shot's control asset. The returned `undo` call
withdraws or restores the export.

Crea la toma con `blocking_build` o `blocking_pass` y pasa su `id` a
`blocking_guide`. El MP4 H.264 usa la cámara y la frecuencia de la toma.
Usa la CPU por defecto y toma muestras hasta la última clave. El recibo
indica la duración animada y la del MP4. Usa `include_end:false` para que
ambas coincidan. En una toma detallada, `gpu` elige un dispositivo CUDA
por índice y reserva 4 GiB de VRAM en el Hub. Importa el `path` devuelto
como vídeo y usa su ID como recurso de control del plano. La llamada
`undo` retira o restaura la exportación.
