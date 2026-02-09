import os
import sys
import glob
import yaml
import subprocess
from lxml import etree

NS = {"svg": "http://www.w3.org/2000/svg"}

def set_text(root, element_id, value):
    el = root.xpath(f"//svg:*[@id='{element_id}']", namespaces=NS)
    if not el:
        raise RuntimeError(f"Missing element id='{element_id}' in template")
    el[0].text = value

def set_css_vars(svg_text, bg, ink):
    # Replace the first occurrences of --neon-bg and --neon-ink defaults.
    # Assumes template contains lines like: --neon-bg:#B7E718; and --neon-ink:#0A3DBB;
    svg_text = svg_text.replace("--neon-bg:#B7E718;", f"--neon-bg:{bg};", 1)
    svg_text = svg_text.replace("--neon-ink:#0A3DBB;", f"--neon-ink:{ink};", 1)
    return svg_text

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
        data = yaml.safe_load(f)

    catalog = data["catalog"]
    out_dir = os.path.join(out_root, catalog)
    os.makedirs(out_dir, exist_ok=True)

    # Load SVG template
    parser = etree.XMLParser(remove_blank_text=False)
    tree = etree.parse(template_path, parser)
    root = tree.getroot()

    # Text substitutions
    set_text(root, "t_label", data.get("label", "IMMUTABLE"))
    coords = data.get("coords", {})
    set_text(root, "t_coord_lat", coords.get("lat", ""))
    set_text(root, "t_coord_lon", coords.get("lon", ""))

    set_text(root, "t_serial", f"SERIAL: {data.get('serial','')}")
    set_text(root, "t_idx", f"IDX: {data.get('idx','')}")
    set_text(root, "t_code", f"CODE: {data.get('code','')}")
    set_text(root, "t_sector", f"SECTOR: {data.get('sector','')}")

    bottom = f"{catalog} • {data.get('speed','33⅓')} • {data.get('genre','HARDGROOVE')}"
    set_text(root, "t_bottom", bottom)

    # Serialize, then set CSS vars (easiest without fragile CSS parsing)
    svg_bytes = etree.tostring(tree, pretty_print=True, xml_declaration=True, encoding="UTF-8")
    svg_text = svg_bytes.decode("utf-8")

    colors = data.get("colors", {})
    bg = colors.get("bg", "#B7E718")
    ink = colors.get("ink", "#0A3DBB")
    svg_text = set_css_vars(svg_text, bg, ink)

    # Write SVG
    svg_out = os.path.join(out_dir, "label.svg")
    with open(svg_out, "w", encoding="utf-8") as f:
        f.write(svg_text)

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
