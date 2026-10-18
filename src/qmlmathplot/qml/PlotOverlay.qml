// Model-driven annotation layer: text/annotate labels and horizontal/vertical guides. It is
// part of the scene (not a host overlay), so the offscreen export renders the same pixels.
import QtQuick
import QtQuick.Window

Item {
    id: overlayRoot

    property Item view

    readonly property var plot: view.plot
    readonly property real _dpr: Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1

    function _deviceWidth(logicalWidth) {
        return Math.max(1, Math.round(logicalWidth * _dpr)) / _dpr;
    }

    function _snapLine(center, logicalWidth) {
        const wd = Math.max(1, Math.round(logicalWidth * _dpr));
        return (Math.round(center * _dpr - wd / 2) + wd / 2) / _dpr;
    }

    function _dash(style) {
        if (style === "--")
            return [6, 4];
        if (style === ":")
            return [1, 3];
        if (style === "-.")
            return [6, 3, 1, 3];
        return [];
    }

    // Guides are painted as one Canvas so dashed styles look the same as the grid's.
    Canvas {
        id: guideCanvas
        anchors.fill: parent

        onPaint: {
            const ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            for (let i = 0; i < overlayRoot.plot.guides.rowCount(); ++i) {
                const guide = overlayRoot.plot.guides.at(i);
                if (!guide.visible)
                    continue;
                ctx.strokeStyle = guide.color;
                ctx.lineWidth = overlayRoot._deviceWidth(guide.width);
                ctx.setLineDash(overlayRoot._dash(guide.style));
                ctx.beginPath();
                if (guide.kind === "h") {
                    const py = overlayRoot._snapLine(
                        overlayRoot.view.mapToScreen(0, guide.value).y, ctx.lineWidth);
                    ctx.moveTo(0, py);
                    ctx.lineTo(width, py);
                } else {
                    const px = overlayRoot._snapLine(
                        overlayRoot.view.mapToScreen(guide.value, 0).x, ctx.lineWidth);
                    ctx.moveTo(px, 0);
                    ctx.lineTo(px, height);
                }
                ctx.stroke();
            }
        }

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        Connections {
            target: overlayRoot.plot.camera
            function onViewChanged() { guideCanvas.requestPaint(); }
        }
        Connections {
            target: overlayRoot.plot.guides
            function onDataChanged() { guideCanvas.requestPaint(); }
            function onRowsInserted() { guideCanvas.requestPaint(); }
            function onRowsRemoved() { guideCanvas.requestPaint(); }
            function onModelReset() { guideCanvas.requestPaint(); }
        }
    }

    Repeater {
        model: overlayRoot.plot.annotations

        delegate: Item {
            readonly property point anchor: overlayRoot.view.mapToScreen(model.x, model.y)

            x: anchor.x + model.dx
            y: anchor.y + model.dy
            width: label.implicitWidth
            height: label.implicitHeight
            visible: model.visible

            // From the label's origin back to the world anchor.
            Rectangle {
                visible: model.arrow && (model.dx !== 0 || model.dy !== 0)
                x: -model.dx
                y: -model.dy
                width: Math.sqrt(model.dx * model.dx + model.dy * model.dy)
                height: 1
                color: model.color
                transformOrigin: Item.Left
                rotation: Math.atan2(model.dy, model.dx) * 180 / Math.PI
            }
            Text {
                id: label
                text: model.text
                color: model.color
                font.pixelSize: model.fontSize
            }
        }
    }
}
