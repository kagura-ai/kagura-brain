"""Phase A smoke test: the package imports and exposes a version."""

from __future__ import annotations

import kagura_brain


def test_version_is_exposed() -> None:
    # Assert shape, not a frozen literal — pinning the exact version makes this
    # test fail on every release bump.
    version = kagura_brain.__version__
    parts = version.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), version
