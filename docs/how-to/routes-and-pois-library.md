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

**Map or List.** The small switch next to the drop-down picks how the routes are shown. **Map**
shows square cards side by side, each with the route drawn on a map, its name, distance and
climb; a click on a square opens the large map. **List** shows one compact line per route. The
choice is remembered.

## POIs

On **POIs**, the same drop-down picks **On the watch** or **Library (saved POIs)**. The page is
there without a watch too. It is one map and one list, side by side (the map above the list on a
narrow screen). The map shows every POI of the list, and it is also where you add one.

**Where it opens.** On a tablet or phone the map opens where the device is, with a blue dot, once
you allow Sommet to use the location (it stays on the device). Indoors, where GPS may find
nothing, it opens on the last position the device had. Before its first position, and on a
computer, it opens on the place the weather uses. That place comes from your internet address
and can be another city. The target button comes back to where the map opened.

**Moving the map.** Drag it, pinch it, or use **+** and **−**. On a computer the mouse wheel
zooms after one click on the map. On a tablet or phone, hold the map still for a moment and then
drag, so a quick swipe still scrolls the page. **All** brings every POI back into view.

**Adding a POI.** Double-click the map (double-tap on a tablet or phone), right-click and choose
**Point here**, or press **+ Add POI**. An orange pin appears and the form opens beside the map.
Drag the pin to the exact spot, give the POI a name and a type, and choose:

- **Save to library** keeps the POI in the library.
- **Send to watch** sends it to the connected watch, and keeps a copy in the library as well.

**Import GPX…** keeps every POI of the file in the library, and each can then be sent to the watch.

**Finding a POI.** Click a pin and its line is marked in the list. Click a line and the map goes
to its pin, and its actions open under it; click it again to see all the pins. **Find in this
list** narrows both the list and the map.

- In the library, a line's actions are **Send to watch**, **Export**, **Rename** and **Delete**.
- In the watch's list they are **Export**, **Save to library** and **Remove from the watch**.
  **Save all to library** keeps all of them. A POI already in the library (same name, same place)
  is not kept twice.

**How many.** An Ambit3 or a Traverse keeps at most 250
POIs, and Sommet says so before sending one more. (Suunto's own software allows an Ambit1 or
Ambit2 100, shared with the waypoints of its routes; Sommet does not check that one.) The
library has no limit.

**POI types.** An Ambit3 has 18 POI types and a Traverse has 48, numbered differently. The
library remembers which watch family a POI's type came from, and sends it to the other family
by its icon: a "Camp" stays a "Camp". A Traverse type with no Ambit3 icon becomes the plain
type on an Ambit3.

**Altitude.** A Traverse keeps a POI's altitude; an Ambit3 POI has none. The library keeps the
altitude when a POI has one.

## Where the library is kept

On a computer: the `Routes` and `Pois` folders inside `SommetBackups` in your home folder. On a
tablet or phone: inside the app's own storage.

## One library on all your devices

With Sommet Sync set up (your own server, Settings), the library is the same on every computer,
tablet and phone: a route or POI you save, rename or delete on one reaches the others. It is
sent a few seconds after the change, and fetched when the app starts or when you open Routes
or POIs. Without Sommet Sync each device keeps its own library.

The same route imported on two devices, or the same POI made on both, ends up once. A delete
in the library reaches your other devices, and still leaves every watch as it is.
