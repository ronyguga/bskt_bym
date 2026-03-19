"""Copy workout markdown files into docs/ for MkDocs build."""
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"

PROGRAMS = {
    "ball-handling": ROOT / "BallHandling" / "workouts",
    "ultimate-atl": ROOT / "Ultimate Atl" / "workouts",
    "ultimate-shooting": ROOT / "Ultimate Shooting" / "workouts",
    "by-any-means": ROOT / "ld" / "workouts",
    "small-guard": ROOT / "Small Guard Essentials" / "workouts",
    "point-guard": ROOT / "Point Guard Program Training" / "workouts",
    "pg-flex": ROOT / "Point Guard Flex Workout" / "workouts",
    "pg-live": ROOT / "Point Guard Live" / "workouts",
    "bh-mindset": ROOT / "Ballhandling Mindset" / "workouts",
    "pg-iq": ROOT / "Point Guard Program" / "workouts",
    "pg-resources": ROOT / "Poing Guard Resources" / "workouts",
    "adv-ballhandling": ROOT / "Advanced Ballhandling" / "workouts",
}

def main():
    if DOCS.exists():
        for sub in DOCS.iterdir():
            if sub.name == "index.md":
                continue
            if sub.is_dir():
                shutil.rmtree(sub)
            elif sub.suffix == ".md":
                pass

    DOCS.mkdir(exist_ok=True)

    for slug, src in PROGRAMS.items():
        dest = DOCS / slug
        dest.mkdir(exist_ok=True)
        if not src.exists():
            print(f"  SKIP {src} (not found)")
            continue
        for md in sorted(src.glob("*.md")):
            shutil.copy2(md, dest / md.name)
            print(f"  {md.name} -> docs/{slug}/{md.name}")

    extra_css = ROOT / "extra.css"
    if extra_css.exists():
        shutil.copy2(extra_css, DOCS / "extra.css")

    print("Done. Run: mkdocs build")

if __name__ == "__main__":
    main()
