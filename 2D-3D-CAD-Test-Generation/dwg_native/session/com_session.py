"""SolidWorks COM session lifecycle for the DWG-native pipeline.

Wraps the proven late-bound connect from pipeline.solidworks_builder (imported,
not copied) and adds: dialog suppression, crash detection + rebuild, per-call
document cleanup, and the low-level parameterized-property Invoke helpers the DWG
import needs (win32com late binding cannot set IImportDxfDwgData.ImportMethod;
raw IDispatch.Invoke with DISPATCH_PROPERTYPUT can — established in Phase 0).

WINDOWS ONLY. All COM imports are lazy so this module imports on any OS (the
semantic/tests layers must load without SolidWorks).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("dwg_native.session")


class SessionError(RuntimeError):
    """SolidWorks session could not be established or was lost."""


# swUserPreferenceToggle_e.swInputDimValOnCreate — stop the dimension-value input
# box from popping up during automated sketching (a classic unattended deadlock).
_SW_INPUT_DIM_VAL_ON_CREATE = 4


class ComSession:
    """Owns exactly one SolidWorks application object. Not thread-safe by design —
    only the single JobQueue worker touches it."""

    def __init__(self) -> None:
        self._sw: Optional[Any] = None
        self._pythoncom = None

    # -- lifecycle ---------------------------------------------------------- #
    @property
    def app(self) -> Any:
        if self._sw is None:
            self.connect()
        return self._sw

    def connect(self) -> Any:
        """Attach to a running SolidWorks or launch one; suppress dialogs. Reuses
        pipeline.solidworks_builder.connect_to_solidworks so the connect logic has
        ONE home."""
        from pipeline.solidworks_builder import connect_to_solidworks  # imported, not copied
        import pythoncom  # type: ignore

        self._pythoncom = pythoncom
        try:
            self._sw = connect_to_solidworks()
        except Exception as e:  # normalise to our error type
            raise SessionError(f"Could not establish SolidWorks session: {e}") from e
        self._suppress_dialogs()
        return self._sw

    def _suppress_dialogs(self) -> None:
        sw = self._sw
        if sw is None:
            return
        for setter, args in (
            ("SetUserPreferenceToggle", (_SW_INPUT_DIM_VAL_ON_CREATE, False)),
        ):
            try:
                getattr(sw, setter)(*args)
            except Exception:
                pass  # non-fatal: a missing toggle must never block the queue
        # Route command errors to return codes instead of modal dialogs.
        for attr in ("CommandInProgress",):
            try:
                setattr(sw, attr, True)
            except Exception:
                pass

    def is_alive(self) -> bool:
        """True if the session still answers a trivial COM call."""
        if self._sw is None:
            return False
        try:
            _ = self._sw.RevisionNumber
            return True
        except Exception:
            return False

    def ensure(self) -> Any:
        """Return a live app, rebuilding the session if it died mid-job."""
        if not self.is_alive():
            log.warning("SolidWorks session lost — rebuilding.")
            self._sw = None
            self.connect()
        return self._sw

    def close_all_documents(self) -> None:
        """Close every open document so leaked docs do not accumulate between jobs."""
        if self._sw is None:
            return
        try:
            self._sw.CloseAllDocuments(True)
        except Exception as e:
            log.warning("CloseAllDocuments failed (continuing): %s", e)

    def close_doc(self, title: str) -> None:
        if self._sw is None or not title:
            return
        try:
            self._sw.CloseDoc(title)
        except Exception:
            pass

    # -- low-level parameterized-property helpers (Phase 0 finding) --------- #
    def pput(self, obj: Any, name: str, *args: Any) -> Any:
        """Property PUT via raw IDispatch.Invoke. args = (indexArgs..., value).
        Needed because win32com cannot set IImportDxfDwgData.ImportMethod(Sheet)."""
        oo = obj._oleobj_ if hasattr(obj, "_oleobj_") else obj
        did = oo.GetIDsOfNames(name)
        return oo.Invoke(did, 0, self._pythoncom.DISPATCH_PROPERTYPUT, False, *args)

    def pget(self, obj: Any, name: str, *args: Any) -> Any:
        """Property GET via raw IDispatch.Invoke (handles parameterized props)."""
        oo = obj._oleobj_ if hasattr(obj, "_oleobj_") else obj
        did = oo.GetIDsOfNames(name)
        return oo.Invoke(did, 0, self._pythoncom.DISPATCH_PROPERTYGET, True, *args)
