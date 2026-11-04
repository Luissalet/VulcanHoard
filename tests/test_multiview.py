"""Numerical checks for native multiview kernels, with no models or GPU."""
import numpy as np
from PIL import Image
import pytest

torch = pytest.importorskip('torch')
import torch.nn.functional as F

from vulcan.studio_native.daedalus.daedalus.geometry.multiview_model.modules.sparse.basic import SparseTensor, VarLenTensor
from vulcan.studio_native.daedalus.daedalus.geometry.multiview_model.modules.sparse.conv import SparseConv3d
from vulcan.studio_native.daedalus.daedalus.geometry.multiview_model.modules.sparse.attention.full_attn import sparse_scaled_dot_product_attention
from vulcan.studio_native.daedalus.daedalus.geometry.multiview_model.modules.sparse.attention.routed_views import PackedRoutedFullOnlyLayoutBuilder, RoutedViewCrossAttention
from vulcan.studio_native.daedalus.daedalus.geometry.multiview_model.utils.preprocessing import build_input_batch
from vulcan.studio_native.daedalus.daedalus.geometry.multiview_generation import cropped_image


@pytest.mark.parametrize('dilation', [1, 2])
def test_sparse_convolution_matches_dense_oracle(dilation):
    torch.manual_seed(19)
    coords = torch.tensor([[0,0,0,0], [0,1,1,1], [0,2,1,1], [0,3,3,2], [1,0,0,0], [1,2,2,1], [1,3,3,2]], dtype=torch.int32)
    features = torch.randn(len(coords), 2)
    layer = SparseConv3d(2, 3, 3, dilation=dilation)
    volume = torch.zeros(2, 2, 4, 4, 3)
    for point, feature in zip(coords, features):
        b, x, y, z = point.tolist()
        volume[b, :, x, y, z] = feature
    dense = F.conv3d(volume, layer.weight.permute(0,4,1,2,3), layer.bias, padding=dilation, dilation=dilation)
    expected = torch.stack([dense[b,:,x,y,z] for b,x,y,z in coords.tolist()])
    sparse = SparseTensor(features, coords)
    torch.testing.assert_close(layer(sparse).feats, expected, atol=2e-6, rtol=2e-6)
    # A second call exercises the cached neighbor map, not just its construction.
    torch.testing.assert_close(layer(sparse).feats, expected, atol=2e-6, rtol=2e-6)


def attention_oracle(q, k, v):
    q, k, v = (value.transpose(0, 1) for value in (q,k,v))
    scores = q @ k.transpose(-1,-2) / q.shape[-1]**.5
    return (scores.softmax(-1) @ v).transpose(0,1)


@pytest.mark.parametrize('packed', [True, False])
def test_ragged_attention_keeps_batches_separate(packed):
    torch.manual_seed(7)
    q = VarLenTensor.from_tensor_list([torch.randn(2,2,4), torch.randn(1,2,4)])
    k = VarLenTensor.from_tensor_list([torch.randn(3,2,4), torch.randn(4,2,4)])
    v = k.replace(torch.randn_like(k.feats))
    expected = torch.cat([attention_oracle(q.feats[qs], k.feats[ks], v.feats[ks]) for qs,ks in zip(q.layout,k.layout)])
    if packed:
        kv = k.replace(torch.stack([k.feats,v.feats], dim=1))
        output = sparse_scaled_dot_product_attention(q, kv)
    else:
        output = sparse_scaled_dot_product_attention(q, k, v)
    torch.testing.assert_close(output.feats, expected, atol=2e-6, rtol=2e-6)


def test_packed_self_attention_matches_oracle():
    torch.manual_seed(17)
    qkv = VarLenTensor.from_tensor_list([torch.randn(2,3,2,4), torch.randn(3,3,2,4)])
    expected = torch.cat([attention_oracle(*qkv.feats[section].unbind(1)) for section in qkv.layout])
    torch.testing.assert_close(sparse_scaled_dot_product_attention(qkv).feats, expected)


def test_routed_attention_selects_real_views_and_restores_order():
    torch.manual_seed(23)
    coords = torch.tensor([[0,0,0,0], [0,1,0,0], [0,0,1,0], [1,1,1,1]], dtype=torch.int32)
    x = SparseTensor(torch.randn(4,8), coords)
    bank = torch.randn(2,3,9,8)
    selected = torch.zeros(2,2,2,2,dtype=torch.long)
    selected[0,0,0,0] = 2
    selected[0,0,1,0] = 1
    selected[1,1,1,1] = 2
    layout = PackedRoutedFullOnlyLayoutBuilder(2).prepare(x=x, selected_view=selected, batch_size=2, views=3)
    layer = RoutedViewCrossAttention(8,8,2)
    layer.full_tokens = 9
    expected = []
    for row, (batch, cx, cy, cz) in enumerate(coords.tolist()):
        real_view = bank[batch,selected[batch,cx,cy,cz]]
        query = layer.to_q(x.feats[row:row+1]).reshape(1,2,4)
        key, value = layer.to_kv(real_view).reshape(9,2,2,4).unbind(1)
        expected.append(layer.to_out(attention_oracle(query,key,value).reshape(1,8)))
    result = layer(x, {'full_bank':bank, **layout})
    torch.testing.assert_close(result.feats, torch.cat(expected), atol=2e-6, rtol=2e-6)
    changed = bank.clone()
    changed[1,0] += 100  # This view is not selected by any query.
    torch.testing.assert_close(layer(x, {'full_bank':changed, **layout}).feats, result.feats)
    changed[0,2] += 1
    assert not torch.allclose(layer(x, {'full_bank':changed, **layout}).feats[0], result.feats[0])


