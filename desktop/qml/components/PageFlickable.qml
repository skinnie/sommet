import QtQuick
import QtQuick.Controls
import SommetUi

// Real fix, 2026-08-11 (André: desktop scroll "kinda buggy"). Root cause was every
// scrolling page being a bare Flickable with none of its wheel behaviour configured.
// Plain Flickable's default wheel handling doesn't move a fixed distance - it starts a
// kinetic flick() using the wheel notch as an initial velocity, decelerating over this
// item's own default flickDeceleration (1500 px/s^2). Each notch of a real scroll-wheel
// mouse restarts that curve independently, which is exactly the floaty, inconsistent,
// notch-fights-itself feel André was seeing - a real QtQuick characteristic, not a mouse
// or Linux config problem (checked: no wheel tuning existed anywhere in desktop/qml
// except MapView's own scroll-to-zoom, which is unrelated and opt-in per BUGS_ANDRE.md).
//
// Fixed by replacing that kinetic conversion with a direct, fixed-step contentY move -
// what every native list actually does. A WheelHandler declared directly on a Flickable
// is delivered before Flickable's own built-in wheel handling (Qt Quick tries pointer
// handlers on an Item before its legacy wheelEvent()), so accepting the event here fully
// replaces the default behaviour rather than fighting it.
//
// Drop-in replacement for a page's root `Flickable { ... }` - same type, same properties,
// nothing else about a page needs to change.
Flickable {
    id: root

    // Fixed pixel step per wheel "notch" (one 120-unit angleDelta click). Touchpads that
    // report pixelDelta (libinput two-finger scroll on Linux) use their own delta directly
    // instead, so that stays as smooth/continuous as the trackpad itself already is.
    property int wheelStep: 64

    WheelHandler {
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        onWheel: (event) => {
            const delta = event.pixelDelta.y !== 0
                ? event.pixelDelta.y
                : (event.angleDelta.y / 120) * root.wheelStep
            const maxY = Math.max(0, root.contentHeight - root.height)
            root.contentY = Math.max(0, Math.min(maxY, root.contentY - delta))
            event.accepted = true
        }
    }

    ScrollBar.vertical: ScrollBar {
        policy: ScrollBar.AsNeeded
    }
}
