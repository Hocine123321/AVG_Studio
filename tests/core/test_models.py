from pathlib import Path
from avg.core.models import ProjectDoc, PhaseRecord, RenderRequest

def test_project_doc_defaults_schema_version():
    p = ProjectDoc(id="x", title="X", folder=Path("."), aspect_ratio="9:16",
                   short_edge=1080, created="2026-07-24T10:00:00")
    assert p.schema_version == 1
    assert p.phases == {}

def test_render_request_mirrors_render_project_signature():
    r = RenderRequest(project_folder=Path("."))
    assert (r.fps, r.codec, r.aspect_ratio, r.short_edge) == (24, "libx264", "9:16", 1080)
    assert r.kb_mode == "dynamic" and r.render_engine == "auto"

def test_phase_record_roundtrips():
    rec = PhaseRecord(name="render", status="failed", error="boom")
    assert PhaseRecord.model_validate(rec.model_dump()).error == "boom"
