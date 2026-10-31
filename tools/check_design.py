"""Check contrast, local assets, and structural UI accessibility without a browser."""
import argparse
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "document_automation/web_static"


def luminance(color):
    values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in values]
    return sum(value * factor for value, factor in zip(linear, (0.2126, 0.7152, 0.0722)))


def contrast(first, second):
    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.labels = []
        self.controls = []
        self.resources = []
        self.buttons = []
        self.current_button = None
        self.inline = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.append(attrs["id"])
        if tag == "label" and attrs.get("for"):
            self.labels.append(attrs["for"])
        if tag in {"input", "select", "textarea"}:
            self.controls.append((tag, attrs))
        if tag == "button":
            self.current_button = {"attrs": attrs, "text": ""}
            self.buttons.append(self.current_button)
        if tag == "use" and attrs.get("href"):
            self.resources.append(attrs["href"])
        if tag in {"script", "link"}:
            self.resources.append(attrs.get("src") or attrs.get("href"))
        if "style" in attrs or any(name.startswith("on") for name in attrs):
            self.inline.append(tag)

    def handle_data(self, value):
        if self.current_button is not None:
            self.current_button["text"] += value

    def handle_endtag(self, tag):
        if tag == "button":
            self.current_button = None


def check():
    tokens = json.loads((ASSETS / "design-tokens.json").read_text(encoding="utf-8"))
    colors = tokens["color"]
    errors, pairs = [], []
    for foreground in ("text", "secondary", "muted", "accent", "success", "warning", "error"):
        for background in ("canvas", "surface", "raised"):
            ratio = contrast(colors[foreground], colors[background])
            pairs.append({"foreground": foreground, "background": background, "ratio": round(ratio, 4)})
            if ratio < 4.5:
                errors.append(f"Text contrast below 4.5: {foreground}/{background}")
    primary_ratio = contrast(colors["canvas"], colors["accent"])
    if primary_ratio < 4.5:
        errors.append("Primary button text contrast is below 4.5")
    if contrast(colors["controlBorder"], colors["raised"]) < 3:
        errors.append("Control boundary contrast is below 3")
    css = (ASSETS / "styles.css").read_text(encoding="utf-8")
    css_colors = dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", css))
    aliases = {"controlBorder": "control-border", "accentHover": "accent-hover"}
    for name, value in colors.items():
        if css_colors.get(aliases.get(name, name), "").upper() != value.upper():
            errors.append(f"Token and production CSS differ: {name}")
    if re.search(r"#[0]{3}(?:[0]{3})?\b|#[fF]{3}(?:[fF]{3})?\b|(?:linear|radial)-gradient\(|backdrop-filter", css):
        errors.append("Forbidden pure black/white, decorative gradient, or glass effect")
    if "prefers-reduced-motion:reduce" not in css:
        errors.append("Reduced-motion override is missing")
    icons = (ASSETS / "icons.svg").read_text(encoding="utf-8")
    symbols = set(re.findall(r'<symbol id="([^"]+)"', icons))
    pages = {}
    for filename in ("index.html", "design-system.html"):
        parser = Markup()
        parser.feed((ASSETS / filename).read_text(encoding="utf-8"))
        if len(parser.ids) != len(set(parser.ids)):
            errors.append(f"Duplicate DOM IDs in {filename}")
        for label in parser.labels:
            if label not in parser.ids:
                errors.append(f"Missing labelled control {label} in {filename}")
        for button in parser.buttons:
            if not (button["text"].strip() or button["attrs"].get("aria-label")):
                errors.append(f"Unlabelled button in {filename}")
        if parser.inline:
            errors.append(f"CSP-incompatible inline style/event attributes in {filename}")
        for resource in filter(None, parser.resources):
            if resource.startswith("/static/icons.svg#"):
                if resource.split("#")[1] not in symbols:
                    errors.append(f"Unknown icon: {resource}")
            elif resource.startswith("/static/"):
                if not (ASSETS / resource.removeprefix("/static/")).is_file():
                    errors.append(f"Missing local asset: {resource}")
        pages[filename] = {"unique_ids": len(parser.ids), "labelled_buttons": len(parser.buttons)}
    fonts = {}
    for name in ("InterVariable.woff2", "JetBrainsMono-Regular.woff2"):
        content = (ASSETS / "fonts" / name).read_bytes()
        if not content.startswith(b"wOF2"):
            errors.append(f"Invalid WOFF2 header: {name}")
        fonts[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    return {"passed": not errors, "errors": errors, "contrast_pairs": pairs,
            "minimum_text_ratio": min(item["ratio"] for item in pairs),
            "primary_button_ratio": round(primary_ratio, 4), "pages": pages, "fonts": fonts,
            "browser_geometry_verified": False,
            "limits": "Static structure and computed color ratios only. Browser layout, keyboard focus and screen reader QA need a real browser."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = check()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{'PASS' if result['passed'] else 'FAIL'}: minimum text contrast {result['minimum_text_ratio']:.2f}:1; primary action {result['primary_button_ratio']:.2f}:1.")
    for error in result["errors"]:
        print(error)
    print("Browser geometry is not verified by this check.")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
