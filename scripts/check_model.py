"""Load the fine-tuned decision model once and answer one question; used by the setup scripts."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ai import laya_guard  # noqa: E402

guard = laya_guard.LayaGuard(device="cpu")
r = guard.predict("E. coli in urine on our ward, women, last 4 weeks")
print("model answers:", r["intent"], r["view"], {k: v for k, v in r["slice"].items() if v}, f"{r['latency_ms']:.0f} ms")
