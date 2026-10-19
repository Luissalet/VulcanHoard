const words = {
  title: ['Guía de montaje', 'Assembly guide'],
  intro: ['Plano y archivos de una generación guardada, con sus colores y materiales.', 'Plan and files from a saved build, with its colours and materials.'],
  build: ['Generación', 'Build'], current: ['Actual', 'Current'],
  prepare: ['Preparar guía', 'Prepare guide'], preparing: ['Preparando guía…', 'Preparing guide…'],
  open: ['Abrir guía para imprimir', 'Open printable guide'], download: ['Descargar HTML', 'Download HTML'],
  note: ['Vista cenital, norte arriba. Piezas separadas para ver los cantos; no es una plantilla a tamaño real. Las posiciones de los pines no se dibujan.', 'Top view, north up. Pieces are separated to show their edges; this is not a full-size template. Pin positions are not drawn.'],
  more: ['Más piezas', 'More pieces'], files: ['Archivos', 'Files'],
  unknown: ['Material sin especificar', 'Material unspecified'],
  legend: ['Colores y materiales del 3MF', '3MF colours and materials'],
  fit: ['Cabe en la cama', 'Fits the bed'], nofit: ['Revisar cama', 'Check printer bed'],
  original: ['Los números acompañan al identificador de cada archivo. La guía conserva esta generación aunque cambies la maqueta o sus perfiles.', 'Numbers accompany each file identifier. The guide retains this build when the model or its profiles change.'],
  history: ['Sin parámetros históricos: la guía no deduce pines ni marcas inferiores de los ajustes actuales.', 'No saved parameter snapshot: the guide does not infer pins or underside labels from current settings.'],
};
export const assemblyT = lang => key => words[key]?.[lang === 'en' ? 1 : 0] || key;
