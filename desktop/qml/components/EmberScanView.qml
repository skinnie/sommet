import QtQuick
import QtCore
import QtMultimedia
import AmbitApp

// Live webcam preview that reads a food's barcode for Ember's meal search (André, 2026-09-27:
// "everybody has a webcam. go"; issue #20). BarcodeScanner (C++, ZXing-C++) decodes the frames;
// detected(code) fires once two frames agree. Only in builds with Qt Multimedia - the meal
// dialog loads this file only when EmberFoodService.webcamScanAvailable.
Item {
    id: root
    signal detected(string code)
    implicitHeight: 270

    // macOS asks the user once (Info.plist NSCameraUsageDescription); Linux/Windows grant at once.
    CameraPermission {
        id: permission
        Component.onCompleted: if (status !== Qt.PermissionStatus.Granted) request()
    }
    MediaDevices { id: devices }

    readonly property bool _granted: permission.status === Qt.PermissionStatus.Granted
    readonly property bool _haveCamera: devices.videoInputs.length > 0

    CaptureSession {
        camera: Camera {
            id: camera
            cameraDevice: devices.defaultVideoInput
            active: root._granted && root._haveCamera && root.visible
            // Sharpest mode up to 720p: EAN bars need resolution more than frame rate, and
            // bigger frames only slow the decode down.
            onCameraDeviceChanged: {
                let best = null
                for (const f of cameraDevice.videoFormats) {
                    if (f.resolution.height > 720 || f.maxFrameRate < 10) continue
                    if (!best || f.resolution.width * f.resolution.height > best.resolution.width * best.resolution.height)
                        best = f
                }
                if (best) cameraFormat = best
            }
        }
        videoOutput: preview
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: "black"
        clip: true

        VideoOutput {
            id: preview
            anchors.fill: parent
            fillMode: VideoOutput.PreserveAspectCrop
        }
        // Aim here: a wide box the size a barcode should fill.
        Rectangle {
            visible: camera.active
            anchors.centerIn: parent
            width: parent.width * 0.7; height: parent.height * 0.4
            color: "transparent"; radius: 6
            border.color: Theme.success; border.width: 2
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom; anchors.bottomMargin: Theme.spacingSmall
            width: parent.width - Theme.spacingMedium * 2
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
            color: "white"; font.pixelSize: Theme.fontSizeCaption
            text: !root._haveCamera ? qsTr("No camera found.")
                : !root._granted ? qsTr("Allow camera access to scan a barcode.")
                : camera.errorString.length > 0 ? camera.errorString
                : qsTr("Hold the barcode in the box, 15–25 cm from the camera, in good light.")
        }
    }

    BarcodeScanner {
        videoSink: preview.videoSink
        active: camera.active
        onDetected: (code) => root.detected(code)
    }
}
