#!/usr/bin/env python3
"""Build the published Sommet site from the single-file page.

    python3 _src/build.py

Source  : _src/sommet.html  (the one-file page as saved from the editor: every
          "page" in one document, shown by a #/hash router, images inline)
Output  : index.html, watches/, software/, help/, faq/, contribute/, legal/,
          credits/, img/, sitemap.xml, robots.txt   (in the branch root)

Why: a search engine sees a #/hash site as ONE address, so only one page can
ever rank. This gives each page its own address, title and description, and
moves the images out of the HTML (3 MB -> a few tens of KB per page).

To change the site: edit _src/sommet.html (or drop a new export over it), run
this script, commit, push.
"""
import base64
import hashlib
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parent / "sommet.html"
OUT = Path(__file__).resolve().parent.parent
DRAFTS = Path(__file__).resolve().parent / "pages"
BASE = "https://skinnie.github.io/sommet/"

# slug -> title, description, breadcrumb name. Order = sitemap order.
PAGES = {
    "home": dict(
        title="Sommet — desktop & Android app for Suunto Ambit, Traverse and Kailash",
        desc=None,  # keep the description written in the source
        crumb="Home",
    ),
    "watches": dict(
        title="Supported watches: Suunto Ambit 1, 2, 3, Traverse, Kailash & Garmin eTrex — Sommet",
        desc="Every watch Sommet works with: Suunto Ambit, Ambit2, Ambit3 Peak, Sport, Run and Vertical, "
             "Traverse, Traverse Alpha and Kailash, plus Garmin eTrex handhelds — what each one can do "
             "over USB or Bluetooth, without Movescount.",
        crumb="Watches",
    ),
    "software": dict(
        title="Sommet for Linux, Windows, macOS and Android — the app for your Suunto Ambit",
        desc="Screenshots of Sommet, the free app that replaces Movescount and Moveslink: activities with "
             "map and charts, route planner, sport-mode editor, watch settings, totals and calendar. "
             "Linux, Windows, macOS and Android.",
        crumb="Software",
    ),
    "faq": dict(
        title="Sommet FAQ — the app for Suunto Ambit, Traverse and Kailash watches",
        desc="Answers about Sommet: can a Suunto Ambit still be used without Movescount, is a phone or an "
             "account needed, which watches are supported, and what to do if something goes wrong.",
        crumb="FAQ",
    ),
    "help": dict(
        title="Old Suunto Ambit help: dead battery, broken strap, Moveslink — Sommet",
        desc="Keeping an old Suunto Ambit, Traverse or Kailash going: battery, strap, the Moveslink icon — "
             "and which second-hand watch to look for if you don't own one yet.",
        crumb="Help",
    ),
    "contribute": dict(
        title="Contribute to Sommet — donate, send watch captures, code or time",
        desc="Ways to help Sommet, the open-source app for legacy Suunto watches: donate, send captures "
             "from watches not yet supported, give time and skills, or share it.",
        crumb="Contribute",
    ),
    "credits": dict(
        title="Credits — the projects Sommet is built on",
        desc="The reverse-engineering work, open-source projects, services and libraries Sommet relies on, "
             "including Openambit and OpenSportSync.",
        crumb="Credits",
    ),
    "legal": dict(
        title="Legal & licenses — Sommet",
        desc="Sommet's licence (GNU GPLv3), third-party frameworks, data and image credits. "
             "Not affiliated with Suunto or Garmin.",
        crumb="Legal & licenses",
    ),
}
# old #/key -> (page, anchor on that page)
ROUTES = {p: (p, None) for p in PAGES}
ROUTES.update(get=("home", "get"), features=("home", "kit"), why=("home", "why"))


def url_for(key, prefix):
    page, anchor = ROUTES.get(key, ("home", None))
    path = (prefix or "./") if page == "home" else f"{prefix}{page}/"
    return path + (f"#{anchor}" if anchor else "")


