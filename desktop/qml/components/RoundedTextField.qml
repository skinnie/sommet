import QtQuick
import QtQuick.Controls
import AmbitApp

// Real request 2026-08-09 ("for everything possible, never use true square stuff, always
// rounded corners") - QtQuick.Controls Basic style's TextField background has no radius at
// all (confirmed against the actual installed style's own TextField.qml - a plain square
// Rectangle). A drop-in replacement for `TextField { ... }` - extends TextField directly,
// so every existing property (text/width/enabled/validator) keeps working unchanged.
TextField {
    id: root

    color: Theme.text
    // Never inherited from the system Qt palette: this app paints its own light Theme.card
    // background, and on a dark desktop the palette's placeholder color is near-white -
    // invisible on it. Found live 2026-08-11: the POI picker's Name and Search boxes showed
    // as two identical anonymous fields.
    placeholderTextColor: Theme.mutedText
    font.pixelSize: Theme.fontSizeBody
    selectionColor: Theme.primary
    selectedTextColor: Theme.card
    verticalAlignment: Text.AlignVCenter
    // General rule for every text-input box in the app (André, 2026-08-13): the typed text and
    // its placeholder are horizontally centered. One place, so every RoundedTextField follows -
    // the same single-source approach as Theme colors and the Card clip rule.
    horizontalAlignment: TextInput.AlignHCenter
    leftPadding: Theme.spacingSmall
    rightPadding: Theme.spacingSmall

    // Real, 2026-08-25 (André, comparing Gear's "None ▾" pickers against its own grey Rename/
    // Retire/Delete buttons: "shouldn't they be all similar?") - every plain INPUT control
    // (button, text field, combo box) now shares RoundedButton's flat Theme.cardNested fill +
    // quiet Theme.border hairline, so a page never mixes white-bordered and flat-grey controls
    // side by side. This is deliberately NOT applied to ThemedDialog/ThemedMenu/the ComboBox
    // POPUP LIST below - those are floating SURFACES (like a Card), not inline controls, and
    // keep the white/card + border look that already matches Card's own "primary" variant.
    background: Rectangle {
        implicitWidth: 120
        implicitHeight: 36
        radius: Theme.radiusSmall
        color: Theme.cardNested
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.primary : Theme.border
        Behavior on border.color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }
}
