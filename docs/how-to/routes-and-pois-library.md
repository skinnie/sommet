# The library and your watches: routes and POIs

Sommet keeps routes and POIs in two separate places. Knowing which is which answers most
questions about "where did it go".

## Two places

**The library** is Sommet's own collection, kept by Sommet on your computer or tablet. It is
where you keep everything: the 200 km ride for next month, the 5 km loop near home, every hut
and spring you ever marked. It has no size limit that matters.

**A watch's own list** is what is on that watch right now. A watch holds little, so it only
needs what you will use soon. Each watch has its own list.

The two are independent:

- Nothing in the library reaches a watch until you send it there.
- Deleting something in the library leaves every watch as it is.
- Removing something from a watch leaves the library as it is.

So you can use Sommet as your library and give each watch what suits it: the 200 km ride to an
Ambit3 Peak, the 5 km loop to a Traverse, and neither watch has to carry the other's.

## Routes

On **Routes**, the drop-down above the list picks what you are looking at: **On the watch** (or
the eTrex, the Bryton, the Magene) or **Library (saved routes)**.

- A GPX you import, and a route you plan, are kept in the library.
- **Send to device** copies a library route to a connected device.
- A route on a watch can be exported as a GPX, or removed from the watch.

## POIs

On **POIs**, the same drop-down picks **On the watch** or **Library (saved POIs)**. The page is
there without a watch too.

- **Save to library** keeps the POI you made on the map in the library.
- **Send to watch** sends it to the connected watch, and keeps a copy in the library as well.
- A GPX you import is kept in the library; each of its POIs can then be sent to the watch.
- In the watch's list, **⋯ → Save to library** keeps one POI, and **Save all to library** keeps
  all of them. A POI already in the library (same name, same place) is not kept twice.
- In the library, **Send to watch** sends one POI to the connected watch. **⋯** has Export,
  Rename and Delete.

**POI types.** An Ambit3 has 18 POI types and a Traverse has 48, numbered differently. The
library remembers which watch family a POI's type came from, and sends it to the other family
by its icon: a "Camp" stays a "Camp". A Traverse type with no Ambit3 icon becomes the plain
type on an Ambit3.

**Altitude.** A Traverse keeps a POI's altitude; an Ambit3 POI has none. The library keeps the
altitude when a POI has one.

## Where the library is kept

On a computer: the `Routes` and `Pois` folders inside `SommetBackups` in your home folder. Today
each computer or tablet has its own library; a route saved on one is not on the other yet.
