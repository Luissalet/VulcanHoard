"""Agent entry points for selecting, repairing and articulating an inferred mesh."""
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from ..hoard_link.agentkit import Tool, ann
from . import service

Vec = tuple[FiniteFloat, FiniteFloat, FiniteFloat]
Name = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")]
PackageId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
RGB = tuple[Annotated[FiniteFloat, Field(ge=0, le=1)], Annotated[FiniteFloat, Field(ge=0, le=1)],
            Annotated[FiniteFloat, Field(ge=0, le=1)]]
Positive = Annotated[FiniteFloat, Field(gt=0)]
FaceIndex = Annotated[int, Field(ge=0)]
View = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,100}\.png$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-fA-F]{64}$")]


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Plane(Args):
    point: Vec
    normal: Vec = Field(description="Signed half-space. Selects faces on the positive side, along existing edges.")


class Colour(Args):
    rgb: RGB = Field(description="Linear RGB in [0,1], averaged per source face.")
    tolerance: Positive


class Seed(Args):
    point: Vec
    distance: Positive = Field(description="Maximum surface distance, in metres, from the closest eligible face.")


class Selection(Args):
    lower: Vec | None = Field(None, description="Inclusive minimum XYZ of face centres.")
    upper: Vec | None = Field(None, description="Inclusive maximum XYZ of face centres.")
    planes: list[Plane] = Field(default_factory=list, max_length=8)
    colour: Colour | None = None
    seed: Seed | None = None
    face_ranges: list[tuple[FaceIndex, FaceIndex]] = Field(
        default_factory=list, max_length=32768,
        description="Inclusive source face ranges from this GLB revision. / Tramos inclusivos de caras de esta revisión del GLB.",
    )

    @model_validator(mode="after")
    def check(self):
        if not any((self.lower, self.upper, self.planes, self.colour, self.seed, self.face_ranges)):
            raise ValueError("Provide a region, plane, colour, seed or face range. / Indica una región, un plano, un color, una semilla o un tramo de caras.")
        if self.lower and self.upper and any(low > high for low, high in zip(self.lower, self.upper)):
            raise ValueError("Region bounds are reversed. / Los límites de la región están invertidos.")
        if any(not any(plane.normal) for plane in self.planes):
            raise ValueError("A plane normal cannot be zero. / La normal del plano no puede ser cero.")
        previous = -1
        for start, end in self.face_ranges:
            if not previous < start <= end:
                raise ValueError("Face ranges must be disjoint and sorted. / Los tramos de caras deben estar ordenados y no solaparse.")
            previous = end
        return self


class Part(Args):
    name: Name
    select: Selection
    bone: Name | None = None
    pivot: Vec | None = None
    discard: bool = False


class ObjectReference(Args):
    ref: str = Field(min_length=1, max_length=500)
    revision: Digest
    dimensions_m: tuple[Positive, Positive]
    finish: str = Field(min_length=1, max_length=500)


Unit = Annotated[FiniteFloat, Field(ge=0, le=1)]


class DossierObject(Args):
    path: str = Field(description="Absolute dossier JSON path. / Ruta absoluta al JSON del expediente.")
    ref: str = Field(min_length=1, max_length=500, description="Object ref in the dossier. / Referencia del objeto en el expediente.")
    revision: Digest = Field(description="SHA-256 of the dossier file. / SHA-256 del fichero del expediente.")
    right: Vec = Field(description="Panel's rightward axis in source space. / Eje hacia la derecha del panel en el espacio de origen.")
    up: Vec = Field(description="Panel's upward axis in source space. / Eje hacia arriba del panel en el espacio de origen.")
    scale: Positive = Field(description="Uniform scene scale, 1 for real size. / Escala uniforme de escena, 1 para tamaño real.")
    grip_uv: tuple[Unit, Unit] = Field(description="Grip within the full rectangle, from bottom left (0,0). / Agarre en el rectángulo completo, desde abajo a la izquierda (0,0).")


