"""Thresholds from THRESHOLDS.md, in one place. Change a number here only with evidence in DESIGN.md."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Per-turn budgets (P-6)
T_BUD_TOOLS = 6
T_BUD_TOKENS = 30_000
T_BUD_WALL_S = 30

# Memory (R-2)
T_MEM_TOPK = 5
T_MEM_MINSCORE = float(os.getenv("T_MEM_MINSCORE", "0.25"))
T_MEM_MAXCHARS = 500

RUNS_DIR = ROOT / "runs"

JUDGE_URL = f"http://127.0.0.1:{os.getenv('JUDGE_PORT', '10002')}/"
MASKER_URL = f"http://127.0.0.1:{os.getenv('MASKER_PORT', '10003')}/"
