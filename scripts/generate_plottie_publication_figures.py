"""Generate reference-inspired publication figures for MemGuardOS.

This is the only figure-generation entry point. Figure classes live in
separate modules:
- scripts/plottie_figures/method_schematics.py
- scripts/plottie_figures/quantitative_results.py
- scripts/plottie_figures/image_plates.py
"""

from __future__ import annotations

import shutil

from plottie_figures.evidence import load_evidence
from plottie_figures.image_plates import DRAWERS as IMAGE_PLATE_DRAWERS
from plottie_figures.method_schematics import DRAWERS as METHOD_DRAWERS
from plottie_figures.quantitative_results import DRAWERS as QUANT_DRAWERS
from plottie_figures.style import OUT_ROOT, FigureRegistry, apply_publication_style


def main() -> None:
    apply_publication_style()
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    evidence = load_evidence()
    registry = FigureRegistry()
    for drawer in [*METHOD_DRAWERS[:9], *QUANT_DRAWERS, METHOD_DRAWERS[9], IMAGE_PLATE_DRAWERS[0], METHOD_DRAWERS[10], *IMAGE_PLATE_DRAWERS[1:]]:
        drawer(evidence, registry)
    registry.copy_curated_sets()
    registry.write_index()
    print(f"generated {len(registry.rows)} publication figures under {OUT_ROOT}")


if __name__ == "__main__":
    main()
