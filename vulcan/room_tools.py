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
         'Without vision, trace an outline and pass a RoomPlan directly to room_build. Keywords: boceto, planta, habitación.',
         room.SketchArgs, ann(False, False, False), room.interpret, timeout_s=300, capped=False),
    Tool('room_build', 'Build a textured room from a reviewed plan. Keywords: habitación 3D, materiales.\n'
         'Builds in metres as a GLB library model. '
         'Includes concave floor, wall volumes, real door/window openings and furniture boxes; five procedural materials '
         'with planar UV projection. Retains editable plan, runnable code and optional sketch. Creates a new model each time. '
         'The existing texture_project tool can paint a further variant. Keywords: habitación 3D, materiales, boceto a 3D.',
         room.BuildArgs, ann(False, False, False), room.build, timeout_s=300, capped=False),
    Tool('room_get', 'Read a room recipe and measurements. Keywords: planta, receta habitación.\n'
         'Returns the saved plan, assumptions, material choices, dimensions and scoped download URLs without changing it.',
         RoomId, ann(True), lambda services, args: {'ok': True, **room.info(services, args.id)}, capped=False),
]
