# sommet

Interoperability reverse engineering, to send GPX routes to a **Suunto Ambit3** — and now
Traverse and Kailash too — without Movescount, which is dead, without an account and without
a server.

The binary format of the watch's navigation database is decoded and verified byte for byte
against USB and Bluetooth captures of SuuntoLink and the Suunto app. The serializer exists in
Python and in C, the latter written to drop into openambit's `libambit` unmodified.

**It works on hardware.** Routes, POIs, sport modes, settings, activities and more have been
written to and read from real watches over USB and Bluetooth LE — see the
[compatibility matrix](reference/compatibility.md) for exactly what's confirmed on which watch.

This is an **interoperability** project: it targets hardware the user already owns, using
lawfully obtained copies of the manufacturer's own software, and does not circumvent any
technical protection measure or redistribute manufacturer software. See the
[project overview](explanation/project-overview.md) for the full scope and legal basis.

## Where to start

- New to the project? Start with a [tutorial](tutorials/index.md).
- Trying to get something specific done? Check the [how-to guides](how-to/index.md).
- Looking for a spec, a device list, or the changelog? See [reference](reference/index.md).
- Want the background and the "why"? Read the [explanations](explanation/index.md).

Current project status, milestone by milestone, lives in `HANDOFF.md` at the repo root — it
changes fast enough that it's kept out of this stable documentation site.
