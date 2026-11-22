"""Draw every final-report figure from the aggregate report tables -> artifacts/reports/figures/ (PDF + PNG)."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_report.figures import Figures

if __name__ == "__main__":
    Figures(ROOT).run()
