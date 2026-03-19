"""
Universal extraction script for CoachIQ basketball programs.
Parses Next.js RSC data from HTML files to extract exercises with
names, reps/instructions, and playable video links.

Usage:
    python extract_program.py <program_folder> [--program-name "Name"]

Handles:
- Multiple text blocks per slide (combines into reps)
- Button blocks (skipped)
- Note/intro slides (skipped)
- Mobility/optional workout files
- Various filename formats (w1.d1, w1d1, w1.m, w1.mob, w1.opt, etc.)
"""
import re
import sys
import argparse
from pathlib import Path
from collections import defaultdict

CF_BASE = "https://dgpv43ylujmyh.cloudfront.net/video-desktop/undefined/media/5a2a6a2b-d34d-4da6-994f-650e429bf0dc"

BLOCK_RE = re.compile(
    r'\{"__typename":"Block",'
    r'"id":"(?P<id>[^"]+)",'
    r'"slideId":"(?P<slideId>[^"]+)",'
    r'"index":(?P<index>\d+),'
    r'"type":"(?P<type>[^"]+)",'
    r'"url":(?P<url>"(?:[^"\\]|\\.)*"|null),'
    r'"answer":(?:null|"(?:[^"\\]|\\.)*"),'
    r'"settings":(?:\{[^}]*\}|"\$[^"]*"),'
    r'"choices":(?:\[\]|"\$[^"]*"),'
    r'"contentLong":(?P<clong>"(?:[^"\\]|\\.)*"|null),'
    r'"contentShort":(?P<cshort>"(?:[^"\\]|\\.)*"|null),'
    r'"contentMedium":(?P<cmed>"(?:[^"\\]|\\.)*"|null)'
)


def clean_html(raw: str) -> str:
    c = raw
    c = re.sub(r'<span[^>]*class="html-[^"]*"[^>]*>', '', c)
    c = c.replace('</span>', '')
    c = re.sub(r'<tr><td[^>]*>.*?</td><td class="line-content">', '', c)
    c = c.replace('</td></tr>', '')
    c = c.replace('&lt;', '<').replace('&gt;', '>')
    c = c.replace('&amp;', '&').replace('&quot;', '"')
    return c


def extract_rsc_data(html: str) -> str:
    pushes = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html)
    raw = ''.join(pushes)
    return raw.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n')


def unescape_text(text: str) -> str:
    text = text.replace("\\u0026", "&")
    text = text.replace("\\u003c", "<")
    text = text.replace("\\u003e", ">")
    text = text.replace("\\u0027", "'")
    text = text.replace("\\'", "'")
    text = text.replace('\\"', '"')
    return text.strip()


def s3_to_cloudfront(s3_url: str) -> str:
    m = re.search(r'/(\d{13})\.\w+$', s3_url)
    if m:
        return f"{CF_BASE}/{m.group(1)}.mp4"
    return s3_url


def extract_content(d: dict) -> str | None:
    for field in ('cmed', 'cshort', 'clong'):
        val = d.get(field)
        if val and val != 'null':
            return val.strip('"')
    return None


