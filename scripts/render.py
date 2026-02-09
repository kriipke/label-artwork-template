import os
import sys
import glob
import yaml
import subprocess
import re
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS}

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

def inkscape_export(svg_path, out_png, out_pdf, px=3000):
    subprocess.check_call([
        "inkscape",
        svg_path,
        f"--export-filename={out_png}",
        f"--export-width={px}",
    ])
    subprocess.check_call([
        "inkscape",
        svg_path,
        f"--export-filename={out_pdf}",
    ])

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
