import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vulcan import blocking, blocking_guide
from vulcan.hoard_link import proc
from vulcan.hoard_link.media import bins
from vulcan.studio.catalog import tool_catalog


def test_blocking_guide_preserves_last_key_and_can_be_restored(tmp_path, monkeypatch):
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    block_id = "b" * 32
    take = {
        "fps": 24, "duration_s": 0.5,
        "keyframes": [{"frame": 0, "seconds": 0}, {"frame": 12, "seconds": 0.5}],
        "files": {"scene": "blocking.blend", "recipe": "blocking.json"},
    }
    monkeypatch.setattr(blocking, "get", lambda *_: take)
    monkeypatch.setattr(blocking_guide, "find_blender", lambda: "blender")
    monkeypatch.setattr(blocking_guide.bins, "find", lambda _: SimpleNamespace(command=lambda *args: ["ffmpeg", *args]))

    def run(command, **kwargs):
        if command[0] == "blender":
            for i in range(command[-1]):
                (command[-2] / f"frame-{i:03d}.png").touch()
        else:
            assert command[command.index("-framerate") + 1] == 24
            assert command[command.index("-frames:v") + 1] == 13
            command[-1].write_bytes(b"mp4")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(blocking_guide.proc, "run", run)
    take_path = blocking._root(services) / block_id
    take_path.mkdir(parents=True)
    guide = blocking_guide.export(services, blocking_guide.Guide(id=block_id))
    assert guide["keyframes"] == take["keyframes"]
    assert guide["blocking_id"] == block_id
    assert guide["duration_s"] == 0.5
    assert guide["video_duration_s"] == pytest.approx(13 / 24)
    path = Path(guide["path"])
    assert path.is_file()
    undone = blocking_guide.undo(services, blocking.Undo(**guide["undo"]["arguments"]))
    assert not path.exists() and take_path.exists()
    blocking_guide.undo(services, blocking.Undo(**undone["undo"]["arguments"]))
    assert path.read_bytes() == b"mp4"


def test_blocking_guide_is_discoverable():
    tools = {row["name"]: row for row in tool_catalog()}
    assert tools["blocking_guide"]["inputSchema"]["required"] == ["id"]
    assert tools["blocking_guide_undo"]["annotations"]["idempotentHint"]


def test_blender_guide_matches_the_blocking_timing(tmp_path):
    ffprobe = bins.find("ffprobe")
    if not blocking_guide.find_blender() or not bins.find("ffmpeg") or not ffprobe:
        pytest.skip("Blender and FFmpeg with FFprobe are required")
    services = SimpleNamespace(config=SimpleNamespace(data_dir=tmp_path))
    poses = [key.model_copy(update={"frame": key.frame // 4}) for key in blocking.preset("pass")]
    take = blocking.build(services, blocking.Build(gesture="custom", size=128, poses=poses))
    guide = blocking_guide.export(services, blocking_guide.Guide(id=take["id"]))
    output = proc.run(ffprobe.command("-v", "error", "-count_frames", "-select_streams", "v:0",
                                     "-show_entries", "stream=nb_read_frames,avg_frame_rate,width,height",
                                     "-of", "json", guide["path"]), timeout=10, check=True)
    video = json.loads(output.stdout)["streams"][0]
    assert (video["width"], video["height"]) == (128, 128)
    assert video["avg_frame_rate"] == f"{take['fps']}/1"
    assert int(video["nb_read_frames"]) == 13
    assert guide["duration_s"] == take["duration_s"] == 0.5
