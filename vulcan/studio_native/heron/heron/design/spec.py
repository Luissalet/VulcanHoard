"""Declarative part specification: a constrained sketch, an extrusion and operations on semantically named faces.

Values are numbers or arithmetic over named parameters (+ - * / and parentheses); nothing is evaluated as code.
"""
from __future__ import annotations

import re
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..assembly.spec import evaluate

NAME = r'^[a-z][a-z0-9_]{0,40}$'
Value = Union[float, str]

# constraint type -> (required fields, optional fields, value meaning)
CONSTRAINTS = {
    'fixed': (('point',), ('at',), 'Holds a point at "at" [x, y] (mm, expressions allowed) or at its given position.'),
    'coincident': (('a', 'b'), (), 'Points a and b coincide.'),
    'horizontal': (('line',), (), 'The line is parallel to the sketch x axis.'),
    'vertical': (('line',), (), 'The line is parallel to the sketch y axis.'),
    'parallel': (('a', 'b'), (), 'Lines a and b are parallel.'),
    'perpendicular': (('a', 'b'), (), 'Lines a and b are perpendicular.'),
    'angle': (('a', 'b', 'value'), (), 'Signed angle in degrees measured counter-clockwise from line a to line b.'),
    'tangent': (('a', 'b'), ('mode',), 'Line a tangent to circle b, or circles a and b tangent (mode external or internal).'),
    'equal': (('a', 'b'), (), 'Two lines of equal length or two circles of equal radius.'),
    'symmetric': (('a', 'b', 'axis'), (), 'Points a and b are mirror images about the line axis.'),
    'midpoint': (('point', 'line'), (), 'The point is the midpoint of the line.'),
    'point_on_line': (('point', 'line'), (), 'The point lies on the infinite line through the line.'),
    'point_on_circle': (('point', 'circle'), (), 'The point lies on the circle.'),
    'concentric': (('a', 'b'), (), 'Circles a and b share their centre.'),
    'distance': (('a', 'b', 'value'), (), 'Distance (mm) between points a and b, or from point a to line b.'),
    'distance_x': (('a', 'b', 'value'), (), 'Signed horizontal distance x(b) - x(a) in mm.'),
    'distance_y': (('a', 'b', 'value'), (), 'Signed vertical distance y(b) - y(a) in mm.'),
    'length': (('line', 'value'), (), 'Length of the line in mm.'),
    'radius': (('circle', 'value'), (), 'Radius of the circle in mm.'),
    'diameter': (('circle', 'value'), (), 'Diameter of the circle in mm.'),
}


class Constraint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=NAME)
    type: Literal[tuple(CONSTRAINTS)]  # type: ignore[valid-type]
    point: str | None = None
    line: str | None = None
    circle: str | None = None
    a: str | None = None
    b: str | None = None
    axis: str | None = None
    at: tuple[Value, Value] | None = None
    value: Value | None = None
    mode: Literal['external', 'internal'] | None = None

    @model_validator(mode='after')
    def fields(self):
        required, optional, _ = CONSTRAINTS[self.type]
        given = {k for k in ('point', 'line', 'circle', 'a', 'b', 'axis', 'at', 'value', 'mode') if getattr(self, k) is not None}
        missing = [k for k in required if k not in given]
        extra = sorted(given - set(required) - set(optional))
        if missing:
            raise ValueError(f'{self.id}: {self.type} needs {", ".join(missing)}')
        if extra:
            raise ValueError(f'{self.id}: {self.type} does not take {", ".join(extra)}')
        return self


