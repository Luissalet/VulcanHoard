"""Optional CPU mesh passes. Every artifact is written to a new job folder."""
import hashlib
import json
from pathlib import Path
import shutil
import time

import fast_simplification
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt,binary_closing,binary_fill_holes
from scipy.spatial import cKDTree
import trimesh


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def topology(mesh):
    _,counts=np.unique(mesh.edges_sorted,axis=0,return_counts=True)
    return {'faces':len(mesh.faces),'vertices':len(mesh.vertices),
            'boundary_edges':int(np.count_nonzero(counts==1)),
            'nonmanifold_edges':int(np.count_nonzero(counts>2)),
            'watertight':bool(mesh.is_watertight),'winding_consistent':bool(mesh.is_winding_consistent),
            'positive_volume':bool(mesh.is_volume),'components':int(mesh.body_count),
            'bbox_normalized':mesh.bounds.tolist()}


def simplify(mesh,count):
    if count is None:
        return mesh
    while len(mesh.faces)>count:
        previous=len(mesh.faces)
        vertices,faces=fast_simplification.simplify(np.asarray(mesh.vertices,dtype=np.float64),
            np.asarray(mesh.faces,dtype=np.int32),target_count=count,agg=7)
        mesh=trimesh.Trimesh(vertices,faces,process=True)
        mesh.update_faces(mesh.unique_faces() & mesh.nondegenerate_faces())
        # Dense generated meshes can exhaust one collapse pass above the budget.
        # Rebuild its adjacency and try again while reduction remains useful.
        if len(mesh.faces)>=previous*.99:
            break
    return mesh


class Appearance:
    def __init__(self,source,material_source):
        path=Path(material_source)/'pbr-voxels.npz' if material_source else None
        self.voxel=bool(path and path.exists())
        if self.voxel:
            data=np.load(path)
            centers=(data['coords'].astype(np.float32)+.5)/int(data['resolution'])-.5
            self.tree=cKDTree(centers)
            self.attributes=data['attributes']
        else:
            self.tree=cKDTree(source.vertices)
            color=np.asarray(source.visual.to_color().vertex_colors if source.visual.kind=='texture'
                             else source.visual.vertex_colors,dtype=np.float32)/255
            self.attributes=np.concatenate([color[:,:3],np.zeros((len(color),1)),
                np.full((len(color),1),.8),color[:,3:4]],axis=1)

    def sample(self,positions):
        # Generated PBR voxels use engine XYZ; exported GLB uses YZX.
        query=positions[:,[2,0,1]] if self.voxel else positions
        return self.attributes[self.tree.query(query,workers=4)[1]]


