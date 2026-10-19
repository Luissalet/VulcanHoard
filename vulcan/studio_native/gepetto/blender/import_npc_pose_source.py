"""Make a poseable Blender input from an ACNH NPC's original Collada mesh."""
import json
from pathlib import Path
import sys
import bpy

source=Path(sys.argv[sys.argv.index('--')+1]).resolve()
target=Path(sys.argv[sys.argv.index('--')+2]).resolve()
report=Path(sys.argv[sys.argv.index('--')+3]).resolve()
if not source.is_file() or source.suffix.lower()!='.dae' or target.exists():
    raise ValueError('Expected existing DAE and a new .blend destination')
bpy.ops.wm.collada_import(filepath=str(source))
arms=[obj for obj in bpy.context.scene.objects if obj.type=='ARMATURE']
if len(arms)!=1:
    raise ValueError(f'Expected one NPC armature, found {len(arms)}')
required=('Spine_1','Neck','Arm_1_L','Arm_2_L','Arm_1_R','Arm_2_R')
missing=[name for name in required if name not in arms[0].data.bones]
if missing:
    raise ValueError('NPC skeleton misses pose joints: '+', '.join(missing))
for image in bpy.data.images:
    if image.filepath:
        image.filepath=bpy.path.abspath(image.filepath)
bpy.ops.wm.save_as_mainfile(filepath=str(target))
report.write_text(json.dumps({'source':str(source),'blend':str(target),
                             'armature':arms[0].name,'required_bones':list(required),
                             'images':[image.filepath for image in bpy.data.images]},
                            ensure_ascii=False,indent=2),encoding='utf-8')