def extract_film_study(filepath: Path) -> list[dict]:
    """Extract film study content (single video with label) from HTML."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    html = clean_html(raw)
    data = extract_rsc_data(html)

    # Film study uses asset_details with Media type
    label_match = re.search(
        r'"asset_details":\{"__typename":"Media"[^}]*"label":"([^"]+)"[^}]*"url":"([^"]+)"',
        data
    )
    if label_match:
        label = unescape_text(label_match.group(1))
        url = s3_to_cloudfront(label_match.group(2))
        return [{'name': label, 'reps': '', 'video': url}]

    return []


def extract_exercises(filepath: Path) -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    html = clean_html(raw)
    data = extract_rsc_data(html)

    blocks_by_slide = defaultdict(list)
    for m in BLOCK_RE.finditer(data):
        d = m.groupdict()
        url_val = d['url'].strip('"') if d['url'] != 'null' else None

        block = {
            'index': int(d['index']),
            'type': d['type'],
            'url': url_val,
            'cmed': d['cmed'].strip('"') if d['cmed'] != 'null' else None,
            'cshort': d['cshort'].strip('"') if d['cshort'] != 'null' else None,
            'clong': d['clong'].strip('"') if d['clong'] != 'null' else None,
        }
        blocks_by_slide[d['slideId']].append(block)

    slide_order = []
    for m2 in re.finditer(r'\{"__typename":"Slide","id":"([^"]+)"', data):
        sid = m2.group(1)
        if sid not in slide_order:
            slide_order.append(sid)

    exercises = []
    info_texts = []

    for sid in slide_order:
        blocks = blocks_by_slide.get(sid, [])

        seen_keys = set()
        unique_blocks = []
        for b in blocks:
            key = (b['index'], b['type'])
            if key not in seen_keys:
                seen_keys.add(key)
                unique_blocks.append(b)
        blocks = sorted(unique_blocks, key=lambda x: x['index'])

        media_url = None
        text_contents = []

        for b in blocks:
            if b['type'] == 'media' and b['url']:
                media_url = b['url']
            elif b['type'] == 'ciq_text':
                content = extract_content(b)
                if content:
                    content = unescape_text(content)
                    if content.lower() in ('next slide', 'pdf', 'previous slide'):
                        continue
                    if content.startswith('\n'):
                        content = content.strip()
                        if content.lower() in ('next slide',):
                            continue
                    text_contents.append(content)

        if not media_url:
            for t in text_contents:
                if not re.match(r'^Week\s+\d+', t, re.I):
                    info_texts.append(t)
            continue

        if not text_contents:
            continue

        exercise_name = text_contents[0]

        # Skip week/day title slides
        if re.match(r'^Week\s+\d+,?\s*Day\s+\d+', exercise_name, re.I):
            continue

        # Combine remaining text blocks into reps/instructions
        reps_parts = text_contents[1:]
        # Filter out any reps that are week/day titles
        reps_parts = [r for r in reps_parts if not re.match(r'^Week\s+\d+,?\s*Day\s+\d+', r, re.I)]
        reps = ". ".join(reps_parts) if reps_parts else ""

        # Also skip if reps is the title
        if re.match(r'^Week\s+\d+,?\s*Day\s+\d+', reps, re.I):
            reps = ""

        video_link = s3_to_cloudfront(media_url)

        exercises.append({
            'name': exercise_name,
            'reps': reps,
            'video': video_link,
        })

    return exercises, info_texts


def parse_filename(name: str) -> tuple[int, str] | None:
    """Parse HTML filename into (week_number, day_type).
    day_type is 'd1'-'d6', 'mobility', or 'optional'.
    """
    # w1.d1.html, w10.d3.html, w1d1.html
    m = re.match(r'w(\d+)\.?d(\d+)(?:\.full)?\.html', name, re.I)
    if m:
        return int(m.group(1)), f"d{m.group(2)}"

    # w1.m.html, w5.mob.html
    m = re.match(r'w(\d+)\.(?:m|mob)\.html', name, re.I)
    if m:
        return int(m.group(1)), "mobility"

    # w1.mobility.recovery.html
    m = re.match(r'w(\d+)\.mobility', name, re.I)
    if m:
        return int(m.group(1)), "mobility"

    # w1.opt.html
    m = re.match(r'w(\d+)\.opt\.html', name, re.I)
    if m:
        return int(m.group(1)), "optional"

    # w24.d.html (malformed - treat as unknown day)
    m = re.match(r'w(\d+)\.d\.html', name, re.I)
    if m:
        return int(m.group(1)), "dunknown"

    # w1.film.Campazzo.Study.html, w12.film.KyrieIrving.html
    m = re.match(r'w(\d+)\.film\.(.+)\.html', name, re.I)
    if m:
        film_name = m.group(2).replace('.', ' ').replace('_', ' ')
        return int(m.group(1)), f"film:{film_name}"

    return None


DAY_ORDER = {
    'd1': 1, 'd2': 2, 'd3': 3, 'd4': 4, 'd5': 5, 'd6': 6,
    'dunknown': 7, 'mobility': 8, 'optional': 9, 'film': 10,
}

DAY_LABELS = {
    'd1': 'Day 1', 'd2': 'Day 2', 'd3': 'Day 3',
    'd4': 'Day 4', 'd5': 'Day 5', 'd6': 'Day 6',
    'dunknown': 'Day', 'mobility': 'Mobility', 'optional': 'Optional',
}


def get_day_order(day_type: str) -> int:
    if day_type.startswith('film:'):
        return 10
    return DAY_ORDER.get(day_type, 99)


def get_day_label(day_type: str) -> str:
    if day_type.startswith('film:'):
        film_name = day_type[5:]
        return f"Film Study: {film_name}"
    return DAY_LABELS.get(day_type, day_type)


def generate_week_md(week_num: int, days: dict[str, dict]) -> str:
    lines = [f"# Week {week_num}", ""]

    sorted_days = sorted(days.keys(), key=get_day_order)
    quick_links = " | ".join(
        f"[{get_day_label(d)}](#{get_day_label(d).lower().replace(' ', '-').replace(':', '')})"
        for d in sorted_days
    )
    lines.append(f"**Quick Links:** {quick_links}")
    lines.append("")
    lines.append("---")

    for day_type in sorted_days:
        day_data = days[day_type]
        exercises = day_data.get('exercises', [])
        info = day_data.get('info', [])
        label = get_day_label(day_type)
        lines.append("")
        lines.append(f"## {label}")
        lines.append("")

        if info and not exercises:
            for text in info:
                lines.append(f"> {text}")
                lines.append("")
        else:
            lines.append("| Exercise | Reps/Duration | Video |")
            lines.append("|----------|---------------|-------|")

            for ex in exercises:
                name = ex['name'].replace("|", "\\|")
                reps = ex['reps'].replace("|", "\\|")
                video = f"[Video]({ex['video']})" if ex['video'] else ""
                lines.append(f"| {name} | {reps} | {video} |")

        lines.append("")
        lines.append("---")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Extract workout data from HTML files")
    parser.add_argument("folder", help="Path to program folder containing HTML files")
    parser.add_argument("--program-name", default=None, help="Program display name")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing files")
    args = parser.parse_args()

    src_dir = Path(args.folder)
    out_dir = src_dir / "workouts"
    program_name = args.program_name or src_dir.name

    if not src_dir.exists():
        print(f"ERROR: {src_dir} does not exist")
        sys.exit(1)

    out_dir.mkdir(exist_ok=True)

    weeks = defaultdict(dict)
    skipped = []

    for html_file in sorted(src_dir.glob("w*.html")):
        parsed = parse_filename(html_file.name)
        if not parsed:
            skipped.append(html_file.name)
            continue

        week, day_type = parsed

        # Skip duplicate "full" versions if regular version exists
        if ".full." in html_file.name.lower():
            regular = html_file.name.replace(".full.", ".")
            if (src_dir / regular).exists():
                print(f"  SKIP duplicate: {html_file.name}")
                continue

        print(f"  Processing: {html_file.name} -> Week {week}, {get_day_label(day_type)}")

        try:
            if day_type.startswith('film:'):
                exercises = extract_film_study(html_file)
                info_texts = []
            else:
                exercises, info_texts = extract_exercises(html_file)
            if exercises or info_texts:
                weeks[week][day_type] = {'exercises': exercises, 'info': info_texts}
                if exercises:
                    print(f"    Found {len(exercises)} exercises")
                else:
                    print(f"    Info-only section ({len(info_texts)} text blocks)")
            else:
                print(f"    WARNING: No exercises found!")
        except Exception as e:
            print(f"    ERROR: {e}")

    if skipped:
        print(f"\n  Skipped files (unrecognized names): {skipped}")

    # Generate weekly markdown files
    total_exercises = 0
    total_with_reps = 0
    for week_num in sorted(weeks.keys()):
        days = weeks[week_num]
        md = generate_week_md(week_num, days)

        for day_data in days.values():
            for ex in day_data.get('exercises', []):
                total_exercises += 1
                if ex['reps']:
                    total_with_reps += 1

        if args.dry_run:
            print(f"\n--- week{week_num:02d}.md ({len(days)} sections) ---")
            print(md[:500])
        else:
            out_path = out_dir / f"week{week_num:02d}.md"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"  Written: {out_path.name} ({len(days)} sections)")

    # Generate index.md
    max_week = max(weeks.keys()) if weeks else 0
    total_weeks = max_week

    # Determine phases
    if total_weeks <= 12:
        phases = [
            ("Phase 1: Foundation", 1, 4),
            ("Phase 2: Development", 5, 8),
            ("Phase 3: Mastery", 9, min(12, total_weeks)),
        ]
    else:
        third = total_weeks // 3
        phases = [
            ("Phase 1: Foundation", 1, third),
            ("Phase 2: Development", third + 1, 2 * third),
            ("Phase 3: Performance", 2 * third + 1, total_weeks),
        ]

    index_lines = [
        f"# {program_name}",
        "",
        f"**{total_weeks}-Week Training Program**",
        "",
        "---",
        "",
        "## Weekly Schedule",
    ]

    for phase_name, start, end in phases:
        index_lines += ["", f"### {phase_name}", "", "| Week | Days | Link |", "|------|------|------|"]
        for w in range(start, end + 1):
            if w in weeks:
                day_types = sorted(weeks[w].keys(), key=get_day_order)
                day_labels = [get_day_label(d) for d in day_types]
                day_str = ", ".join(day_labels)
                index_lines.append(f"| Week {w} | {day_str} | [View Week {w}](week{w:02d}.md) |")

    index_lines += [
        "",
        "---",
        "",
        "*Generated from CoachIQ training platform*",
        "",
    ]

    if not args.dry_run:
        index_path = out_dir / "index.md"
        with open(index_path, "w", encoding="utf-8") as f:
            f.write("\n".join(index_lines))
        print(f"  Written: index.md")

    print(f"\nDone! {len(weeks)} weeks, {total_exercises} exercises ({total_with_reps} with reps, {total_exercises - total_with_reps} without)")


if __name__ == "__main__":
    main()
