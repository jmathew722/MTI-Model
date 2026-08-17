"""An unclassifiable sheet must not silently delete the whole part.

REAL CASE (TEST3 batch, 2026-08-17). Part folder `SB10009/` holds two sheets:

    SB10009 SHT 3.pdf
    SB10009 SHT 4.pdf

`classify_view` is filename-based. Neither stem contains a view keyword, neither
starts with a digit, and neither equals the folder name — so both were skipped,
`PartViews.views` came out empty, and extraction died with
"No view images supplied for multi-view extraction". **The part produced no
model, no macros and no report at all.**

The single-sheet parts in the same batch survived only by luck: a folder
`A040791E/` holding `A040791E.PDF` matches the "stem equals folder name" rule and
becomes an overview.

Dropping every sheet is the one outcome the guiding principle forbids — a
complete approximate model is always correct, an incomplete one always wrong. So
when nothing classifies, the sheets are adopted as overview context and flagged.
"""
from pathlib import Path

from pipeline.view_ingest import OVERVIEW_VIEW, _collect_part, classify_view


class TestUnclassifiableSheetsAreAdopted:
    def test_the_real_sb10009_case(self):
        """Two sheets, neither classifiable — the part must still have views."""
        paths = [Path("SB10009 SHT 3.pdf"), Path("SB10009 SHT 4.pdf")]
        assert classify_view(paths[0].name) == "", "precondition: unclassifiable"
        assert classify_view(paths[1].name) == "", "precondition: unclassifiable"

        part = _collect_part("SB10009", paths)

        assert part.views, "both sheets were dropped — the part is lost"
        assert part.views[OVERVIEW_VIEW] == paths[0]
        assert len(part.views) == 2, "the second sheet must be kept too"
        assert any("adopted" in w for w in part.warnings), (
            "adopting sheets is a guess and must be stated in the warnings"
        )

    def test_the_warning_names_the_sheets_and_the_remedy(self):
        part = _collect_part("SB10009", [Path("SB10009 SHT 3.pdf")])
        warning = next(w for w in part.warnings if "adopted" in w)
        assert "SB10009 SHT 3.pdf" in warning
        assert "front_view" in warning, "the warning must say how to remove the guess"

    def test_an_empty_folder_adopts_nothing(self):
        """No sheets means no views — never invent one."""
        part = _collect_part("EMPTY", [])
        assert part.views == {}

    def test_classified_sheets_are_untouched(self):
        """The fallback must only fire when NOTHING classified."""
        paths = [Path("P_front_view.png"), Path("weird name.png")]
        part = _collect_part("P", paths)
        assert part.views.get("front") == paths[0]
        assert OVERVIEW_VIEW not in part.views, (
            "one classified view means the fallback must stay out of the way"
        )
        assert any("Could not classify" in w for w in part.warnings)

    def test_stem_equals_folder_name_still_wins(self):
        """The pre-existing overview rule keeps working."""
        part = _collect_part("A040791E", [Path("A040791E.PDF")])
        assert part.views[OVERVIEW_VIEW] == Path("A040791E.PDF")
        assert not any("adopted" in w for w in part.warnings), (
            "this path was already handled; it should not report a guess"
        )
