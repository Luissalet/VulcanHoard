"""Declarative assembly specification. Values are numbers or arithmetic over named variables; nothing is evaluated as code."""
from __future__ import annotations

import ast
import math
import re
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAME = r'^[a-z][a-z0-9_]{0,40}$'
REF = re.compile(r'^([a-z][a-z0-9_]{0,40})\.([a-z][a-z0-9_.]{0,60})$')
UNITS = {'mm': 1.0, 'in': 25.4}
Value = Union[float, str]


def evaluate(value, variables):
    """Evaluate a number or an arithmetic expression over variables (+ - * / parentheses, unary minus)."""
    if isinstance(value, bool):
        raise ValueError('Booleans are not dimensions')
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError('Values must be finite')
        return float(value)
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError('Expected a number or a short arithmetic expression')
    try:
        tree = ast.parse(value, mode='eval')
    except SyntaxError as exc:
        raise ValueError(f'Invalid expression: {value}') from exc

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f'Unknown variable {node.id!r} in {value!r}')
            return float(variables[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            inner = walk(node.operand)
            return -inner if isinstance(node.op, ast.USub) else inner
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise ValueError(f'Division by zero in {value!r}')
            return left / right
        raise ValueError(f'Only numbers, variables and + - * / are allowed: {value!r}')

    result = walk(tree)
    if not math.isfinite(result):
        raise ValueError(f'Expression {value!r} is not finite')
    return result


class Pose(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    translation: tuple[float, float, float] = (0, 0, 0)
    rotation_deg: tuple[float, float, float] = Field((0, 0, 0), description='Extrinsic X, then Y, then Z rotation in degrees')


class Thread(BaseModel):
    """Declared thread metadata of a screw. The geometry stays a nominal cylinder; this only feeds the clearance checks."""
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    designation: str = Field(pattern=r'^M[0-9]{1,2}(\.[0-9])?$', description='ISO metric designation, for example M3 or M4')
    pitch: float | None = Field(None, gt=0, le=3, description='Pitch in mm; the coarse pitch of the designation when omitted')


class FamilySource(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['family'] = 'family'
    family: str = Field(pattern=r'^[a-z_]{1,30}$')
    params: dict[str, Value] = Field(default_factory=dict)
    thread: Thread | None = Field(None, description='screw family only: declared metric thread used to check clearance holes (the solid stays a nominal cylinder)')


class BuildSource(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['build']
    build_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$', description='A finished Heron CAD build; its STEP is read and never modified')


class MeshSource(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['mesh']
    vertices: list[tuple[float, float, float]] = Field(min_length=4, max_length=20000)
    faces: list[tuple[int, int, int]] = Field(min_length=4, max_length=40000)


class Component(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=NAME)
    name: str = Field('', max_length=120)
    units: Literal['mm', 'in'] = 'mm'
    source: Union[FamilySource, BuildSource, MeshSource] = Field(discriminator='kind')
    pose: Pose = Field(default_factory=Pose, description='Initial pose (fixed pose for a fixed component)')


class Relation(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    id: str = Field(pattern=NAME)
    type: Literal['fixed', 'coincident', 'distance', 'coaxial', 'clearance', 'angle', 'point_on_plane', 'point_on_axis', 'point_coincident']
    component: str | None = Field(None, description='fixed: the component held at its pose')
    a: str | None = Field(None, description='component.reference (planes for coincident/distance, axes for coaxial, planes or axes for angle, a point for point_on_plane/point_on_axis/point_coincident); a component id for clearance')
    b: str | None = Field(None, description='component.reference (a plane for point_on_plane, an axis for point_on_axis, a point for point_coincident)')
    value: Value | None = Field(None, description='distance: signed offset of plane b from plane a along the normal of a (mm); angle: angle in degrees (0 to 180) between the directions of a and b (plane normals / axis directions as listed); point_on_plane: optional signed offset of the point along the plane normal (mm)')
    min: Value | None = Field(None, description='clearance: requested minimum gap between the two solids (mm)')
    orientation: Literal['opposed', 'aligned', 'either'] = Field('opposed', description="coincident/distance default 'opposed' (faces mate); coaxial uses 'aligned' or 'opposed'; 'either' is solved for both and never chosen silently")

    @model_validator(mode='after')
    def shape(self):
        if self.type == 'fixed':
            if not self.component or self.a or self.b:
                raise ValueError(f'{self.id}: fixed takes only component')
        elif self.type == 'clearance':
            if not (self.a and self.b and self.min is not None) or '.' in self.a or '.' in self.b:
                raise ValueError(f'{self.id}: clearance takes two component ids a, b and min')
        else:
            if not (self.a and self.b) or not REF.match(self.a) or not REF.match(self.b):
                raise ValueError(f'{self.id}: {self.type} takes references a and b written component.reference')
            if self.a.split('.')[0] == self.b.split('.')[0]:
                raise ValueError(f'{self.id}: a relation joins two different components')
            if self.type in ('distance', 'angle') and self.value is None:
                raise ValueError(f'{self.id}: {self.type} needs value')
            if self.type in ('angle', 'point_on_plane', 'point_on_axis', 'point_coincident') and 'orientation' in self.model_fields_set and self.orientation != 'opposed':
                raise ValueError(f'{self.id}: orientation does not apply to {self.type}')
        return self


class Checks(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    contact_tolerance: float = Field(0.01, gt=0, le=1, description='Gap at or below which two solids are in contact (mm)')
    volume_tolerance: float = Field(0.001, gt=0, le=10, description='Common volume at or below which there is no penetration (mm3)')
    mesh_tolerance: float = Field(0.02, gt=0.001, le=1, description='Tessellation tolerance for the mesh approximation (mm)')


class Assembly(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    title: str = Field('Assembly', min_length=1, max_length=120)
    units: Literal['mm'] = 'mm'
    variables: dict[str, float] = Field(default_factory=dict)
    components: list[Component] = Field(min_length=1, max_length=40)
    relations: list[Relation] = Field(default_factory=list, max_length=200)
    checks: Checks = Field(default_factory=Checks)

    @model_validator(mode='after')
    def consistent(self):
        ids = [c.id for c in self.components]
        if len(set(ids)) != len(ids):
            raise ValueError('Component ids must be unique')
        rids = [r.id for r in self.relations]
        if len(set(rids)) != len(rids):
            raise ValueError('Relation ids must be unique')
        for name in self.variables:
            if not re.match(NAME, name):
                raise ValueError(f'Invalid variable name {name!r}')
        for relation in self.relations:
            names = [relation.component] if relation.type == 'fixed' else [relation.a.split('.')[0], relation.b.split('.')[0]]
            for name in names:
                if name not in ids:
                    raise ValueError(f'{relation.id}: unknown component {name!r}')
        if not any(r.type == 'fixed' for r in self.relations):
            raise ValueError('An assembly needs at least one fixed component')
        if sum(r.orientation == 'either' for r in self.relations) > 6:
            raise ValueError("At most six relations may use orientation 'either'")
        return self
