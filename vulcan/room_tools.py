"""Native room tools shared by API, studio and MCP."""
from pydantic import BaseModel, Field
from . import room_sketch as room
from .hoard_link.agentkit import Tool, ann


class RoomId(BaseModel):
    id: int = Field(ge=1)


ROOM_TOOLS = [
    Tool('room_plan', 'Interpret a room sketch as a measured plan. Keywords: boceto, planta, habitación.\n'
         'Uses the configured global vision model. '
         'Returns a measured editable plan, volumes, openings, materials and explicit assumptions. '
         'width_m calibrates the horizontal span. Review the plan before room_build. '
         'Without vision, trace an outline and pass a RoomPlan directly to room_build. Keywords: boceto, planta, habitación.\n'
         'Sinónimos: pasar un croquis a planta, interpretar un boceto, sacar las medidas de una habitación.',
         room.SketchArgs, ann(False, False, False), room.interpret, timeout_s=300, capped=False),
    Tool('room_build', 'Build a textured room from a reviewed plan. Keywords: habitación 3D, materiales.\n'
         'Sinónimos: levantar una habitación en 3D, construir una estancia desde la planta, amueblar un plano.\n'
         'Builds in metres as a GLB library model. '
         'Includes concave floors, connected rooms with partitions and doorways, ceilings, framed doors/windows with glass, '
         'and parametric bed/table/chair/sofa/shelf/wardrobe furniture or free boxes; selectable interior/exterior materials '
         'with planar UV projection. Retains editable plan, runnable code and optional sketch. Creates a new model each time. '
         'The existing texture_project tool can paint a further variant. Keywords: habitación 3D, materiales, boceto a 3D.',
         room.BuildArgs, ann(False, False, False), room.build, timeout_s=300, capped=False),
    Tool('room_get', 'Read a room recipe and measurements. Keywords: planta, receta habitación.\n'
         'Returns the saved plan, assumptions, material choices, dimensions and scoped download URLs without changing it.\n'
         'Sinónimos: consultar la planta guardada, ver las cotas de la habitación, recuperar la receta de la estancia.',
         RoomId, ann(True), lambda services, args: {'ok': True, **room.info(services, args.id)}, capped=False),
    Tool('room_rectify', 'Rectify perspective in a photographed sketch before tracing. Keywords: perspectiva, boceto, foto.\n'
         'Provide image_path or image_base64, four normalized corners in top-left/top-right/bottom-right/bottom-left order, '
         'and the true paper aspect (width/height). Returns a rectified PNG data URL, width and height; no model required.\n'
         'Sinónimos: enderezar la foto de un croquis, corregir la perspectiva del dibujo, rectificar el plano fotografiado.',
         room.RectifyArgs, ann(True), lambda services, args: room.rectify(args), capped=False),
]