class Circle(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    center: str
    radius: Value = Field(description='Starting radius (mm); dimension it with a radius/diameter constraint')


class Sketch(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    points: dict[str, tuple[Value, Value]] = Field(description='Point id -> starting position [x, y] in mm; the solver moves free points')
    lines: dict[str, tuple[str, str]] = Field(default_factory=dict, description='Line id -> [start point, end point]')
    circles: dict[str, Circle] = Field(default_factory=dict)
    profile: list[str] = Field(min_length=3, max_length=200, description='Line ids going once around the closed outline, end to start')
    holes: list[Union[str, list[str]]] = Field(default_factory=list, max_length=50, description='Inner loops cut through the extrusion: a circle id or a closed list of line ids')
    constraints: list[Constraint] = Field(default_factory=list, max_length=400)

    @model_validator(mode='after')
    def references(self):
        for group in (self.points, self.lines, self.circles):
            for name in group:
                if not re.match(NAME, name):
                    raise ValueError(f'Invalid sketch id {name!r}')
        names = list(self.points) + list(self.lines) + list(self.circles)
        if len(set(names)) != len(names):
            raise ValueError('Point, line and circle ids must be distinct')
        if len(self.points) > 300 or len(self.lines) > 300 or len(self.circles) > 60:
            raise ValueError('At most 300 points, 300 lines and 60 circles')
        for lid, (p, q) in self.lines.items():
            if p not in self.points or q not in self.points or p == q:
                raise ValueError(f'Line {lid}: needs two different known points')
        for cid, circle in self.circles.items():
            if circle.center not in self.points:
                raise ValueError(f'Circle {cid}: unknown centre point {circle.center!r}')
        ids = [c.id for c in self.constraints]
        if len(set(ids)) != len(ids):
            raise ValueError('Constraint ids must be unique')
        for loop in [self.profile] + [h for h in self.holes if isinstance(h, list)]:
            chain(self, loop)
        for hole in self.holes:
            if isinstance(hole, str) and hole not in self.circles:
                raise ValueError(f'Hole {hole!r} is not a circle id')
        for c in self.constraints:
            kinds = entity_kinds(self, c)
            for field, (name, kind) in kinds.items():
                if name is None:
                    continue
                if kind == 'line' and name not in self.lines or kind == 'circle' and name not in self.circles or kind == 'point' and name not in self.points:
                    raise ValueError(f'{c.id}: {field} must be a {kind} id, got {name!r}')
        return self


def chain(sketch, loop):
    """Ordered point ids of a closed loop of lines; each line must start where the previous one ended."""
    if len(loop) < 3 or len(set(loop)) != len(loop):
        raise ValueError('A loop needs at least three distinct lines')
    for lid in loop:
        if lid not in sketch.lines:
            raise ValueError(f'Loop line {lid!r} is not a line id')
    points = []
    for i, lid in enumerate(loop):
        start, end = sketch.lines[lid]
        nxt = sketch.lines[loop[(i + 1) % len(loop)]]
        if end != nxt[0]:
            raise ValueError(f'Loop is not closed: line {lid} ends at {end} but {loop[(i + 1) % len(loop)]} starts at {nxt[0]}')
        points.append(start)
    return points


def entity_kinds(sketch, c):
    """field -> (id, kind) for a constraint; a/b kinds depend on the type."""
    t = c.type
    out = {'point': (c.point, 'point'), 'line': (c.line, 'line'), 'circle': (c.circle, 'circle'), 'axis': (c.axis, 'line')}
    if t in ('coincident', 'symmetric', 'distance_x', 'distance_y'):
        out.update(a=(c.a, 'point'), b=(c.b, 'point'))
    elif t in ('parallel', 'perpendicular', 'angle'):
        out.update(a=(c.a, 'line'), b=(c.b, 'line'))
    elif t == 'concentric':
        out.update(a=(c.a, 'circle'), b=(c.b, 'circle'))
    elif t == 'tangent':
        out.update(a=(c.a, 'line' if c.a in sketch.lines else 'circle'), b=(c.b, 'circle'))
    elif t == 'equal':
        kind = 'line' if c.a in sketch.lines else 'circle'
        out.update(a=(c.a, kind), b=(c.b, kind))
    elif t == 'distance':
        out.update(a=(c.a, 'point'), b=(c.b, 'line' if c.b in sketch.lines else 'point'))
    return out


class FaceRef(BaseModel):
    """A face named by meaning, resolved again on every regeneration (never by an internal face index)."""
    model_config = ConfigDict(extra='forbid')
    cap: Literal['top', 'bottom'] | None = Field(None, description='The end face of the extrusion at z = depth (top) or z = 0 (bottom)')
    side: str | None = Field(None, description='The side face swept by this sketch line')
    contains: str | None = Field(None, description='The single face containing this sketch point lifted to level')
    level: Literal['top', 'bottom', 'mid'] = Field('mid', description='Height of the contains point: z = depth, 0 or depth / 2')

    @model_validator(mode='before')
    @classmethod
    def shorthand(cls, value):
        return {'cap': value} if isinstance(value, str) else value

    @model_validator(mode='after')
    def one(self):
        if sum(v is not None for v in (self.cap, self.side, self.contains)) != 1:
            raise ValueError('A face reference is "top", "bottom", {side: line} or {contains: point, level}')
        if self.level != 'mid' and self.contains is None:
            raise ValueError('level only applies to a contains reference')
        return self

    def label(self):
        return self.cap or (f'side:{self.side}' if self.side else f'contains:{self.contains}@{self.level}')


class Feature(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=NAME)
    type: Literal['hole', 'fillet', 'chamfer', 'shell']
    face: FaceRef
    at: list[str] = Field(default_factory=list, max_length=60, description='hole: sketch point ids giving the centres (projected onto the face)')
    diameter: Value | None = Field(None, description='hole diameter (mm)')
    depth: Value | None = Field(None, description='hole depth (mm); omitted = through all')
    z: Value | None = Field(None, description='hole on a side face: height of the centres above the sketch plane (mm); default depth / 2')
    radius: Value | None = Field(None, description='fillet radius (mm) of the edges of the face')
    distance: Value | None = Field(None, description='chamfer distance (mm) of the edges of the face')
    thickness: Value | None = Field(None, description='shell wall thickness (mm); the face is removed and the solid hollowed inwards')
    edges: Literal['outer', 'all'] = Field('outer', description='fillet/chamfer: the outer boundary of the face or all its edges (holes too)')

    @model_validator(mode='after')
    def needs(self):
        need = {'hole': ('diameter',), 'fillet': ('radius',), 'chamfer': ('distance',), 'shell': ('thickness',)}[self.type]
        for key in need:
            if getattr(self, key) is None:
                raise ValueError(f'{self.id}: {self.type} needs {key}')
        if self.type == 'hole' and not self.at:
            raise ValueError(f'{self.id}: a hole needs at least one centre point in at')
        if self.type != 'hole' and (self.at or self.depth is not None or self.z is not None):
            raise ValueError(f'{self.id}: at/depth/z only apply to holes')
        return self


class Check(BaseModel):
    """An expected dimension measured independently on the finished B-rep (not read back from the sketch)."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=NAME)
    measure: Literal['hole_distance', 'hole_diameter', 'bbox_x', 'bbox_y', 'bbox_z', 'volume']
    feature: str | None = Field(None, description='hole_distance/hole_diameter: the hole feature (distance between its first two holes)')
    value: Value
    tolerance: float = Field(0.01, gt=0, le=1000, description='Allowed absolute difference (mm, or mm3 for volume)')


class Extrude(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    depth: Value = Field(description='Extrusion height along +z (mm)')


class Part(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field('Part', min_length=1, max_length=120)
    units: Literal['mm'] = 'mm'
    parameters: dict[str, Value] = Field(default_factory=dict, description='Named dimensions; a value may be an expression of earlier ones')
    sketch: Sketch
    extrude: Extrude
    features: list[Feature] = Field(default_factory=list, max_length=40)
    checks: list[Check] = Field(default_factory=list, max_length=40)
    tolerance: float = Field(.02, ge=.005, le=1, description='STL tessellation tolerance (mm)')

    @model_validator(mode='after')
    def consistent(self):
        if len(self.parameters) > 80:
            raise ValueError('At most 80 parameters')
        for name in self.parameters:
            if not re.match(NAME, name):
                raise ValueError(f'Invalid parameter name {name!r}')
        resolve(self.parameters)
        ids = [f.id for f in self.features]
        if len(set(ids)) != len(ids):
            raise ValueError('Feature ids must be unique')
        for f in self.features:
            for p in f.at:
                if p not in self.sketch.points:
                    raise ValueError(f'{f.id}: unknown sketch point {p!r}')
            if f.face.side and f.face.side not in self.sketch.lines:
                raise ValueError(f'{f.id}: unknown sketch line {f.face.side!r}')
            if f.face.contains and f.face.contains not in self.sketch.points:
                raise ValueError(f'{f.id}: unknown sketch point {f.face.contains!r}')
        for c in self.checks:
            if c.measure.startswith('hole_'):
                feature = next((f for f in self.features if f.id == c.feature), None)
                if feature is None or feature.type != 'hole':
                    raise ValueError(f'{c.id}: {c.measure} needs a hole feature id')
                if c.measure == 'hole_distance' and len(feature.at) < 2:
                    raise ValueError(f'{c.id}: hole_distance needs a hole feature with two centres')
        return self


def resolve(parameters):
    """Parameter values in declaration order; each may use the ones declared before it."""
    values = {}
    for name, raw in parameters.items():
        values[name] = evaluate(raw, values)
    return values


def value(raw, values, what):
    try:
        return evaluate(raw, values)
    except ValueError as exc:
        raise ValueError(f'{what}: {exc}') from exc