def slugify(text):
    text = re.sub(r"&[a-z]+;", " ", text.lower())
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:48]


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def main():
    global OUT
    if "--out" in sys.argv:
        OUT = Path(sys.argv[sys.argv.index("--out") + 1]).resolve()
        OUT.mkdir(parents=True, exist_ok=True)
    src = SRC.read_text(encoding="utf-8")
    head = src[: src.index("</head>")]
    body_start = src.index('<div id="app">')
    main_open = src.index("<main>")
    main_close = src.index("</main>")
    footer_end = src.index("</div><!-- /#app -->")
    header = src[body_start:main_open]
    footer = src[main_close + len("</main>"):footer_end]

    marks = list(re.finditer(r'<div class="page[^"]*" id="p-([a-z]+)"', src))
    sections = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else main_close
        chunk = re.sub(r"\s*<!-- =+ [A-Z &]+ =+ -->\s*$", "\n", src[m.start():end])
        sections[m.group(1)] = re.sub(r'^<div class="page[^"]*"', '<div class="page active"', chunk)
    if "--drafts" in sys.argv:  # extra pages kept as fragments in _src/pages/, not published by default
        for f in sorted(DRAFTS.glob("*.html")):
            raw = f.read_text(encoding="utf-8")
            meta, body = raw.split("-->", 1)
            meta = json.loads(meta.replace("<!--", "", 1))
            PAGES[meta["slug"]] = meta
            ROUTES[meta["slug"]] = (meta["slug"], None)
            sections[meta["slug"]] = '<div class="page active">\n' + body.strip() + "\n</div>\n"
    missing = set(PAGES) - set(sections)
    if missing:
        sys.exit(f"source has no section for: {sorted(missing)}")

    styles = "\n".join(re.findall(r"(?s)<style>.*?</style>", head))
    fonts = "\n".join(re.findall(r'<link rel="(?:preconnect|stylesheet)"[^>]*>', head))
    src_desc = re.search(r'<meta name="description" content="([^"]*)"', head).group(1)
    ld_blocks = re.findall(r'(?s)<script type="application/ld\+json">(.*?)</script>', head)
    ld = {json.loads(b)["@type"]: json.loads(b) for b in ld_blocks}

    img_dir = OUT / "img"
    img_dir.mkdir(exist_ok=True)
    used = set()

    def externalise(html, slug, prefix):
        """Inline data: images -> files in img/, with size, alt and lazy loading."""
        count = 0

        def repl(m):
            nonlocal count
            tag = m.group(0)
            data = re.search(r'src="data:image/([a-z+]+);base64,([^"]+)"', tag)
            if not data:
                return tag
            raw = base64.b64decode(data.group(2))
            im = Image.open(io.BytesIO(raw))
            w, h = im.size
            alt = re.search(r'alt="([^"]*)"', tag)
            alt_text = alt.group(1) if alt else ""
            if not alt_text:  # fall back to the caption that follows the image
                cap = re.match(r"\s*<figcaption>(.*?)</figcaption>", html[m.end():], re.S)
                alt_text = text_of(cap.group(1)).replace('"', "&quot;") if cap else ""
            ext = {"jpeg": "jpg", "svg+xml": "svg"}.get(data.group(1), data.group(1))
            name = f"{slugify(alt_text) or slug}-{hashlib.sha1(raw).hexdigest()[:8]}.{ext}"
            (img_dir / name).write_bytes(raw)
            used.add(name)
            tag = tag.replace(data.group(0), f'src="{prefix}img/{name}"')
            tag = re.sub(r'\salt="[^"]*"', "", tag)
            tag = re.sub(r"\s(width|height|loading|decoding|fetchpriority)=\"[^\"]*\"", "", tag)
            first = count == 0
            count += 1
            load = 'fetchpriority="high"' if first else 'loading="lazy" decoding="async"'
            return tag.replace("<img", f'<img alt="{alt_text}" width="{w}" height="{h}" {load}', 1)

        return re.sub(r"<img\b[^>]*>", repl, html)

    def relink(html, slug, prefix):
        html = re.sub(r'href="#/([a-z]*)"', lambda m: f'href="{url_for(m.group(1) or "home", prefix)}"', html)
        # mark the current page in the navigation
        html = re.sub(r'(<a href="[^"]*" data-nav="%s")( class="([^"]*)")?' % slug,
                      lambda m: f'{m.group(1)} class="{((m.group(3) or "") + " cur").strip()}" aria-current="page"',
                      html)
        return html

    sitemap = []
    for slug, meta in PAGES.items():
        home = slug == "home"
        prefix = "" if home else "../"
        url = BASE if home else f"{BASE}{slug}/"
        desc = meta["desc"] or src_desc
        title = meta["title"]
        esc = lambda s: s.replace("&", "&amp;").replace('"', "&quot;")

        graph = []
        if home:
            graph += [ld[k] for k in ("WebSite", "SoftwareApplication", "SoftwareSourceCode") if k in ld]
        else:
            graph.append({
                "@context": "https://schema.org", "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Sommet", "item": BASE},
                    {"@type": "ListItem", "position": 2, "name": meta["crumb"], "item": url},
                ],
            })
        if slug == "faq" and "FAQPage" in ld:
            graph.append(ld["FAQPage"])
        ld_html = "\n".join(
            '<script type="application/ld+json">' + json.dumps(g, ensure_ascii=False) + "</script>" for g in graph)

        body = externalise(relink(header + "<main>\n" + sections[slug] + "</main>" + footer, slug, prefix),
                           slug, prefix)
        page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(desc)}">
