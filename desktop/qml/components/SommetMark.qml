import QtQuick

// Sommet "Summit sync" mark (shared 2026-08-29). A rounded-square device tile carrying a
// dashed teal survey/sync ring around a snow-capped amber summit - Sommet is French for
// "summit", the dashed ring is the GPS track the app plants around a route, the white cap is
// the peak. Drawn on a plain Canvas (same approach as MapView's track polyline) so it needs
// no Qt SVG module and stays crisp at any size. Geometry is identical to the raster app icon
// (tools/packaging/sommet_icon.py) - both in a 240-unit reference box. Set `size`.
Item {
    id: root
    property int size: 48
    width: size
    height: size
    onSizeChanged: canvas.requestPaint()

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var s = width / 240;
            ctx.scale(s, s);

            // rounded device tile
            var r = 54;
            ctx.fillStyle = "#14181c";
            ctx.beginPath();
            ctx.moveTo(r, 0);
            ctx.arcTo(240, 0, 240, 240, r);
            ctx.arcTo(240, 240, 0, 240, r);
            ctx.arcTo(0, 240, 0, 0, r);
            ctx.arcTo(0, 0, 240, 0, r);
            ctx.closePath();
            ctx.fill();

            // dashed survey / sync ring (behind the summit)
            ctx.strokeStyle = "#1d9e75";
            ctx.lineWidth = 7;
            ctx.lineCap = "round";
            ctx.setLineDash([11, 13]);
            ctx.beginPath();
            ctx.arc(120, 122, 78, 0, 2 * Math.PI);
            ctx.stroke();
            ctx.setLineDash([]);

            // amber summit body
            ctx.fillStyle = "#ef9f27";
            ctx.beginPath();
            ctx.moveTo(76, 166); ctx.lineTo(120, 76); ctx.lineTo(164, 166); ctx.closePath();
            ctx.fill();

            // snow cap
            ctx.fillStyle = "#f2f2ee";
            ctx.beginPath();
            ctx.moveTo(120, 76); ctx.lineTo(143, 123); ctx.lineTo(128, 116);
            ctx.lineTo(120, 124); ctx.lineTo(111, 116); ctx.lineTo(97, 123);
            ctx.closePath();
            ctx.fill();
        }
    }
}
