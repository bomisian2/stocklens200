from __future__ import annotations

import pandas as pd
import pytest

from stocklens.storage import SNAPSHOT_COLUMNS, SnapshotError, SnapshotStore


def test_validation_rejects_incomplete_snapshot() -> None:
    frame = pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    with pytest.raises(SnapshotError, match="exactly 200"):
        SnapshotStore.validate(frame)