def test_inputs_keep_distinct_alpha_views_and_black_background(tmp_path):
    paths = []
    for index, color in enumerate(((255,0,0), (0,255,0))):
        pixels = np.zeros((10,14,4),dtype=np.uint8)
        pixels[2:8,3:11,:3] = color
        pixels[2:8,3:11,3] = 255
        path = tmp_path / f'{index}.png'
        Image.fromarray(pixels).save(path)
        paths.append(path)
    batch = build_input_batch(paths, {'alpha_threshold':0,'padding_factor':1}, cropped_image)
    assert batch.dino_images512.shape == (1,2,3,512,512)
    assert batch.vggt_images.shape == (1,2,3,448,448)
    assert batch.input_masks.dtype == torch.bool
    assert batch.dino_images512[0,0,0].sum() > 0
    assert batch.dino_images512[0,0,1].sum() == 0
    assert batch.dino_images512[0,1,1].sum() > 0
    assert not batch.input_masks[0,0,0].any()


def test_native_encoder_matches_reference_feature_path_for_small_model():
    transformers = pytest.importorskip('transformers')
    from vulcan.studio_native.daedalus.daedalus.geometry import ops
    from vulcan.studio_native.daedalus.daedalus.geometry.image_encoder import DINOv3ViTModel
    config = {'hidden_size':32,'intermediate_size':64,'num_hidden_layers':2,'num_attention_heads':4,
              'layer_norm_eps':1e-5,'num_register_tokens':4,'num_channels':3,'patch_size':16,
              'rope_theta':100.,'image_size':32,'use_gated_mlp':False}
    torch.manual_seed(37)
    reference = transformers.DINOv3ViTModel(transformers.DINOv3ViTConfig(**config)).eval()
    native = DINOv3ViTModel(config, torch.float32, 'cpu', ops.disable_weight_init).eval()
    native.load_state_dict(reference.state_dict(), strict=True)
    pixels = torch.randn(2,3,32,32)
    with torch.no_grad():
        hidden = reference.embeddings(pixels, bool_masked_pos=None)
        position = reference.rope_embeddings(pixels)
        for layer in reference.layer:
            hidden = layer(hidden, position_embeddings=position)
        expected = F.layer_norm(hidden, hidden.shape[-1:])
        actual = native(pixels, skip_norm_elementwise=True)[0]
    torch.testing.assert_close(actual, expected, atol=1e-5, rtol=1e-5)


