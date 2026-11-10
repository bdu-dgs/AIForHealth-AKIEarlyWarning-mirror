"""Notebook 03, Part 15: feature-window and missingness sensitivity analyses that retrain the locked model.

Settings: configs/robustness_retrain_v1.yaml (frozen with input and code hashes on the first run).
Fitted heads are cached per variant and horizon, so an interrupted run resumes. About 10 min per variant on 16 threads.
"""
import argparse
import ctypes
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from aki_robustness.retrain import RetrainStudy


def keep_full_speed():
    """Windows: keep the machine awake and stop EcoQoS from moving the process to efficiency cores."""
    if sys.platform != "win32":
        return
    ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)          # ES_CONTINUOUS | ES_SYSTEM_REQUIRED

    class State(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

    state = State(1, 1, 0)                                                # execution-speed throttling off
    ctypes.windll.kernel32.SetProcessInformation(ctypes.windll.kernel32.GetCurrentProcess(), 4,
                                                 ctypes.byref(state), ctypes.sizeof(state))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variants", nargs="*", default=None, help="subset of variant names (default: all)")
    args = ap.parse_args()
    keep_full_speed()
    RetrainStudy(ROOT).run(args.variants)


if __name__ == "__main__":
    main()