class Panel(Args):
    name: Name
    outline: list[Vec] | None = Field(None, min_length=3, max_length=128, description="Ordered planar outline in source space. / Contorno plano ordenado en el espacio de origen.")
    thickness: Positive
    rgb: RGB
    reference: str = Field(min_length=1, max_length=500, description="Reference identifying the added geometry.")
    bone: Name | None = None
    pivot: Vec | None = None
    source_object: ObjectReference | None = Field(None, description="Pinned dossier object and finish. / Objeto fijado del expediente y acabado.")
    dossier_object: DossierObject | None = Field(None, description="Build a clipped rectangle from a pinned dossier. / Construye un rectángulo con una esquina cortada desde el expediente fijado.")

    @model_validator(mode="after")
    def check_geometry(self):
        if self.dossier_object:
            if self.outline is not None or self.source_object is not None or self.pivot is None:
                raise ValueError("dossier_object requires pivot and takes the place of outline and source_object. / dossier_object requiere pivot y sustituye a outline y source_object.")
        elif self.outline is None:
            raise ValueError("Provide outline or dossier_object. / Indica outline o dossier_object.")
        return self


class Reconstruction(Args):
    name: Name
    path: str = Field(description="Triangulated GLB in source coordinates and metres.")
    sha256: Digest
    reference: str = Field(min_length=1, max_length=500)
    bone: Name | None = None
    anchor: Vec = Field(description="Connection point in source space. / Anclaje en el espacio de origen.")


class Bone(Args):
    name: Name
    head: Vec
    tail: Vec
    parent: Name | None = None


class Pose(Args):
    name: Name
    rotations: dict[Name, Vec] = Field(default_factory=dict, description="Bone-local XYZ Euler angles in degrees.")
    export: bool = True
    hide: list[Name] = Field(default_factory=list, description="Pieces excluded from this pose and its frozen export.")
    turn_degrees: FiniteFloat = Field(0, description="World Z rotation around the scene origin.")
    cameras: list[Name] | None = Field(None, max_length=8, description="Preview from these cameras. / Usa estas cámaras para la vista previa.")


class Camera(Args):
    name: Name
    direction: Vec = Field(description="World offset from the common focus, looking towards the subject.")
    focus: Vec | None = Field(None, description="Optional camera target in source space. / Punto de mira en el espacio de origen.")
    view_scale: Positive | None = Field(None, description="Optional orthographic span in metres. / Anchura ortográfica en metros.")


class Build(Args):
    source: str = Field(description="Absolute path to a self-contained GLB, in metres.")
    source_ref: str | None = Field(None, max_length=500)
    source_sha256: Digest | None = None
    parts: list[Part] = Field(default_factory=list, max_length=32)
    panels: list[Panel] = Field(default_factory=list, max_length=16)
    reconstructions: list[Reconstruction] = Field(default_factory=list, max_length=16)
    bones: list[Bone] = Field(default_factory=list, max_length=32)
    body_bone: Name | None = None
    repair_original: bool = Field(False, description="Also attempt to close pre-existing boundary loops.")
    seam_repairs: list[tuple[Name, Name]] = Field(default_factory=list, max_length=32,
        description="Repair local separation edges between pairs of retained pieces. / Repara los bordes de separación entre pares de piezas conservadas.")
    poses: list[Pose] = Field(default_factory=list, max_length=8)
    cameras: list[Camera] = Field(default_factory=list, max_length=8)
    focus: Vec | None = None
    view_scale: Positive | None = Field(None, description="Orthographic span, in metres.")
    view_size: int = Field(480, ge=256, le=1024)
    selection_views: bool = True
    game_triangle_budget: int | None = Field(None, ge=4, le=10_000_000, description="Additional skinned game GLB with a triangle limit. / GLB adicional de juego con límite de triángulos.")

    @model_validator(mode="after")
    def check_face_revision(self):
        if not self.source_sha256 and any(part.select.face_ranges for part in self.parts):
            raise ValueError("Face ranges require source_sha256. / Los tramos de caras requieren source_sha256.")
        return self


