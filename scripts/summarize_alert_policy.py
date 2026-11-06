"""Compatibility entry point for aggregate-only report finalization."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from aki_eval.reporting import diagnostic_representatives, finalize_reports, point_constraints

__all__ = ["diagnostic_representatives", "finalize_reports", "point_constraints"]