<meta name="author" content="André Faria">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="canonical" href="{url}">
<link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">
<link rel="icon" href="{prefix}favicon.png" sizes="32x32" type="image/png">
<link rel="apple-touch-icon" href="{prefix}apple-touch-icon.png">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Sommet">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:locale" content="en">
<meta property="og:image" content="{BASE}og.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="A mountain ridgeline at golden hour — Sommet, field software for Suunto watches">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{BASE}og.jpg">
{ld_html}
<script>try{{var t=localStorage.getItem('sommet-theme');if(t)document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}</script>
{fonts}
{styles}
<noscript><style>.reveal{{opacity:1;transform:none}}</style></noscript>
</head>
<body>
{body}</div><!-- /#app -->
<script>{SCRIPT.replace("__HOME__", "true" if home else "false").replace("__ROUTES__", json.dumps({k: url_for(k, prefix) for k in ROUTES}))}</script>
</body>
</html>
"""
        target = OUT / "index.html" if home else OUT / slug / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(page, encoding="utf-8")
        sitemap.append(url)
        print(f"{slug:11s} {len(page.encode()) / 1024:7.1f} KB  {title}")

    for old in img_dir.iterdir():  # drop images no page uses any more
        if old.name not in used:
            old.unlink()

    today = date.today().isoformat()
    (OUT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc><lastmod>{today}</lastmod></url>\n" for u in sitemap)
        + "</urlset>\n", encoding="utf-8")
    (OUT / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /sommet/_src/\n\nSitemap: {BASE}sitemap.xml\n", encoding="utf-8")
    print(f"{len(used)} images, {len(sitemap)} pages in sitemap.xml")


SCRIPT = r"""
var HOME=__HOME__, ROUTES=__ROUTES__;
/* old #/page addresses keep working */
function oldRoute(){
  if(location.hash.indexOf('#/')!==0)return;
  var k=location.hash.slice(2); if(ROUTES[k]) location.replace(ROUTES[k]);
}
if(HOME){oldRoute();window.addEventListener('hashchange',oldRoute);}
/* theme toggle (remembered across pages) */
document.getElementById('themeToggle').addEventListener('click',function(){
  var root=document.documentElement, cur=root.getAttribute('data-theme');
  var sysDark=window.matchMedia('(prefers-color-scheme:dark)').matches;
  var next=(cur? cur==='dark' : sysDark)?'light':'dark';
  root.setAttribute('data-theme',next);
  try{localStorage.setItem('sommet-theme',next);}catch(e){}
});
/* mobile menu */
var menuBtn=document.getElementById('menuBtn'),mobNav=document.getElementById('mobNav');
if(menuBtn&&mobNav){menuBtn.addEventListener('click',function(){var o=mobNav.classList.toggle('open');menuBtn.setAttribute('aria-expanded',o?'true':'false');});
  mobNav.addEventListener('click',function(e){if(e.target.closest('a'))mobNav.classList.remove('open');});}
/* reveal on scroll */
(function(){
  var els=document.querySelectorAll('.reveal:not(.in)');
  var reduce=window.matchMedia('(prefers-reduced-motion:reduce)').matches;
  if('IntersectionObserver' in window && !reduce){
    var io=new IntersectionObserver(function(es){es.forEach(function(en){if(en.isIntersecting){en.target.classList.add('in');io.unobserve(en.target);}})},{threshold:.1,rootMargin:'0px 0px -6% 0px'});
    els.forEach(function(el){io.observe(el)});
  } else els.forEach(function(el){el.classList.add('in')});
})();
"""

if __name__ == "__main__":
    main()