class ProjectionAxes(Args):
    origin: Vec = Field(description="Lower left of the projection in world metres. / Esquina inferior izquierda de la proyección en metros de escena.")
    right: Vec = Field(description="Full width vector towards image right. / Vector de anchura completa hacia la derecha de la imagen.")
    up: Vec = Field(description="Full height vector towards image top. / Vector de altura completa hacia arriba en la imagen.")

    @model_validator(mode="after")
    def check_axes(self):
        width, height = math.hypot(*self.right), math.hypot(*self.up)
        if min(width, height) < 1e-6 or abs(sum(a * b for a, b in zip(self.right, self.up))) > 1e-6 * width * height:
            raise ValueError("Use nonzero perpendicular projection axes. / Usa ejes de proyección perpendiculares y no nulos.")
        return self


class Projection(ProjectionAxes):
    part: str = Field(min_length=1, max_length=100, description="Name of the articulated mesh. / Nombre de la malla articulada.")
    path: str = Field(description="Absolute PNG or JPEG path. / Ruta absoluta a un PNG o JPEG.")
    sha256: Digest
    reference: str | None = Field(None, max_length=500)


class LoftSection(Args):
    v: Unit = Field(description="Height in the projection, from bottom to top. / Altura en la proyección, de abajo arriba.")
    u: Unit = Field(description="Horizontal centre in the projection. / Centro horizontal en la proyección.")
    depth: FiniteFloat = Field(description="Signed centre depth in metres. / Profundidad con signo del centro en metros.")
    radius_u: Positive = Field(description="Horizontal radius in projection units. / Radio horizontal en unidades de proyección.")
    radius_depth_m: Positive = Field(description="Depth radius in metres. / Radio en profundidad en metros.")


class SurfaceFinish(Args):
    depth: tuple[FiniteFloat, FiniteFloat] | None = Field(
        None, description="Optional narrower depth interval for geometry, in metres. / Intervalo de profundidad más estrecho para la geometría, en metros.")
    loft: list[LoftSection] | None = Field(
        None, min_length=2, max_length=32,
        description="Closed patch between elliptical sections on a part bound to one bone. / Parche cerrado entre secciones elípticas de una pieza ligada a un hueso.")
    max_hole_m: Positive | None = Field(
        None, description="Fill boundary rings up to this diameter in metres. / Rellena contornos abiertos hasta este diámetro en metros.")
    max_gap_m: Annotated[FiniteFloat, Field(ge=0)] = Field(
        0, description="Maximum new edge between nearby ends of an interrupted ring. / Longitud máxima de una arista nueva entre extremos de un contorno interrumpido.")
    backing_m: Positive | None = Field(
        None, description="Back an interrupted rim patch with a closed shell of this thickness. / Cierra el parche de un contorno interrumpido con este grosor de respaldo.")
    normal_blend_radius_m: Positive | None = Field(
        None, description="Blend shading normals on new repair faces with nearby original vertices, in metres. / Funde las normales de sombreado de las caras nuevas con los vértices originales próximos, en metros.")
    smooth_iterations: int = Field(
        0, ge=0, le=50, description="Passes of local surface smoothing. / Pasadas de alisado de la superficie local.")
    smooth_radius_m: Positive | None = Field(
        None, description="Fit nearby projected surface heights within this radius. / Ajusta la altura de la superficie proyectada dentro de este radio en metros.")
    max_displacement_m: Positive | None = Field(
        None, description="Maximum movement of existing vertices in metres. / Desplazamiento máximo de los vértices existentes en metros.")

    @model_validator(mode="after")
    def check_operation(self):
        if self.depth and self.depth[0] > self.depth[1]:
            raise ValueError("Geometry depth bounds are reversed. / Los límites de profundidad de la geometría están invertidos.")
        if self.loft and any(a.v >= b.v for a, b in zip(self.loft, self.loft[1:])):
            raise ValueError("Order sections by increasing height. / Ordena las secciones por altura ascendente.")
        if not self.max_hole_m and not self.smooth_iterations and not self.loft:
            raise ValueError("Choose hole filling, a closed patch or smoothing. / Elige rellenar huecos, un parche cerrado o alisar la superficie.")
        if self.max_gap_m and (not self.max_hole_m or self.max_gap_m >= self.max_hole_m):
            raise ValueError("A gap must be smaller than the hole diameter. / El tramo abierto debe ser menor que el diámetro del hueco.")
        if self.backing_m and (not self.max_gap_m or self.backing_m >= self.max_hole_m):
            raise ValueError("A backing needs gap repair and must be thinner than the hole diameter. / El respaldo requiere reparar tramos abiertos y debe ser más fino que el diámetro del hueco.")
        if self.normal_blend_radius_m and not (self.max_hole_m or self.loft):
            raise ValueError("Normal blending needs a surface patch. / El fundido de normales requiere un parche de superficie.")
        if self.smooth_radius_m and not self.smooth_iterations:
            raise ValueError("A smoothing radius needs smoothing passes. / El radio de alisado requiere pasadas de alisado.")
        if self.smooth_iterations and not self.max_displacement_m:
            raise ValueError("Set a maximum smoothing displacement. / Indica el desplazamiento máximo del alisado.")
        return self


