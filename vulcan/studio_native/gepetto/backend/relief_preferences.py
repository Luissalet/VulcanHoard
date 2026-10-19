"""Artist's default local relief order; explicit edits may override it."""

# Applied once to a new semantic proposal. Subsequent user edits remain literal.
ROLE_HEIGHTS = {
    "skin": 128,
    "sclera": 64,
    "iris": 84,
    "pupil": 104,
    "eyelid": 160,
    "eyelash": 192,
    "eyebrow": 192,
    "nostril": 96,
    "freckle": 176,
    "coating": 160,
    "hair": 160,
    "modeled": 128,
    "uncertain": 128,
    "nose": 128,
    "muzzle": 128,
    "beak": 128,
    "mouth": 96,
    "mouth_opening": 96,
    "lip": 144,
    "sucker_rim": 160,
    "sucker_center": 96,
    "fabric": 128,
    "inner_fabric": 96,
    "belt": 160,
    "buckle": 192,
    "trim": 160,
    "decoration": 160,
    "base": 128,
}

INITIAL_BRIEF = """El objetivo es una figura que conserve sus detalles al imprimirla
sin color. La preferencia del artista es que las pecas y la cobertura superior
tengan relieve sobre la piel. Mantén una altura plana por superficie, sin convertir
sombras o degradados en escalones. El orden de los ojos indicado es obligatorio.
En ropa, conserva los estampados y el orden físico de las prendas superpuestas.
La imagen 1 muestra el original completo y la imagen 2 el atlas de este material.
Los demás materiales son contexto. La comprobación geométrica se hace después;
en esta petición NO hay una imagen de control uniforme."""

EYE_ORDER = """The artist's preferred eye relief order, from lowest to highest, is:
eye white (sclera) < iris < pupil < skin < eyelid < eyebrow/eyelash.
Use this order for recognizable painted eye surfaces. A starting palette is
sclera=64, iris=84, pupil=104, skin=128, eyelid=160, eyebrow/eyelash=192.
The order is a user preference, not inferred from RGB brightness. Do not raise
the white above the pupil or lower the pupil below the white. If the artwork
does not distinguish iris and pupil, do not invent an extra region. Highlights
are not automatically sclera. Keep nostrils independent from same-color pupils.
A pupil belongs inside an actual eye. Round freckles or markings elsewhere on
the face are not pupils; preserve such decorative markings as raised detail.
An eyelid touches and occludes the eye opening. A coating or patch across the
crown of the head is not an eyelid. Use the complete original figure as context.
An iris lies around the pupil WITHIN the eye white. A colored upper half-disk
that covers or cuts across the top of the pupil is an EYELID, not an iris.
Eyelashes can be pale, blue or white: identify their short projections outside
the outer upper eye corners. Never group these visible projections with skin
just because they are small or have an unusual color. An eyelid and an eyelash
are separate surfaces even when they touch. Verify this before assigning heights.
These are local displacements on the existing model, not absolute world positions.
Explicit user edits take precedence; preserve unrelated parts when editing.
"""
