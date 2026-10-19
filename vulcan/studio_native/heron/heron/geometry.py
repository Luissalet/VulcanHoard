"""Declarative recipes; no evaluated Python, expressions, paths or shell commands."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Primitive(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['box','cylinder','sphere'] = 'box'
    operation: Literal['union','cut','intersect'] = 'union'
    size: tuple[float,float,float] = (40,30,5)
    radius: float = Field(5,gt=0,le=1000)
    height: float = Field(10,gt=0,le=2000)
    position: tuple[float,float,float] = (0,0,0)
    rotation: tuple[float,float,float] = (0,0,0)

    @model_validator(mode='after')
    def bounds(self):
        if any(not math.isfinite(v) or v<=0 or v>2000 for v in self.size):
            raise ValueError('Sizes must be finite millimetres in (0,2000]')
        if any(not math.isfinite(v) or abs(v)>10000 for v in self.position):
            raise ValueError('Position must be finite and within 10000 mm')
        if any(not math.isfinite(v) or abs(v)>360 for v in self.rotation):
            raise ValueError('Rotation must be finite degrees in [-360,360]')
        return self


class Recipe(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    title: str = Field('Measured part',min_length=1,max_length=120)
    units: Literal['mm'] = 'mm'
    parts: list[Primitive] = Field(min_length=1,max_length=50)
    fillet: float = Field(0,ge=0,le=100)
    tolerance: float = Field(.05,ge=.01,le=1)
    angular_tolerance: float = Field(.1,ge=.01,le=.5)

    @model_validator(mode='after')
    def first_solid(self):
        if self.parts[0].operation!='union':
            raise ValueError('First primitive must create a solid')
        return self


def templates():
    return {
      'plate':{'title':'Placa perforada / Drilled plate','parts':[{'size':[80,50,5]},*[{'kind':'cylinder','operation':'cut','radius':3,'height':10,'position':[x,y,0]} for x in (-30,30) for y in (-15,15)]]},
      'spacer':{'title':'Separador / Spacer','parts':[{'kind':'cylinder','radius':12,'height':20},{'kind':'cylinder','operation':'cut','radius':4,'height':25}]},
      'bracket':{'title':'Soporte en L / L bracket','parts':[{'size':[60,40,5]},{'size':[5,40,40],'position':[-27.5,0,17.5]},{'kind':'cylinder','operation':'cut','radius':4,'height':12,'position':[15,0,0]}]},
    }


def build(recipe: Recipe):
    import cadquery as cq
    result=None
    for part in recipe.parts:
        work=cq.Workplane('XY')
        if part.kind=='box': shape=work.box(*part.size)
        elif part.kind=='cylinder': shape=work.cylinder(part.height,part.radius)
        else: shape=work.sphere(part.radius)
        for angle,axis in zip(part.rotation,((1,0,0),(0,1,0),(0,0,1))):
            if angle:shape=shape.rotate((0,0,0),axis,angle)
        shape=shape.translate(part.position)
        if result is None: result=shape
        elif part.operation=='union': result=result.union(shape)
        elif part.operation=='cut': result=result.cut(shape)
        else: result=result.intersect(shape)
    if recipe.fillet: result=result.edges().fillet(recipe.fillet)
    solids=result.solids().vals()
    if not solids or any(not s.isValid() or s.Volume()<=0 for s in solids):
        raise ValueError('CAD kernel rejected empty or invalid geometry')
    return result


def export(recipe: Recipe, directory):
    import cadquery as cq
    import hashlib
    import trimesh
    from pathlib import Path
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    shape=build(recipe)
    step=directory/'model.step'; stl=directory/'model.stl'
    cq.exporters.export(shape,str(step))
    cq.exporters.export(shape,str(stl),tolerance=recipe.tolerance,angularTolerance=recipe.angular_tolerance)
    for name,angle in [('isometric',(-1,-1,1)),('front',(0,-1,0)),('top',(0,0,1))]:
        view=directory/(name+'.svg')
        cq.exporters.export(shape,str(view),opt={'width':800,'height':600,'marginLeft':100,'marginTop':75,'projectionDir':angle,'showAxes':False,'showHidden':False})
        # CadQuery supplies fixed dimensions but no viewBox: a narrow img would
        # crop the drawing instead of scaling the full projection to its viewport.
        import xml.etree.ElementTree as ET
        svg=ET.parse(view);svg.getroot().set('viewBox','0 0 800 600')
        svg.getroot().set('preserveAspectRatio','xMidYMid meet')
        svg.write(view,encoding='utf-8',xml_declaration=True)
    mesh=trimesh.load_mesh(stl,process=True)
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        raise ValueError('Exported STL failed watertightness or winding verification')
    # Independently reload the analytic export, not just check that files exist.
    restored=cq.importers.importStep(str(step)); b=shape.val().BoundingBox()
    volume=sum(s.Volume() for s in shape.solids().vals())
    restored_volume=sum(s.Volume() for s in restored.solids().vals())
    if not math.isclose(volume,restored_volume,rel_tol=1e-7,abs_tol=1e-5):
        raise ValueError('STEP round-trip changed the volume')
    return {'valid':True,'units':'mm','solids':len(shape.solids().vals()),'volume_mm3':volume,'bounds_mm':[b.xlen,b.ylen,b.zlen],
      'mesh':{'watertight':bool(mesh.is_watertight),'winding_consistent':bool(mesh.is_winding_consistent),'faces':len(mesh.faces),'volume_mm3':float(mesh.volume)},
      'files':{p.name:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in directory.iterdir() if p.suffix in ('.stl','.step','.svg')}}
