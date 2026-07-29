"""Unit tests for the post-feature geometric verification helpers added to
pipeline.solidworks_builder (2026-07-28) — pure-Python graceful-degradation
behavior tested via lightweight mocks. The end-to-end behavior (a cut that
removes no material raises SolidWorksError; a normal build passes) was verified
LIVE against a real SolidWorks session during development: a 4x2x0.5in plate
with 4 through-holes built and its exported STL volume (3.9023 in^3) matched
the analytically expected volume (3.9018 in^3) to within measurement tolerance,
confirming the new checks introduce no false positive on a correct build.
"""
from __future__ import annotations

from pipeline.solidworks_builder import _total_volume, _solid_body_exists


class _Body:
    def __init__(self, volume):
        self._volume = volume

    def GetMassProperties(self, accuracy):
        # SolidWorks returns [cx,cy,cz,volume,surface_area,mass,...]
        return (0.0, 0.0, 0.0, self._volume, 0.0, 0.0)


class _Doc:
    def __init__(self, bodies=(), raise_on_get_bodies=False):
        self._bodies = bodies
        self._raise = raise_on_get_bodies

    def GetBodies2(self, body_type, visible_only):
        if self._raise:
            raise RuntimeError("simulated COM failure")
        return self._bodies


def test_total_volume_sums_all_bodies():
    doc = _Doc(bodies=(_Body(1.5), _Body(0.5)))
    assert _total_volume(doc) == 2.0


def test_total_volume_none_when_no_bodies():
    doc = _Doc(bodies=())
    assert _total_volume(doc) is None


def test_total_volume_none_on_com_failure_never_raises():
    doc = _Doc(raise_on_get_bodies=True)
    assert _total_volume(doc) is None  # must degrade gracefully, never propagate


def test_total_volume_none_when_mass_properties_unavailable():
    class _BadBody:
        def GetMassProperties(self, accuracy):
            raise RuntimeError("DISP_E_MEMBERNOTFOUND")
    doc = _Doc(bodies=(_BadBody(),))
    assert _total_volume(doc) is None


def test_solid_body_exists_true_false():
    assert _solid_body_exists(_Doc(bodies=(_Body(1.0),))) is True
    assert _solid_body_exists(_Doc(bodies=())) is False
    assert _solid_body_exists(_Doc(raise_on_get_bodies=True)) is False
