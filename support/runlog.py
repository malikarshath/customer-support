"""Write one run log per turn: runs/<turn_id>.json (EVALS 4.1). The pipeline writes it, the runner reads it."""
from __future__ import annotations

import json
import os

from support import config


def write_run_log(log: dict, failing: bool = False) -> str:
    """Atomic write. A deliberately failed turn goes to runs/failing/ so trajectory checks never read it."""
    d = config.RUNS_DIR / "failing" if failing else config.RUNS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{log['turn_id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(log, indent=2))
    os.replace(tmp, path)
    return str(path)
