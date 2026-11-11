from kanton import ffmpeg


def test_probe_video(media):
    i = ffmpeg.probe(str(media["face"]))
    assert (i["width"], i["height"]) == (1080, 1920) and i["fps"] == 30 and i["has_audio"] and i["kind"] == "video"
    assert 5.9 < i["duration"] < 6.1 and not i["has_alpha"]


def test_probe_image_and_alpha(media):
    assert ffmpeg.probe(str(media["still"]))["kind"] == "image"
    s = ffmpeg.probe(str(media["sticker"]))
    assert s["has_alpha"] and s["kind"] == "video" and not s["has_audio"]


def test_probe_hlg(media):
    i = ffmpeg.probe(str(media["hlg"]))
    assert i["transfer"] == "arib-std-b67" and ffmpeg.is_hlg(i)
    assert not ffmpeg.is_hlg(ffmpeg.probe(str(media["face"])))


def test_probe_missing_exits(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        ffmpeg.probe(str(tmp_path / "nope.mp4"))


def test_probe_creation_time(media, tmp_path):
    from conftest import ff
    dated = tmp_path / "dated.mp4"
    ff("-i", media["broll"], "-c", "copy", "-metadata", "creation_time=2026-11-01T10:00:00Z", dated)
    assert ffmpeg.probe(str(dated))["created"].startswith("2026-11-01T10:00:00")
    assert ffmpeg.probe(str(media["face"]))["created"] is None
