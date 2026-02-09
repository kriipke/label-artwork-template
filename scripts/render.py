import os
import sys
import glob
import yaml
import subprocess
import re
from lxml import etree
import base64
import shutil
from math import floor

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS}

# Geometry constants based on template
MONOLITH_X = 923  # approximate vertical edge of monolith
MONOLITH_GAP = 220  # horizontal gap between monolith edge and track columns
TRACK_LETTER_SPACING_EM = 0.06  # from template CSS var --track

FONT_LOGO = "DMSans-Variable"
FONT_TEXT = "" #"DMSans-Variable"

def parse_metadata_defaults(root) -> dict:
    """
    Reads <metadata id="release_vars">KEY=VALUE</metadata> into defaults.
    Ignores blank lines and lines without '='.
    """
    nodes = root.xpath("//svg:metadata[@id='release_vars']", namespaces=NS)
    if not nodes:
        return {}
    text = nodes[0].text or ""
    defaults = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("RELEASE_VARIABLES"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        defaults[k.strip()] = v.strip()
    return defaults

def set_text(root, element_id: str, value: str):
    el = root.xpath(f"//svg:*[@id='{element_id}']", namespaces=NS)
    if not el:
        raise RuntimeError(f"Missing element id='{element_id}' in template")
    el[0].text = value

def clear_children(el):
    el.text = None
    # remove existing tspans or nodes
    for child in list(el):
        el.remove(child)

def _wrap_words(text: str, max_chars: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return [""]
    words = re.split(r"(\s+)", text)
    lines = []
    line = ""
    for token in words:
        if not token:
            continue
        candidate = line + token
        if len(candidate.strip()) <= max_chars or not line:
            line = candidate
        else:
            lines.append(line.strip())
            line = token.strip()
    if line.strip():
        lines.append(line.strip())
    # Hard-break any single tokens that exceed max_chars (no spaces case)
    fixed = []
    for ln in lines:
        if len(ln) <= max_chars:
            fixed.append(ln)
        else:
            s = ln
            while len(s) > max_chars:
                fixed.append(s[:max_chars] + "-")
                s = s[max_chars:]
            if s:
                fixed.append(s)
    return fixed or [""]

def set_wrapped_text(root, element_id: str, value: str, max_chars: int, max_lines: int | None = None) -> int:
    el = root.xpath(f"//svg:*[@id='{element_id}']", namespaces=NS)
    if not el:
        raise RuntimeError(f"Missing element id='{element_id}' in template")
    el = el[0]
    clear_children(el)
    x = el.get("x")
    y = el.get("y")
    font_size = float(el.get("font-size", "54"))
    lines = _wrap_words(value, max_chars)
    if max_lines and len(lines) > max_lines:
        # truncate with ellipsis on the last line
        lines = lines[:max_lines]
        lines[-1] = (lines[-1][:max(0, max_chars - 1)] + "…") if len(lines[-1]) >= max_chars else (lines[-1] + "…")

    for i, ln in enumerate(lines):
        tspan = etree.Element(f"{{{SVG_NS}}}tspan")
        tspan.set("x", x)
        if i == 0:
            tspan.set("y", y)
        else:
            tspan.set("dy", "1.1em")
        tspan.text = ln
        el.append(tspan)
    return len(lines)

def set_multiline_block(root, element_id: str, header: str | None, items: list[str], max_chars: int, max_lines_total: int = 12, center_vertically: bool = False):
    el = root.xpath(f"//svg:*[@id='{element_id}']", namespaces=NS)
    if not el:
        raise RuntimeError(f"Missing element id='{element_id}' in template")
    el = el[0]
    clear_children(el)
    x = el.get("x")
    y = float(el.get("y"))
    font_size = float(el.get("font-size", "42"))
    line_h = font_size * 1.1

    # Build all lines first
    lines: list[str] = []
    if header:
        lines.extend(_wrap_words(header, max_chars))
    for it in (items or []):
        lines.extend(_wrap_words(str(it), max_chars))
    if max_lines_total and len(lines) > max_lines_total:
        lines = lines[:max_lines_total]

    # Compute starting baseline for vertical centering
    start_y = y
    if center_vertically and lines:
        total_h = line_h * (len(lines) - 1)
        start_y = y - total_h / 2.0

    # Emit tspans
    for i, ln in enumerate(lines):
        tspan = etree.Element(f"{{{SVG_NS}}}tspan")
        tspan.set("x", x)
        if i == 0:
            tspan.set("y", str(start_y))
        else:
            tspan.set("dy", "1.1em")
        tspan.text = ln
        el.append(tspan)
    return len(lines)

def _estimate_max_chars_for_element(el, right_boundary_x: float, padding_px: float = 6.0) -> int:
    x = float(el.get("x"))
    font_size = float(el.get("font-size", "42"))
    # Rough average glyph width + letter-spacing contribution
    char_px = font_size * (0.60 + TRACK_LETTER_SPACING_EM)
    width_px = max(0.0, right_boundary_x - padding_px - x)
    est = max(8, int(width_px // max(1.0, char_px)))
    return est

def set_css_var_in_style(root, var_name: str, value: str):
    """
    Updates :root{ --var_name:...; } inside <style id="css_vars">.
    """
    style_nodes = root.xpath("//svg:style[@id='css_vars']", namespaces=NS)
    if not style_nodes:
        raise RuntimeError("Missing <style id='css_vars'> in template")
    style_el = style_nodes[0]
    css = style_el.text or ""

    # Replace existing var definition; if missing, inject into :root block.
    pattern = rf"(--{re.escape(var_name)}\s*:\s*)([^;]+)(;)"
    if re.search(pattern, css):
        css = re.sub(pattern, rf"\g<1>{value}\g<3>", css, count=1)
    else:
        # Insert into first :root{ ... } block
        css = re.sub(r"(:root\s*\{)", rf"\1\n        --{var_name}:{value};", css, count=1)

    style_el.text = css

def append_css_to_style(root, css_snippet: str):
    style_nodes = root.xpath("//svg:style[@id='css_vars']", namespaces=NS)
    if not style_nodes:
        raise RuntimeError("Missing <style id='css_vars'> in template")
    style_el = style_nodes[0]
    existing = style_el.text or ""
    style_el.text = existing.rstrip() + "\n\n" + css_snippet.strip() + "\n"

def _mime_from_ext(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".woff2"):
        return "font/woff2"
    if lower.endswith(".woff"):
        return "font/woff"
    if lower.endswith(".otf"):
        return "font/otf"
    return "font/ttf"

def embed_font_face(root, family: str, font_path: str, weight: str = "700", style: str = "normal"):
    with open(font_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    mime = _mime_from_ext(font_path)
    css = f"""
@font-face {{
  font-family: '{family}';
  src: url(data:{mime};base64,{b64});
  font-weight: {weight};
  font-style: {style};
  font-display: swap;
}}
"""
    append_css_to_style(root, css)

def _get_inkscape_bin() -> str | None:
    return os.environ.get("INKSCAPE_BIN") or shutil.which("inkscape")

def inkscape_export(svg_path, out_png, out_pdf, px=3000):
    if str(os.environ.get("SKIP_EXPORT", "")).lower() in {"1", "true", "yes"}:
        print("SKIP_EXPORT set; skipping PNG/PDF export")
        return
    bin_path = _get_inkscape_bin()
    if not bin_path:
        print("Inkscape not found; skipping PNG/PDF export. Set INKSCAPE_BIN or install Inkscape.")
        return
    try:
        subprocess.check_call([
            bin_path,
            svg_path,
            f"--export-filename={out_png}",
            f"--export-width={px}",
        ])
        subprocess.check_call([
            bin_path,
            svg_path,
            f"--export-filename={out_pdf}",
        ])
    except FileNotFoundError:
        print("Inkscape binary missing at runtime; skipping PNG/PDF export.")
        return

def render_one(template_path, yml_path, out_root="rendered"):
    with open(yml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(template_path, parser)
    root = tree.getroot()

    defaults = parse_metadata_defaults(root)

    def get(key, fallback=""):
        # YAML keys are lower-case in your schema; metadata uses UPPERCASE
        # We'll check YAML first, then metadata, then fallback.
        if key in data and data[key] is not None:
            return str(data[key])
        return str(defaults.get(key.upper(), fallback))

    # Required-ish: catalog
    catalog = get("catalog", defaults.get("CATALOG", "UNKNOWN"))
    out_dir = os.path.join(out_root, catalog)
    os.makedirs(out_dir, exist_ok=True)

    # Text substitutions
    set_text(root, "t_label", get("label", "IMMUTABLE"))

    coords = data.get("coords") or {}
    lat = coords.get("lat") or defaults.get("COORD_LAT", "")
    lon = coords.get("lon") or defaults.get("COORD_LON", "")
    set_text(root, "t_coord_lat", str(lat))
    set_text(root, "t_coord_lon", str(lon))

    # Artist/Release (replaces former serial/idx area). Fallback to legacy serial/idx if new fields absent.
    artist_val = get('artist', defaults.get('ARTIST', ''))
    release_val = get('release', defaults.get('RELEASE_NAME', ''))
    if not artist_val:
        artist_val = f"SERIAL: {get('serial', defaults.get('SERIAL',''))}"
    if not release_val:
        release_val = f"IDX: {get('idx', defaults.get('IDX',''))}"

    # Wrap artist/release for a right-column width; tuned for font-size 54 and end-anchored text.
    artist_lines = set_wrapped_text(root, "t_artist", artist_val, max_chars=16, max_lines=2)
    # Shift release baseline if artist wrapped to maintain spacing
    rel_nodes = root.xpath("//svg:*[@id='t_release']", namespaces=NS)
    if rel_nodes:
        rel_el = rel_nodes[0]
        base_y = float(rel_el.get("y", "585"))
        line_h = float(rel_el.get("font-size", "54")) * 1.1
        new_y = base_y + (artist_lines - 1) * line_h
        rel_el.set("y", str(new_y))
    set_wrapped_text(root, "t_release", release_val, max_chars=16, max_lines=2)
    # Legacy code/sector removed from template; still available in YAML for reference.

    bottom = f"{catalog} • {get('speed', defaults.get('SPEED','33⅓'))} • {get('genre', defaults.get('GENRE','HARDGROOVE'))}"
    set_text(root, "t_bottom", bottom)

    # Colors (YAML overrides metadata)
    colors = data.get("colors") or {}
    bg = colors.get("bg") or defaults.get("COLOR_BG") or "#B7E718"
    ink = colors.get("ink") or defaults.get("COLOR_INK") or "#0A3DBB"

    # Update CSS vars safely
    set_css_var_in_style(root, "neon-bg", bg)
    set_css_var_in_style(root, "neon-ink", ink)

    # Optional custom fonts from repository: fonts/label.(ttf|otf|woff|woff2) and fonts/text.(ttf|otf|woff|woff2)
    def find_font(prefix: str):
        base = os.path.join("fonts", prefix)
        for ext in (".woff2", ".woff", ".otf", ".ttf"):
            p = base + ext
            if os.path.isfile(p):
                return p
        return None

    label_font = find_font(FONT_LOGO)
    text_font = find_font(FONT_TEXT)

    # Embed fonts and set CSS variables to prefer them
    fallback_stack = '"Roboto Condensed","Arial Narrow","DIN Condensed","Helvetica Neue Condensed",Arial,sans-serif'
    if label_font:
        embed_font_face(root, "RepoLabel", label_font, weight="700")
        set_css_var_in_style(root, "font-label", f"'RepoLabel', {fallback_stack}")
    if text_font:
        embed_font_face(root, "RepoText", text_font, weight="700")
        set_css_var_in_style(root, "font-text", f"'RepoText', {fallback_stack}")

    # Tracks: two lists (Side A on left, Side B on right). Accepts either:
    # tracks: { A: [..], B: [..] } or tracks_a: [..] / tracks_b: [..] / side_a / side_b
    tracks = data.get("tracks") or {}
    tracks_a = tracks.get("A") if isinstance(tracks, dict) else None
    tracks_b = tracks.get("B") if isinstance(tracks, dict) else None
    tracks_a = tracks_a or data.get("tracks_a") or data.get("side_a") or []
    tracks_b = tracks_b or data.get("tracks_b") or data.get("side_b") or []

    # Center-aligned columns with clip guards; keep conservative wrap to avoid touching monolith
    # Left column: compute max_chars so the longest wrapped lines end close to the monolith gap
    left_nodes = root.xpath("//svg:*[@id='t_tracks_a']", namespaces=NS)
    if left_nodes:
        left_el = left_nodes[0]
        left_right_bound = MONOLITH_X - MONOLITH_GAP
        max_chars_left = _estimate_max_chars_for_element(left_el, right_boundary_x=left_right_bound, padding_px=10.0)
    else:
        max_chars_left = 22
    set_multiline_block(root, "t_tracks_a", "SIDE A", tracks_a, max_chars=max_chars_left, max_lines_total=15, center_vertically=True)

    # Right column: keep conservative width; left-aligned starting at its x
    set_multiline_block(root, "t_tracks_b", "SIDE B", tracks_b, max_chars=22, max_lines_total=15, center_vertically=True)

    # Write SVG
    svg_out = os.path.join(out_dir, "label.svg")
    tree.write(svg_out, xml_declaration=True, encoding="UTF-8", pretty_print=True)

    # Render PNG + PDF
    png_out = os.path.join(out_dir, "label.png")
    pdf_out = os.path.join(out_dir, "label.pdf")
    inkscape_export(svg_out, png_out, pdf_out, px=3000)

    return out_dir

def main():
    template = sys.argv[1] if len(sys.argv) > 1 else "templates/label.template.svg"
    releases_glob = sys.argv[2] if len(sys.argv) > 2 else "releases/*.yml"

    files = sorted(glob.glob(releases_glob))
    if not files:
        print(f"No releases found for glob: {releases_glob}")
        return 0

    for yml in files:
        out_dir = render_one(template, yml)
        print(f"Rendered {yml} -> {out_dir}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
