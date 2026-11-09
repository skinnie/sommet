# Use the tools

The `tools/` directory holds the Python scripts that decode and write the Ambit3 navigation
database. Full module-by-module spec: [Tools format spec](../reference/tools-format-spec.md)
(or `tools/README.md` directly).

## Everyday commands

```
./tools/selftest.py                        # non-regression, the whole corpus
./tools/decode_route.py CAPTURE             # dump + self-checks
./tools/decode_route.py --sequence CAPTURE  # chronological command sequence
./tools/regen_route.py CAPTURE --from-gpx GPX [--route N]
./tools/build_route.py --compare CAPTURE GPX [GPX...]
```

`build_route.py` builds the entire navigation database from GPX files alone and diffs it byte
for byte against a capture, packet sequence included.

## Writing to the watch

```
./tools/write_nav.py reset                   # simulates, nothing is emitted
./tools/write_nav.py reset --compare CAPTURE  # checks against a capture
./tools/write_nav.py route GPX              # distance, ascent, descent from the GPX
./tools/write_nav.py route GPX --meta CAPTURE  # ...or SuuntoLink's values from a capture
./tools/write_nav.py delroute --name NAME --write   # remove one route
./tools/write_nav.py reset --write            # ACTUALLY EMITS
```

A route write ends with a commit that empties the watch's POI list. The tools save the POIs to
`~/SommetBackups/POIs/<serial>/POIs_pre_<date>.bin` first, then put them back; if that fails
twice, the error names this file.

A route's distance is the whole GPX summed with the 6371 km Earth radius, and its ascent and
descent are every rise and drop between points (as SuuntoLink's own `route.js`). A GPX without
elevations gives 0 m of climb, as in SuuntoLink.

Dry-run is the default across this toolset — pass `--write` explicitly to touch a real watch.
See `docs/reference/compatibility.md` before trusting a feature on a watch that isn't marked
confirmed there, and the `docs/tools/compatibility-matrix.html` scratch tool for running a
test session.
