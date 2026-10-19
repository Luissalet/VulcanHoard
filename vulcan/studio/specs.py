"""Static capabilities; no imports of any engine or native store."""
SPECS = [
    {'id': 'library', 'name': 'Biblioteca 3D', 'origin': 'Vulcan',
     'description': 'Modelos STL, 3MF y OBJ, medidas, miniaturas, colecciones y fichas de tienda.',
     'automation': 'Vigilar carpetas y reescanear las bibliotecas al activar.'},
    {'id': 'gepetto', 'name': 'Figuras y relieves', 'origin': 'Gepetto',
     'description': 'Texturas, UV, regiones semánticas, relieve editable y proyectos Blender.', 'automation': None},
    {'id': 'daedalus', 'name': 'Generación 3D', 'origin': 'Daedalus',
     'description': 'Imagen a malla, refinado, reparación y preparación de modelos para juegos.',
     'automation': 'Reanudar los trabajos pendientes al activar este módulo.'},
    {'id': 'plato', 'name': 'Siluetas y extrusión', 'origin': 'Plato',
     'description': 'Editor de siluetas, composición, ajuste, relieve y exportación STL.', 'automation': None},
    {'id': 'heron', 'name': 'CAD con medidas', 'origin': 'Heron',
     'description': 'Recetas paramétricas, booleanas y sólidos verificados STEP y STL.', 'automation': None},
    {'id': 'gulliver', 'name': 'Terrenos y ciudades', 'origin': 'Gulliver',
     'description': 'Mapas, relieve geográfico, edificios y conjuntos imprimibles STL y 3MF.', 'automation': None},
]