def bake(mesh,appearance,folder,size,progress):
    import xatlas
    atlas=xatlas.Atlas()
    atlas.add_mesh(np.asarray(mesh.vertices,dtype=np.float32),np.asarray(mesh.faces,dtype=np.uint32))
    pack=xatlas.PackOptions();pack.padding=4
    atlas.generate(pack_options=pack)
    if atlas.atlas_count!=1:
        raise ValueError('No se ha podido empaquetar el modelo en una sola textura. Prueba con menos caras.')
    mapping,faces,uvs=atlas[0]
    vertices=mesh.vertices[mapping]
    positions=np.zeros((size,size,3),dtype=np.float32)
    valid=np.zeros((size,size),dtype=bool)
    pixels=uvs*np.array([size,-size])+np.array([0,size])
    for face in faces:
        tri=pixels[face];a,b,c=tri
        denom=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
        if abs(denom)<1e-12:
            continue
        lo=np.maximum(np.floor(tri.min(0)).astype(int),0)
        hi=np.minimum(np.ceil(tri.max(0)).astype(int),size-1)
        if (hi<lo).any():
            continue
        xx,yy=np.meshgrid(np.arange(lo[0],hi[0]+1)+.5,np.arange(lo[1],hi[1]+1)+.5)
        w0=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/denom
        w1=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/denom
        w2=1-w0-w1;inside=(w0>=0)&(w1>=0)&(w2>=0)
        patch=positions[lo[1]:hi[1]+1,lo[0]:hi[0]+1]
        patch[inside]=(w0[...,None]*vertices[face[0]]+w1[...,None]*vertices[face[1]]+w2[...,None]*vertices[face[2]])[inside]
        valid[lo[1]:hi[1]+1,lo[0]:hi[0]+1]|=inside
    if not valid.any():
        raise ValueError('No se ha podido crear el mapa de materiales.')
    progress('baking_materials',.65)
    values=np.concatenate([appearance.sample(chunk) for chunk in np.array_split(positions[valid],
        max(1,int(np.ceil(valid.sum()/262144))))])
    attrs=np.zeros((size,size,6),dtype=np.float32);attrs[valid]=values
    nearest=distance_transform_edt(~valid,return_distances=False,return_indices=True)
    attrs=attrs[tuple(nearest)]
    color=Image.fromarray(np.clip(attrs[:,:,[0,1,2,5]]*255,0,255).astype(np.uint8))
    mr=np.zeros((size,size,3),dtype=np.uint8)
    mr[...,1]=np.clip(attrs[...,4]*255,0,255).astype(np.uint8)
    mr[...,2]=np.clip(attrs[...,3]*255,0,255).astype(np.uint8)
    metallic=Image.fromarray(mr)
    color.save(folder/'base-color.png');metallic.save(folder/'metallic-roughness.png')
    material=trimesh.visual.material.PBRMaterial(name='Baked PBR',baseColorTexture=color,
        metallicRoughnessTexture=metallic,metallicFactor=1,roughnessFactor=1,doubleSided=True,
        alphaMode='MASK' if attrs[...,5].min()<.98 else 'OPAQUE')
    result=trimesh.Trimesh(vertices,faces,process=False,
        visual=trimesh.visual.TextureVisuals(uv=uvs,material=material),
        vertex_normals=mesh.vertex_normals[mapping])
    return result,{'texture_size':size,'covered_texels':int(valid.sum()),'vertices_with_uv_seams':len(vertices),
                  'uv_method':'packed chart atlas','material_method':'nearest source PBR voxel' if appearance.voxel else 'nearest source vertex color'}


