"""DWG routing — the decision expressed as code (REFACTOR_ANALYSIS §1.6 / §2.4).

Two DWG products exist on purpose (see docs/DWG_PATHS.md). What was wrong was
that nothing said which one handles a given file. These tests pin the rule: the
entry point decides, a file never silently hops products, and a DWG on the
vision path is honest about whether it got the exact-text cross-check.
"""
from pipeline.dwg_routing import (
    DWG_NATIVE,
    VISION_PIPELINE,
    describe,
    is_dwg,
    is_vector_source,
    route_for,
)


class TestClassification:
    def test_dwg_detection_is_case_insensitive(self):
        assert is_dwg("part.dwg") and is_dwg("PART.DWG")
        assert not is_dwg("part.dxf") and not is_dwg("part.pdf")

    def test_vector_sources_are_the_exact_geometry_inputs(self):
        assert all(is_vector_source(f) for f in ("a.dwg", "a.dxf", "a.pdf"))
        assert not is_vector_source("a.png")


class TestRouting:
    def test_a_dwg_on_the_vision_path_stays_on_the_vision_path(self):
        r = route_for("part.dwg")
        assert r.route == VISION_PIPELINE and not r.is_native
        assert r.crosscheck is True
        assert "Stage 2.4" in r.reason

    def test_the_native_pipeline_is_entered_deliberately(self):
        r = route_for("part.dwg", entry_point=DWG_NATIVE)
        assert r.is_native and r.requires_solidworks
        assert "no vision model" in r.reason

    def test_crosscheck_disabled_is_reported_not_silent(self):
        r = route_for("part.dwg", dwg_crosscheck=False)
        assert r.crosscheck is False
        assert "stands on its own" in r.reason

    def test_no_solidworks_disables_the_crosscheck_honestly(self):
        r = route_for("part.dwg", solidworks_available=False)
        assert r.crosscheck is False
        assert "no SolidWorks" in r.reason

    def test_a_pdf_is_the_vision_path_with_vector_geometry_available(self):
        r = route_for("drawing.pdf")
        assert r.route == VISION_PIPELINE
        assert "exact vector geometry" in r.reason
        assert r.crosscheck is False           # Stage 2.4 is DWG-only

    def test_a_raster_is_plain_vision(self):
        r = route_for("scan.png")
        assert r.route == VISION_PIPELINE and not r.crosscheck

    def test_a_non_dwg_handed_to_the_native_pipeline_is_named_as_wrong(self):
        r = route_for("drawing.pdf", entry_point=DWG_NATIVE)
        assert r.is_native
        assert "not a DWG" in r.reason

    def test_route_is_serializable_for_reports(self):
        assert set(route_for("part.dwg").as_dict()) == {
            "route", "reason", "crosscheck", "requires_solidworks"}

    def test_describe_names_both_entry_points(self):
        text = describe()
        assert "8092" in text and "8095" in text and "DWG_PATHS.md" in text


class TestMainDelegates:
    def test_main_is_dwg_uses_the_routing_module(self):
        import inspect
        import main

        assert "dwg_routing" in inspect.getsource(main.is_dwg)
        assert main.is_dwg("x.DWG") and not main.is_dwg("x.pdf")

    def test_main_reports_the_route_for_a_dwg_run(self):
        import inspect
        import main

        assert "route_for" in inspect.getsource(main._dwg_route)