class MixedPrecisionFixture(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.body = torch.nn.Linear(4, 4, dtype=torch.float16)
        self.output = torch.nn.Linear(4, 2, dtype=torch.float32)
        self.register_buffer('phase', torch.tensor([1 + 2j], dtype=torch.complex64))

    def forward(self, value):
        return self.output(self.body(value.half()).float())


def test_meta_checkpoint_assignment_matches_normal_copy_precision_and_output():
    from vulcan.studio_native.daedalus.daedalus.geometry.multiview_model.models.checkpoints import assign_checkpoint
    torch.manual_seed(71)
    source = MixedPrecisionFixture()
    # Checkpoint storage and the inference constructor need not share a dtype.
    checkpoint = {name: value.float() if value.is_floating_point() else value.clone()
                  for name, value in source.state_dict().items()}
    reference = MixedPrecisionFixture()
    reference.load_state_dict(checkpoint)  # Independent normal-copy semantics.
    with torch.device('meta'):
        assigned = MixedPrecisionFixture()
    assign_checkpoint(assigned, checkpoint)
    for name, expected in reference.state_dict().items():
        torch.testing.assert_close(assigned.state_dict()[name], expected, rtol=0, atol=0)
    pixels = torch.randn(3, 4)
    torch.testing.assert_close(assigned(pixels), reference(pixels), rtol=0, atol=0)
    assert assigned.phase.imag.item() == 2


def test_resident_preserves_mixed_precision_until_owned_model_release():
    from vulcan.studio_native.daedalus.daedalus.geometry.multiview_generation import resident
    source = MixedPrecisionFixture()
    with resident(lambda: source, 'cpu') as model:
        assert model.body.weight.dtype == torch.float16
        assert model.output.weight.dtype == torch.float32
        assert model.phase.dtype == torch.complex64 and model.phase.imag.item() == 2
    assert all(value.is_meta for value in list(source.parameters()) + list(source.buffers()))


@pytest.mark.parametrize('axis', [0, 1, 2])
def test_dual_grid_rejects_flattened_boundary_aliases(axis):
    from vulcan.studio_native.daedalus.daedalus.geometry.surface_vae import flexible_dual_grid_to_mesh
    # Exact XYZ lookup is the oracle: each emitted quad must have four real cells.
    import itertools
    dims = (2, 3, 4)
    coords = torch.tensor(list(itertools.product(*(range(size) for size in dims))), dtype=torch.int32)
    flags = torch.zeros((len(coords), 3), dtype=torch.bool)
    flags[:, axis] = True
    _, faces = flexible_dual_grid_to_mesh(coords, torch.full((len(coords), 3), .5),
        flags, torch.ones((len(coords), 1)), aabb=[[0, 0, 0], list(dims)], grid_size=list(dims))
    expected_quads = dims[axis]
    for other in range(3):
        if other != axis:
            expected_quads *= dims[other] - 1
    assert len(faces) == 2 * expected_quads
    for triangle in faces:
        vertices = coords[triangle.long()]
        assert (vertices.max(0).values - vertices.min(0).values <= 1).all()
        assert vertices[:, axis].unique().numel() == 1


def test_dual_grid_closed_cube_exports_consistent_outward_surface():
    import itertools
    from vulcan.studio_native.daedalus.daedalus.geometry.surface_vae import flexible_dual_grid_to_mesh
    from vulcan.studio_native.daedalus.daedalus.geometry.multiview_generation import finalize_mesh
    coords = torch.tensor(list(itertools.product(range(2), repeat=3)), dtype=torch.int32)
    flags = torch.zeros((8, 3), dtype=torch.bool)
    for index, xyz in enumerate(coords.tolist()):
        for axis in range(3):
            flags[index, axis] = all(xyz[other] == 0 for other in range(3) if other != axis)
    vertices, faces = flexible_dual_grid_to_mesh(coords, torch.full((8, 3), .5), flags,
        torch.ones((8, 1)), aabb=[[0, 0, 0], [2, 2, 2]], grid_size=2)
    mesh = finalize_mesh(vertices.numpy(), faces.numpy())
    assert len(mesh.faces) == 12 and len(mesh.vertices) == 8
    assert mesh.is_watertight and mesh.is_winding_consistent
    assert mesh.volume == pytest.approx(1) and mesh.area == pytest.approx(6)
    np.testing.assert_array_equal(mesh.bounds, [[.5, .5, .5], [1.5, 1.5, 1.5]])


def test_surface_cleanup_rejects_all_degenerate_faces():
    from vulcan.studio_native.daedalus.daedalus.geometry.multiview_generation import finalize_mesh
    vertices = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    with pytest.raises(RuntimeError, match='cleanup returned an empty mesh'):
        finalize_mesh(vertices, np.array([[0, 1, 2]]))


def test_surface_cleanup_drops_loose_fragments_but_keeps_real_parts():
    from vulcan.studio_native.daedalus.daedalus.geometry.multiview_generation import finalize_mesh
    import trimesh
    body = trimesh.creation.icosphere(subdivisions=3)
    part = trimesh.creation.icosphere(subdivisions=2)
    part.apply_translation([3, 0, 0])
    speck = trimesh.Trimesh([[9, 9, 9], [10, 9, 9], [9, 10, 9]], [[0, 1, 2]], process=False)
    merged = trimesh.util.concatenate([body, part, speck])
    mesh = finalize_mesh(merged.vertices[:, [2, 0, 1]], merged.faces)
    assert len(mesh.faces) == len(body.faces) + len(part.faces)
    assert mesh.metadata['fragments_removed'] == 1


def test_surface_processing_note_reports_the_dropped_fragments():
    """The recorded provenance must say that loose pieces were removed and how many, not only that faces were de-duplicated."""
    from vulcan.studio_native.daedalus.daedalus.geometry.multiview_generation import (
        finalize_mesh, fragments_removed, surface_processing_note)
    import trimesh
    body = trimesh.creation.icosphere(subdivisions=3)
    speck = trimesh.Trimesh([[9, 9, 9], [10, 9, 9], [9, 10, 9]], [[0, 1, 2]], process=False)
    dirty = trimesh.util.concatenate([body, speck])
    cleaned = finalize_mesh(dirty.vertices[:, [2, 0, 1]], dirty.faces)
    assert fragments_removed(cleaned) == 1
    assert '1 loose piece(s)' in surface_processing_note(cleaned) and 'removed' in surface_processing_note(cleaned)
    clean = finalize_mesh(body.vertices[:, [2, 0, 1]], body.faces)
    assert fragments_removed(clean) == 0
    assert 'no loose piece' in surface_processing_note(clean)