def run(spec,progress):
    begin=time.perf_counter();folder=Path(spec['output_dir']);source_path=Path(spec['mesh'])
    original_sha=digest(source_path)
    progress('checking_mesh',.03)
    source=trimesh.load(source_path,force='mesh',process=False)
    source_faces=len(source.faces)
    appearance=Appearance(source,spec.get('material_source'))
    mesh=trimesh.Trimesh(source.vertices.copy(),source.faces.copy(),process=True)
    mesh.update_faces(mesh.unique_faces() & mesh.nondegenerate_faces())
    count=spec.get('detail_faces') if spec['kind']=='repair' else spec['target_faces']
    progress('simplifying_mesh',.12)
    mesh=simplify(mesh,count)
    if spec['kind']=='repair':
        before=topology(mesh)
        progress('repairing_mesh',.25)
        method=spec.get('method','surface')
        if method=='volume':
            if spec.get('remove_small_components',False):
                vertices_labels=trimesh.graph.connected_component_labels(mesh.edges_unique,node_count=len(mesh.vertices))
                labels=vertices_labels[mesh.faces[:,0]]
                mesh.update_faces(labels==np.bincount(labels).argmax())
                mesh.remove_unreferenced_vertices()
            pitch=float(mesh.extents.max())/spec['voxel_resolution']
            grid=mesh.voxelized(pitch)
            solid=binary_fill_holes(binary_closing(np.pad(grid.matrix,2),structure=np.ones((3,3,3),dtype=bool),iterations=1))
            mesh=trimesh.voxel.ops.matrix_to_marching_cubes(solid)
            mesh.vertices-=2
            mesh.apply_transform(grid.transform)
            repair_info={'method':'voxel volume reconstruction','voxel_resolution':spec['voxel_resolution'],
                         'voxel_pitch_normalized':pitch,'closing_voxels':1}
        else:
            import pymeshfix
            fixer=pymeshfix.PyTMesh(False)
            fixer.load_array(np.asarray(mesh.vertices,dtype=np.float64),np.asarray(mesh.faces,dtype=np.int32))
            if spec.get('remove_small_components',False):
                fixer.remove_smallest_components()
            filled=fixer.fill_small_boundaries()
            fixer.clean(max_iters=10,inner_loops=3)
            filled+=fixer.fill_small_boundaries()
            vertices,faces=fixer.return_arrays()
            mesh=trimesh.Trimesh(vertices,faces,process=True)
            repair_info={'method':'mesh cleanup, boundary fill and intersection removal','boundaries_patched':int(filled)}
        mesh.update_faces(mesh.unique_faces() & mesh.nondegenerate_faces())
        if not len(mesh.faces):
            raise ValueError('La reparación no ha conservado una superficie. Prueba con mayor detalle.')
        trimesh.repair.fix_inversion(mesh,multibody=True)
        after=topology(mesh)
        colors=appearance.sample(mesh.vertices)
        mesh.visual=trimesh.visual.ColorVisuals(mesh=mesh,
            vertex_colors=np.clip(colors[:,[0,1,2,5]]*255,0,255).astype(np.uint8))
        processing={**repair_info,'before':before,'after':after,
                    'closure_verified':bool(after['positive_volume'] and after['watertight']),
                    'note':'Consulta el estado de cierre. La reconstrucción puede suavizar detalles o unir piezas próximas; grosor, soportes y ajustes se revisan en el laminador.'}
    else:
        mesh.fix_normals(multibody=True)
        after=topology(mesh)
        progress('unwrapping_mesh',.35)
        mesh,baked=bake(mesh,appearance,folder,spec['texture_size'],progress)
        processing={'method':'quadric simplification, UV charts and PBR baking',**baked,
                    'target_faces':spec['target_faces'],'budget_met':len(mesh.faces)<=spec['target_faces'],
                    'note':'Malla triangular optimizada; no añade rigging ni retopología de animación.'}
    progress('preparing_export',.85)
    mesh.export(folder/'model.glb');mesh.export(folder/'model.stl');mesh.export(folder/'model.ply')
    preview=mesh.copy()
    if len(preview.faces)>200000:
        preview=simplify(trimesh.Trimesh(mesh.vertices,mesh.faces,process=True),200000)
        preview.visual=trimesh.visual.ColorVisuals(mesh=preview,
            vertex_colors=np.clip(appearance.sample(preview.vertices)[:,[0,1,2,5]]*255,0,255).astype(np.uint8))
    preview.export(folder/'model-preview.glb')
    preview.visual=trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
        baseColorFactor=[60,65,75,255],roughnessFactor=.8,metallicFactor=0,doubleSided=True))
    preview.export(folder/'model-clay.glb')
    for name in ['input.png','preprocessed.png']:
        p=Path(spec['source_folder'])/name
        if p.exists():
            shutil.copy2(p,folder/name)
    assert digest(source_path)==original_sha
    processing.update(topology_basis='welded working geometry before UV seams',
                      source_sha256=original_sha,source_faces=source_faces,result_faces=len(mesh.faces),
                      source_glb_bytes=source_path.stat().st_size,glb_bytes=(folder/'model.glb').stat().st_size)
    (folder/'processing-report.json').write_text(json.dumps(processing,indent=2),encoding='utf-8')
    files={'full_glb':'model.glb','glb':'model-preview.glb','clay':'model-clay.glb',
           'stl':'model.stl','ply':'model.ply','processing_report':'processing-report.json'}
    for key,name in [('color','base-color.png'),('material','metallic-roughness.png'),('reference','preprocessed.png')]:
        if (folder/name).exists():files[key]=name
    metrics={**after,'faces':len(mesh.faces),'vertices':len(mesh.vertices),'preview_faces':len(preview.faces)}
    return {'files':files,'metrics':metrics,'processing':processing,'kind':spec['kind'],
            'materials':'none' if spec.get('material_mode')=='none' else 'baked' if spec['kind']=='optimize' else 'transferred','seconds_total':time.perf_counter()-begin,
            'execution':'CPU','gpu_leases':0}