def validate_region(depth, mask):
    if depth[0] > depth[1]:
        raise ValueError("Depth bounds are reversed. / Los límites de profundidad están invertidos.")
    if mask:
        edges = list(zip(mask, mask[1:] + mask[:1]))
        if len(set(mask)) != len(mask) or len(mask) < 3 or any(
            (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) <= 0
            for a, b in edges for p in mask if p not in (a, b)
        ):
            raise ValueError("Use a convex counterclockwise mask. / Usa una máscara convexa antihoraria.")


class ColourProjection(Projection):
    mode: Literal["colour"]
    depth: tuple[FiniteFloat, FiniteFloat] = Field(
        description="Signed depth interval along right cross up, in metres. / Intervalo de profundidad con signo desde el plano, según el producto vectorial de right por up, en metros.")
    min_facing: Annotated[FiniteFloat, Field(ge=-1, le=1)] = 0.2
    attribute: str | None = Field(None, max_length=100)
    crop: tuple[FaceIndex, FaceIndex, FaceIndex, FaceIndex] | None = Field(
        None, description="Image pixel box [left, top, right, bottom], exclusive right and bottom. / Recorte en píxeles [izquierda, arriba, derecha, abajo], con derecha y abajo excluidos.")
    mask: list[tuple[Unit, Unit]] = Field(default_factory=list, max_length=32,
        description="Convex counterclockwise polygon in projection UV (v points up). / Polígono convexo antihorario en UV de proyección (v hacia arriba).")
    feather: Unit = Field(0.08, description="Inward edge blend in normalized projection units. / Fundido hacia dentro en unidades normalizadas de proyección.")
    opacity: Annotated[FiniteFloat, Field(gt=0, le=1)] = 1.0
    surface: SurfaceFinish | None = Field(
        None, description="Repair and smooth this region before applying colour. / Repara y alisa esta zona antes de aplicar el color.")

    @model_validator(mode="after")
    def check_region(self):
        validate_region(self.depth, self.mask)
        if self.surface and self.surface.depth and not (
            self.depth[0] <= self.surface.depth[0] <= self.surface.depth[1] <= self.depth[1]
        ):
            raise ValueError("Geometry depth must be inside colour depth. / La profundidad de la geometría debe quedar dentro de la del color.")
        if self.crop and (self.crop[0] >= self.crop[2] or self.crop[1] >= self.crop[3]):
            raise ValueError("The crop must have a positive width and height. / El recorte debe tener anchura y altura positivas.")
        return self


class ImageProjection(Projection):
    mode: Literal["image"]


class ViewBox(ProjectionAxes):
    depth: tuple[FiniteFloat, FiniteFloat] = Field(description="Signed depth bounds along right × up. / Límites de profundidad con signo en la dirección right × up.")
    mask: list[tuple[Unit, Unit]] = Field(default_factory=list, max_length=32,
        description="Convex counterclockwise mask in view coordinates. / Máscara convexa antihoraria en coordenadas de la vista.")

    @model_validator(mode="after")
    def check_box(self):
        validate_region(self.depth, self.mask)
        return self


