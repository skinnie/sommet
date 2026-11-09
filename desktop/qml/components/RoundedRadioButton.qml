import QtQuick
import QtQuick.Controls
import SommetUi

// Real, 2026-08-10 ("on dark theme the menu for switch dark light system has grey
// letters, not correct") - SettingsPage.qml's Appearance card used plain stock
// RadioButton, the one remaining place in the app that hadn't been given the same
// themed-wrapper treatment as every other QQC2 control (RoundedButton/RoundedSwitch/
// RoundedCheckBox/etc, all listed in this same components/ dir) - its label pulled the
// Basic style's own default text color (OS/platform-theme-influenced, same root cause as
// the Backup page's plain Button fix earlier tonight) instead of Theme.text, so it read
// as a fixed mid-grey regardless of the app's own dark/light mode. A drop-in replacement
// for `RadioButton { ... }` - extends RadioButton directly, so every existing property
// (checked/enabled/text/onClicked) keeps working unchanged.
RadioButton {
    id: root

    indicator: Rectangle {
        implicitWidth: 20
        implicitHeight: 20
        x: root.leftPadding
        y: root.topPadding + (root.availableHeight - height) / 2
        radius: height / 2
        color: Theme.card
        border.width: 1.4
        // Real, 2026-08-10 ("the contour of the buttons when not selected...they are
        // black...not that visible") - see RoundedButton.qml's own comment on this same
        // fix: Theme.background has almost no contrast against this indicator's own
        // Theme.card fill. Theme.mutedText (WatchFacePreview.qml's own established
        // selected/unselected border pattern) instead.
        border.color: root.checked ? Theme.primary : Theme.mutedText

        Rectangle {
            anchors.centerIn: parent
            visible: root.checked
            width: 10
            height: 10
            radius: 5
            color: Theme.primary
        }
    }

    contentItem: Text {
        text: root.text
        color: root.enabled ? Theme.text : Theme.mutedText
        font.pixelSize: Theme.fontSizeBody
        leftPadding: root.indicator ? root.indicator.width + Theme.spacingSmall : 0
        verticalAlignment: Text.AlignVCenter
    }
}
