import avg.media.vendor.avg as v

def test_sink_receives_progress(tmp_path):
    seen = []
    v.PROGRESS_SINK = lambda pct, msg: seen.append((pct, msg))
    try:
        v._write_progress(str(tmp_path), 40, "encoding")
    finally:
        v.PROGRESS_SINK = None
    assert seen == [(40, "encoding")]

def test_file_is_still_written(tmp_path):
    import json
    v._write_progress(str(tmp_path), 55, "mixing")
    data = json.loads((tmp_path / "render" / "progress.json").read_text())
    assert data == {"percent": 55, "message": "mixing"}

def test_a_broken_sink_never_fails_the_phase(tmp_path):
    def boom(pct, msg): raise RuntimeError("sink exploded")
    v.PROGRESS_SINK = boom
    try:
        v._write_progress(str(tmp_path), 10, "ok")
    finally:
        v.PROGRESS_SINK = None
