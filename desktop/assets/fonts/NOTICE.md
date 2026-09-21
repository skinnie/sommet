# MaterialSymbolsRounded.ttf

Subsetted from Google's [Material Symbols](https://github.com/google/material-design-icons)
(`variablefont/MaterialSymbolsRounded[FILL,GRAD,opsz,wght].ttf`), licensed under the
[Apache License 2.0](https://github.com/google/material-design-icons/blob/master/LICENSE).

Pinned to a static instance (FILL=0 outlined, wght=400, GRAD=0, opsz=24) and subset down to
only the glyphs this app actually uses (~6.5 KB vs. the original 15 MB variable font covering
the full icon set). Regenerate after adding a new icon:

```
pip install fonttools brotli   # into a venv, not system Python - see tools/packaging/README.md
                                # for why this project avoids touching system Python

curl -sL -o full.ttf "https://raw.githubusercontent.com/google/material-design-icons/master/variablefont/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf"

fonttools varLib.instancer -o static.ttf full.ttf FILL=0 wght=400 GRAD=0 opsz=24

pyftsubset static.ttf --output-file=MaterialSymbolsRounded.ttf \
    --unicodes="U+xxxx,U+yyyy,..." \
    --layout-features='*' --glyph-names --symbol-cmap --legacy-cmap \
    --notdef-glyph --notdef-outline --recommended-glyphs \
    --name-IDs='*' --name-legacy --name-languages='*'
```

Codepoints for each name come from the same repo's `.codepoints` file (a plain `name hex`
list) - see `qml/Icons.qml` for the ones currently in use and their meaning in this app.

Added 2026-09-21: `church` (U+EAAE) for the POI planner's cemetery pin. Regenerated with the steps above;
all 58 earlier glyphs verified pixel-identical before/after.
