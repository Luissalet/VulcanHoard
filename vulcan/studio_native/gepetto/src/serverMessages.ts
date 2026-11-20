import { language } from './i18n';

// Native services return these messages as text, including in saved proposals and jobs.
const english = new Map([
  ['Esperando para generar la pose…', 'Waiting to generate pose…'],
  ['Abriendo el personaje en Blender…', 'Opening the character in Blender…'],
  ['Preparando la vista de referencia del personaje…', 'Preparing the character reference view…'],
  ['Interpretando la imagen y ajustando la pose…', 'Interpreting the image and fitting the pose…'],
  ['Creando la vista previa de la propuesta…', 'Creating the proposal preview…'],
  ['Propuesta lista para revisar', 'Proposal ready for review'],
  ['Propuesta cancelada', 'Proposal cancelled'],
  ['No se pudo generar la pose', 'Could not generate pose'],
  ['Proceso interrumpido', 'Process interrupted'],
  [
    'El proceso anterior se interrumpió. Genera otra propuesta.',
    'The previous process was interrupted. Generate another proposal.',
  ],
  ['Propuesta no encontrada.', 'Proposal not found.'],
  ['No encuentro Blender en este equipo.', 'Blender was not found on this computer.'],
  ['Proveedor no compatible.', 'Unsupported provider.'],
  [
    'Espera a que termine la propuesta actual o cancélala.',
    'Wait for the current proposal to finish or cancel it.',
  ],
  ['Hay otra operación de IA en marcha.', 'Another AI operation is running.'],
  ['Este proyecto no contiene una escena Blender.', 'This project does not contain a Blender scene.'],
  ['Ruta del proyecto no válida.', 'Invalid project path.'],
  ['Elige un archivo .blend o un proyecto Blender.', 'Choose a .blend file or a Blender project.'],
  ['Selecciona un archivo .blend existente.', 'Select an existing .blend file.'],
  [
    'La escena supera 160 MB. Usa el botón desde Blender para este archivo.',
    'The scene exceeds 160 MB. Use the pose button in Blender for this file.',
  ],
  [
    'No hay card.png junto al .blend. Elige una imagen de referencia.',
    'There is no card.png next to the .blend file. Choose a reference image.',
  ],
  ['La referencia supera 20 MB.', 'The reference exceeds 20 MB.'],
  [
    'Usa una imagen PNG, JPG o WebP de hasta 40 megapíxeles.',
    'Use a PNG, JPG or WebP image up to 40 megapixels.',
  ],
  ['La imagen es demasiado pequeña.', 'The image is too small.'],
  [
    'El posador tardó demasiado. Revisa la conexión o el modelo elegido.',
    'Pose generation timed out. Check the connection or selected model.',
  ],
  [
    'Blender no pudo completar la propuesta. Consulta el registro local.',
    'Blender could not finish the proposal. Check the local log.',
  ],
  ['La propuesta está incompleta.', 'The proposal is incomplete.'],
  [
    'Selecciona una escena con una única armadura de personaje.',
    'Select a scene with a single character armature.',
  ],
  [
    'La comprobación de articulaciones detectó un cambio no permitido.',
    'The joint check found an unexpected change.',
  ],
  [
    'La vista previa no muestra al personaje; no se entrega la propuesta.',
    'The preview does not show the character. The proposal could not be saved.',
  ],
  ['La carpeta no existe.', 'The folder does not exist.'],
  ['Máximo 160 MB por escena.', 'Maximum 160 MB per scene.'],
  ['Acción desconocida.', 'Unknown action.'],
  ['En espera.', 'Queued.'],
  [
    'El servidor se reinició. Puedes reintentar desde la última fase completa.',
    'The server restarted. You can retry from the last completed stage.',
  ],
  ['Se detendrá al terminar la operación actual.', 'It will stop when the current operation finishes.'],
  ['Preparado para reintentar.', 'Ready to retry.'],
  [
    'Reanudando con las regiones y mapas calculados antes del fallo.',
    'Resuming with the regions and maps calculated before the error.',
  ],
  ['Guardando el proyecto y sus evidencias.', 'Saving the project and its evidence.'],
  ['Mapas calculados. Pendiente de revisión visual.', 'Maps calculated. Ready for visual review.'],
  ['Espera a que termine el cálculo de este proyecto.', 'Wait for this project to finish processing.'],
  ['Indica la versión que quieres recuperar.', 'Choose the version to restore.'],
  ['Guarda el proyecto antes de crear una versión.', 'Save the project before creating a version.'],
  ['No hay una versión disponible en esa dirección.', 'There is no version available in that direction.'],
  [
    'El proyecto cambió; recarga antes de aplicar el contraste.',
    'The project changed. Reload before applying contrast.',
  ],
  [
    'Ese contraste excede el rango. Reduce el factor para conservar todas las alturas sin recortarlas.',
    'That contrast is out of range. Reduce the factor to preserve all heights without clipping.',
  ],
  [
    'Ese factor uniría alturas distintas. Usa un contraste menos extremo.',
    'That factor would merge distinct heights. Use a smaller change in contrast.',
  ],
  [
    'El modelo está interpretando las vistas 3D y sus UV.',
    'The model is interpreting the 3D views and their UVs.',
  ],
  [
    'Propuesta preparada. Revisa las relaciones antes de aplicarla.',
    'Proposal ready. Review the relationships before applying it.',
  ],
  [
    'El servidor se reinició durante el análisis. Puedes volver a intentarlo.',
    'The server restarted during analysis. You can try again.',
  ],
  ['Preparando el análisis local.', 'Preparing local analysis.'],
  ['Preparando Figure Tools.', 'Preparing Figure Tools.'],
  ['Preparando el modelo.', 'Preparing the model.'],
  [
    'El proyecto cambió. La evaluación se conserva, pero no se aplican alturas antiguas.',
    'The project changed. The evaluation was kept and the previous heights were left unapplied.',
  ],
]);

const prefixes = [
  ['Este rig no usa las articulaciones ACNH compatibles: ', 'The rig is missing required joints: '],
  ['No es una escena Blender: ', 'This is not a Blender scene: '],
  ['No se pudo completar el análisis: ', 'Could not complete analysis: '],
  ['Reutilizando la fase terminada: ', 'Reusing completed stage: '],
];

export function serverMessage(message?: string | null): string {
  if (!message) return '';
  if (language === 'es') return message;
  for (const [prefix, translated] of prefixes) {
    if (message.startsWith(prefix)) return translated + message.slice(prefix.length);
  }
  return english.get(message) ?? message;
}