class LocalRegion(Args):
    part: str = Field(min_length=1, max_length=100)
    material: str = Field(min_length=1, max_length=100, description="Exact material name. / Nombre exacto del material.")
    view: ViewBox | None = None
    group: str | None = Field(None, min_length=1, max_length=100, description="Existing vertex group. / Grupo de vértices existente.")
    face_ranges: list[tuple[FaceIndex, FaceIndex]] = Field(default_factory=list, max_length=20000,
        description="Inclusive face ranges in the pinned editable scene. / Tramos inclusivos de caras en la escena editable fijada.")

    @model_validator(mode="after")
    def check_selection(self):
        if not (self.view or self.group or self.face_ranges):
            raise ValueError("Choose a view, vertex group or face ranges. / Elige una vista, un grupo de vértices o tramos de caras.")
        if any(first > last for first, last in self.face_ranges):
            raise ValueError("Face bounds are reversed. / Los límites de caras están invertidos.")
        return self


class LocalEdit(Args):
    region: LocalRegion


class LocalPaint(LocalEdit):
    mode: Literal["replace_colour"]
    path: str = Field(description="Absolute path to retained PNG or JPEG. / Ruta absoluta al PNG o JPEG conservado.")
    sha256: Digest
    reference: str | None = Field(None, max_length=500)
    crop: tuple[FaceIndex, FaceIndex, FaceIndex, FaceIndex] | None = None
    attribute: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def check_region(self):
        if not self.region.view:
            raise ValueError("A colour replacement needs a view. / La sustitución de color requiere una vista.")
        if self.crop and (self.crop[0] >= self.crop[2] or self.crop[1] >= self.crop[3]):
            raise ValueError("Use a positive image crop. / Usa un recorte de imagen con dimensiones positivas.")
        return self


class Despeckle(LocalEdit):
    mode: Literal["despeckle"]
    attribute: str | None = Field(None, max_length=100)
    radius_m: Positive = Field(description="Neighbour radius in metres. / Radio de la vecindad en metros.")
    threshold: Annotated[FiniteFloat, Field(gt=0, le=1)] = Field(description="Minimum RGB deviation in linear colour. / Desviación RGB mínima en color lineal.")
    max_spot_m: Positive = Field(description="Maximum spot diameter in metres. / Diámetro máximo de una mota en metros.")


class LocalSmooth(LocalEdit):
    mode: Literal["smooth"]
    iterations: int = Field(8, ge=1, le=50)
    max_displacement_m: Positive
    radius_m: Positive | None = Field(None, description="Fit depths across disconnected triangles in a view, in metres. / Ajusta la profundidad entre triángulos desconectados en la vista, en metros.")

    @model_validator(mode="after")
    def check_depth_fit(self):
        if self.radius_m and not self.region.view:
            raise ValueError("Depth fitting needs a view. / El ajuste de profundidad requiere una vista.")
        return self


class ReplaceMesh(LocalEdit):
    mode: Literal["replace_mesh"]
    path: str = Field(description="Absolute path to one unrigged GLB mesh in the part's local rest coordinates. / Ruta absoluta a un GLB sin rig en coordenadas locales de reposo de la pieza.")
    sha256: Digest
    reference: str | None = Field(None, max_length=500)
    name: Name


class Finish(Args):
    source: str = Field(description="Absolute editable articulation .blend path. / Ruta absoluta al .blend editable de articulación.")
    source_sha256: Digest
    source_ref: str | None = Field(None, max_length=500)
    frame: int | None = Field(None, description="Projection frame; defaults to the saved frame. / Fotograma de proyección. Por defecto usa el guardado.")
    edits: list[Annotated[ColourProjection | ImageProjection | LocalPaint | Despeckle | LocalSmooth | ReplaceMesh,
                          Field(discriminator="mode")]] = Field(min_length=1, max_length=16)
    cameras: list[Camera] = Field(default_factory=list, max_length=8)
    view_size: int = Field(384, ge=256, le=1024)

    @model_validator(mode="after")
    def check_views(self):
        if any(isinstance(edit, LocalEdit) for edit in self.edits) and not self.cameras:
            raise ValueError("Provide comparison cameras. / Indica las cámaras para la comparación.")
        if len({camera.name for camera in self.cameras}) != len(self.cameras):
            raise ValueError("Use distinct camera names. / Usa nombres de cámara distintos.")
        return self


class Lookup(Args):
    id: PackageId


class Picture(Lookup):
    file: View


class Undo(Lookup):
    restore: bool = False


