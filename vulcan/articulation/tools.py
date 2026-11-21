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


class Projection(Args):
    part: str = Field(min_length=1, max_length=100, description="Name of the articulated mesh. / Nombre de la malla articulada.")
    path: str = Field(description="Absolute PNG or JPEG path. / Ruta absoluta a un PNG o JPEG.")
    sha256: Digest
    reference: str | None = Field(None, max_length=500)
    origin: Vec = Field(description="Lower left of the projection in world metres. / Esquina inferior izquierda de la proyección en metros de escena.")
    right: Vec = Field(description="Full width vector towards image right. / Vector de anchura completa hacia la derecha de la imagen.")
    up: Vec = Field(description="Full height vector towards image top. / Vector de altura completa hacia arriba en la imagen.")

    @model_validator(mode="after")
    def check_axes(self):
        width, height = math.hypot(*self.right), math.hypot(*self.up)
        if min(width, height) < 1e-6 or abs(sum(a * b for a, b in zip(self.right, self.up))) > 1e-6 * width * height:
            raise ValueError("Use nonzero perpendicular projection axes. / Usa ejes de proyección perpendiculares y no nulos.")
        return self


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

    @model_validator(mode="after")
    def check_region(self):
        if self.depth[0] > self.depth[1]:
            raise ValueError("Depth bounds are reversed. / Los límites de profundidad están invertidos.")
        if self.crop and (self.crop[0] >= self.crop[2] or self.crop[1] >= self.crop[3]):
            raise ValueError("The crop must have a positive width and height. / El recorte debe tener anchura y altura positivas.")
        if self.mask:
            edges = list(zip(self.mask, self.mask[1:] + self.mask[:1]))
            if len(set(self.mask)) != len(self.mask) or len(self.mask) < 3 or any(
                (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) <= 0
                for a, b in edges for p in self.mask if p not in (a, b)
            ):
                raise ValueError("Use a convex counterclockwise mask. / Usa una máscara convexa antihoraria.")
        return self


class ImageProjection(Projection):
    mode: Literal["image"]


class Finish(Args):
    source: str = Field(description="Absolute editable articulation .blend path. / Ruta absoluta al .blend editable de articulación.")
    source_sha256: Digest
    source_ref: str | None = Field(None, max_length=500)
    frame: int | None = Field(None, description="Projection frame; defaults to the saved frame. / Fotograma de proyección. Por defecto usa el guardado.")
    edits: list[Annotated[ColourProjection | ImageProjection, Field(discriminator="mode")]] = Field(min_length=1, max_length=16)


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
         "Paint a localized vertex-colour patch or map an image to a named part. Keeps geometry, rig and animation. "
         "Returns an editable scene and skinned GLB. Undo: articulation_undo. "
         "Reproyecta color localizado o aplica una imagen a una pieza nombrada. Conserva geometría, esqueleto y animación. "
         "Devuelve escena editable y GLB articulado. Deshacer: articulation_undo.\n"
         "Sinónimos: reproyectar el color de la cara, aplicar tinta al mapa, texturizar una pieza articulada.",
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
