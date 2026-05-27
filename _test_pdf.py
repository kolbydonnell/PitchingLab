"""Test the PDF export end-to-end."""
import sys, types
from pathlib import Path
for m in ["streamlit", "plotly", "plotly.express", "plotly.graph_objects",
          "streamlit.components", "streamlit.components.v1"]:
    sys.modules[m] = types.ModuleType(m)
sys.path.insert(0, str(Path(__file__).parent))

from pitching_lab import generate_demo_session, generate_pbr_pdf

print("=" * 60)
print("PDF EXPORT TEST")
print("=" * 60)

df = generate_demo_session("Marcus Vance", hand="Right")
print(f"\nDemo session: {len(df)} pitches")

print("\nGenerating PDF...")
pdf_bytes = generate_pbr_pdf(df, "Marcus Vance", "Right", "2027")
print(f"PDF generated: {len(pdf_bytes):,} bytes")

# Verify it's a valid PDF (starts with %PDF magic)
assert pdf_bytes[:4] == b"%PDF", "Output is not a valid PDF"
print(f"PDF header check: ✓ valid")

# Save to disk so we can visually inspect
out_path = Path(__file__).parent / "sample_pbr.pdf"
out_path.write_bytes(pdf_bytes)
print(f"\nSaved to: {out_path}")
print(f"File size: {out_path.stat().st_size:,} bytes")

print("\n" + "=" * 60)
print("PDF EXPORT TEST PASSED")
print("=" * 60)