class SourcePick(Args):
    source: str
    source_sha256: Digest
    origin: Vec
    direction: Vec
    point: Vec | None = Field(None, description="Visible impact from the viewer. / Punto de impacto visible en el visor.")

    @model_validator(mode="after")
    def check_direction(self):
        length = math.hypot(*self.direction)
        if not length:
            raise ValueError("The ray direction cannot be zero. / La dirección del rayo no puede ser cero.")
        if self.point is not None:
            heading = [value / length for value in self.direction]
            offset = [point - origin for point, origin in zip(self.point, self.origin)]
            along = sum(a * b for a, b in zip(heading, offset))
            if along < 0 or math.hypot(*(b - a * along for a, b in zip(heading, offset))) > 0.00025:
                raise ValueError("The point is outside the view ray. / El punto queda fuera del rayo del visor.")
        return self


def build(services, args):
    return service.build(services.config.data_dir, args.model_dump(mode="json"))


def finish(services, args):
    return service.finish(services.config.data_dir, args.model_dump(mode="json"))


def get(services, args):
    return service.get(services.config.data_dir, args.id)


def view(services, args):
    return service.view(services.config.data_dir, args.id, args.file)


def undo(services, args):
    return service.undo(services.config.data_dir, args.id, args.restore)


def pick(services, args):
    return service.pick(args.model_dump(mode="json"))


TOOLS = [
    Tool("articulation_build", "Split and rig an inferred mesh. Selección, corte, reparación y articulación.\n"
         "Sinónimos: separar y articular una malla, preparar piezas móviles, cortar y reparar encastres.\n"
         "Select by region, plane, colour or seed; cap boundaries, "
         "repair named seams, discard debris and rig separate pieces. Creates a new editable package with source face provenance, GLB, "
         "frozen poses and optional same-camera previews. Build a clipped panel from a pinned dossier. Use articulation_undo to revert. "
         "Selecciona por región, plano, color, semilla o caras de origen. Repara encastres, articula piezas y construye paneles desde un expediente fijado.",
         Build, ann(draft_safe=True), build, timeout_s=600, capped=False),
    Tool("articulation_finish", "Project retained artwork onto a rig. Proyecta ilustraciones de referencia sobre una articulación.\n"
         "Paint local colour, optionally closing holes, adding a bounded patch and smoothing, or map an image to a part. Keeps the rig and animation. "
         "Add material-scoped colour replacement, speck cleaning, smoothing or a skinned mesh swap with before/after views. Undo: articulation_undo. "
         "Reproyecta color localizado, con relleno, parche acotado y alisado opcionales, o aplica una imagen a una pieza. Conserva esqueleto y animación. "
         "Sustituye color o malla, limpia motas o alisa por material, con vistas antes y después. Deshacer: articulation_undo.\n"
         "Sinónimos: reparar huecos y escalones, reproyectar el color de la cara, aplicar tinta al mapa, texturizar una pieza articulada.",
         Finish, ann(draft_safe=True), finish, timeout_s=600, capped=False),
    Tool("articulation_get", "Read an articulation package. Consulta de articulación.\nReturns its revision, measurements and files. "
         "Consulta la procedencia, las medidas y los archivos de la articulación.\n"
         "Sinónimos: consultar una malla articulada, ver la revisión del rig, recuperar piezas y medidas.", Lookup, ann(True), get, capped=False),
    Tool("articulation_view", "Open a source, selection or pose preview. Vista de articulación.\nUse a PNG name from articulation_get. "
         "Abre una vista de origen, selección o pose.\n"
         "Sinónimos: previsualizar la pose, ver el recorte, comparar con la malla original.", Picture, ann(True), view, capped=False),
    Tool("articulation_pick", "Resolve a viewer ray to a source face. Resuelve un rayo del visor a una cara de origen.\n"
         "Sinónimos: localizar una cara de origen, identificar el triángulo señalado, seleccionar desde el visor.",
         SourcePick, ann(True), pick, timeout_s=90),
    Tool("articulation_undo", "Withdraw or restore a package. Deshacer articulación.\nReturns arguments to reverse the operation. "
         "Retira o restaura un paquete generado.\n"
         "Sinónimos: deshacer la articulación, retirar el paquete articulado, restaurar el rig.", Undo, ann(idempotent=True), undo),
]
