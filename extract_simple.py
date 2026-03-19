"""
Extract simple (non-weekly) programs that are mostly single-video-per-file.
Generates a single index.md with a table listing all videos.

For programs with Slide/Block workout files (like Advanced Ballhandling Day files),
those are extracted as full exercise tables within the same markdown.

Usage:
    python extract_simple.py <folder> --program-name "Name" [--slug "slug-name"]
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


def extract_media_asset(filepath: Path) -> dict | None:
    """Extract single video from asset_details Media structure."""
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    html = clean_html(raw)
    data = extract_rsc_data(html)

    label_match = re.search(
        r'"asset_details":\{"__typename":"Media"[^}]*"label":"((?:[^"\\]|\\.)*)"[^}]*"url":"((?:[^"\\]|\\.)*)"',
        data
    )
    if label_match:
        label = unescape_text(label_match.group(1))
        url = s3_to_cloudfront(label_match.group(2))
        return {'name': label, 'video': url}

    return None


def extract_exercises(filepath: Path) -> list[dict]:
    """Extract exercises from Slide/Block structure."""
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
                    text_contents.append(content)

        if not media_url or not text_contents:
            continue

        exercise_name = text_contents[0]
        if re.match(r'^Week\s+\d+|^Day\s+\d+', exercise_name, re.I):
            continue

        reps_parts = [t for t in text_contents[1:]
                      if not re.match(r'^Week\s+\d+|^Day\s+\d+', t, re.I)]
        reps = " | ".join(reps_parts)

        video_link = s3_to_cloudfront(media_url)
        exercises.append({'name': exercise_name, 'reps': reps, 'video': video_link})

    return exercises


def parse_file_order(name: str) -> tuple[int, str]:
    """Extract numeric order and clean title from filename."""
    m = re.match(r'(\d+)\s*[-_.]\s*(.+)\.html$', name, re.I)
    if m:
        order = int(m.group(1))
        title = m.group(2).strip()
        return order, title

    m = re.match(r'(Day)(\d+)\.html$', name, re.I)
    if m:
        return 100 + int(m.group(2)), f"Day {m.group(2)}"

    return 999, name.replace('.html', '')


def main():
    parser = argparse.ArgumentParser(description="Extract simple video-list programs")
    parser.add_argument("folder", help="Path to program folder")
    parser.add_argument("--program-name", required=True, help="Program display name")
    args = parser.parse_args()

    src_dir = Path(args.folder)
    out_dir = src_dir / "workouts"

    if not src_dir.exists():
        print(f"ERROR: {src_dir} not found")
        sys.exit(1)

    out_dir.mkdir(exist_ok=True)

    html_files = sorted(src_dir.glob("*.html"), key=lambda f: parse_file_order(f.name))

    video_entries = []
    workout_sections = []

    for html_file in html_files:
        order, title = parse_file_order(html_file.name)
        print(f"  {html_file.name}")

        asset = extract_media_asset(html_file)
        if asset:
            video_entries.append({
                'order': order,
                'title': asset['name'],
                'video': asset['video'],
                'filename': html_file.name,
            })
            print(f"    -> Video: {asset['name'][:60]}")
            continue

        exercises = extract_exercises(html_file)
        if exercises:
            workout_sections.append({
                'order': order,
                'title': title,
                'exercises': exercises,
            })
            print(f"    -> Workout: {len(exercises)} exercises")
            continue

        video_entries.append({
            'order': order,
            'title': title,
            'video': None,
            'filename': html_file.name,
        })
        print(f"    -> No extractable content (title only)")

    lines = [f"# {args.program_name}", ""]

    if video_entries:
        lines += [
            "| # | Title | Video |",
            "|---|-------|-------|",
        ]
        for i, entry in enumerate(video_entries, 1):
            title = entry['title'].replace("|", "\\|")
            video = f"[Video]({entry['video']})" if entry['video'] else ""
            lines.append(f"| {i} | {title} | {video} |")
        lines.append("")

    for section in workout_sections:
        lines += [
            "---",
            "",
            f"## {section['title']}",
            "",
            "| Exercise | Reps/Duration | Video |",
            "|----------|---------------|-------|",
        ]
        for ex in section['exercises']:
            name = ex['name'].replace("|", "\\|")
            reps = ex['reps'].replace("|", "\\|")
            video = f"[Video]({ex['video']})" if ex['video'] else ""
            lines.append(f"| {name} | {reps} | {video} |")
        lines.append("")

    lines += [
        "---",
        "",
        "*Generated from CoachIQ training platform*",
        "",
    ]

    index_path = out_dir / "index.md"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\n  Written: {index_path}")
    print(f"  Videos: {len(video_entries)}, Workout sections: {len(workout_sections)}")


if __name__ == "__main__":
    main()
