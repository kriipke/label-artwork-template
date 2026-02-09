import os
import sys
import glob
import yaml
import subprocess
import re
from lxml import etree
import base64
import shutil

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS}

FONT_LOGO = "DMSans-Variable"
FONT_TEXT = "DMSans-Variable"

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

    set_text(root, "t_serial", f"SERIAL: {get('serial', defaults.get('SERIAL',''))}")
    set_text(root, "t_idx",    f"IDX: {get('idx', defaults.get('IDX',''))}")
    set_text(root, "t_code",   f"CODE: {get('code', defaults.get('CODE',''))}")
    set_text(root, "t_sector", f"SECTOR: {get('sector', defaults.get('SECTOR',''))}")

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
