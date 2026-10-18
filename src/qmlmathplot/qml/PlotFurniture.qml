// Screen-space furniture: grid, axes, ticks, title and axis labels. Everything is drawn in
// logical QML coordinates, but line edges snap to *device* pixels (see _snapLine).
import QtQuick
import QtQuick.Window

Item {
    id: furnitureRoot

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

    function _gridDash(style) {
        if (style === "--")
            return [6, 4];
        if (style === ":")
            return [1, 3];
        if (style === "-.")
            return [6, 3, 1, 3];
        return [];
    }

    // Grid: one line per tick, straight from the camera's tick values. Repainted when the
    // view moves (positions) or the grid style changes — the tick *values* are cached by the
    // camera and only rebuild the models when they change.
    Canvas {
        id: gridCanvas
        anchors.fill: parent
        visible: furnitureRoot.plot.grid

        onPaint: {
            const ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            ctx.lineWidth = furnitureRoot._deviceWidth(furnitureRoot.plot.grid_width);
            ctx.strokeStyle = furnitureRoot.plot.grid_color;
            ctx.globalAlpha = furnitureRoot.plot.grid_alpha;
            ctx.setLineDash(furnitureRoot._gridDash(furnitureRoot.plot.grid_style));
            ctx.beginPath();
            for (const tick of furnitureRoot.plot.camera.ticks_x) {
                const px = furnitureRoot._snapLine(
                    furnitureRoot.view.mapToScreen(tick[0], 0).x, ctx.lineWidth);
                ctx.moveTo(px, 0);
                ctx.lineTo(px, height);
            }
            for (const tick of furnitureRoot.plot.camera.ticks_y) {
                const py = furnitureRoot._snapLine(
                    furnitureRoot.view.mapToScreen(0, tick[0]).y, ctx.lineWidth);
                ctx.moveTo(0, py);
                ctx.lineTo(width, py);
            }
            ctx.stroke();
        }

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        Connections {
            target: furnitureRoot.plot.camera
            function onViewChanged() { gridCanvas.requestPaint(); }
        }
        Connections {
            target: furnitureRoot.plot
            function onGridChanged() { gridCanvas.requestPaint(); }
            function onGridColorChanged() { gridCanvas.requestPaint(); }
            function onGridWidthChanged() { gridCanvas.requestPaint(); }
            function onGridAlphaChanged() { gridCanvas.requestPaint(); }
            function onGridStyleChanged() { gridCanvas.requestPaint(); }
        }
    }

    // Ticks, labels and axes: screen-space items.
    Item {
        id: furniture

        anchors.fill: parent
        visible: furnitureRoot.plot.ticks_visible

        // The axes are the lines through world (0,0) in "zero" mode. When 0 is off screen the
        // axis sticks to the nearest edge instead, so its labels are never lost (the grid
        // stays where it is). "edge" pins both axes to the item's edges and draws no axis
        // line; "box" draws the four Matplotlib-style spines around the viewport.
        readonly property bool atZero: furnitureRoot.plot.axes_position === "zero"
        readonly property bool boxed: furnitureRoot.plot.axes_position === "box"
        readonly property real xAxisY: atZero
                                       ? Math.max(0, Math.min(height,
                                           furnitureRoot.view.mapToScreen(0, 0).y))
                                       : height
        readonly property real yAxisX: atZero
                                       ? Math.max(0, Math.min(width,
                                           furnitureRoot.view.mapToScreen(0, 0).x))
                                       : 0
        readonly property real axisThickness:
            furnitureRoot._deviceWidth(furnitureRoot.plot.axis_width)

        Rectangle {
            visible: furniture.atZero
            x: 0
            width: parent.width
            height: furniture.axisThickness
            y: furnitureRoot._snapLine(furniture.xAxisY, furniture.axisThickness) - height / 2
            color: furnitureRoot.plot.axis_color
        }
        Rectangle {
            visible: furniture.atZero
            x: furnitureRoot._snapLine(furniture.yAxisX, furniture.axisThickness) - width / 2
            y: 0
            width: furniture.axisThickness
            height: parent.height
            color: furnitureRoot.plot.axis_color
        }

        // Matplotlib-style box: four spines around the viewport, with the ticks/labels still
        // on the bottom and left edges (the same delegates below). The lines sit inside the
        // item so the offscreen export captures them without clipping.
        Rectangle {
            visible: furniture.boxed
            x: 0
            y: 0
            width: parent.width
            height: furniture.axisThickness
            color: furnitureRoot.plot.axis_color
        }
        Rectangle {
            visible: furniture.boxed
            x: 0
            y: parent.height - height
            width: parent.width
            height: furniture.axisThickness
            color: furnitureRoot.plot.axis_color
        }
        Rectangle {
            visible: furniture.boxed
            x: 0
            y: 0
            width: furniture.axisThickness
            height: parent.height
            color: furnitureRoot.plot.axis_color
        }
        Rectangle {
            visible: furniture.boxed
            x: parent.width - width
            y: 0
            width: furniture.axisThickness
            height: parent.height
            color: furnitureRoot.plot.axis_color
        }

        Repeater {
            model: furnitureRoot.plot.camera.ticks_x

            // The mark and the label ride the x axis; both are clamped so they stay on screen
            // when the axis is pinned to an edge (a label may never leave the item).
            delegate: Item {
                required property var modelData
                readonly property real markWidth: furnitureRoot._deviceWidth(1)

                x: furnitureRoot._snapLine(
                       furnitureRoot.view.mapToScreen(modelData[0], 0).x, markWidth)
                   - markWidth / 2
                width: markWidth
                height: furniture.height

                Rectangle {
                    y: Math.max(0, Math.min(furniture.xAxisY, furniture.height - height))
                    width: markWidth
                    height: furnitureRoot.plot.tick_length
                    color: furnitureRoot.plot.tick_color
                }
                Text {
                    text: modelData[1]
                    color: furnitureRoot.plot.text_color
                    font.pixelSize: furnitureRoot.plot.tick_font_size
                    // centred on the tick, but never half off the item
                    x: Math.max(-parent.x, Math.min(-Math.round(width / 2),
                                                    furniture.width - width - parent.x))
                    y: Math.max(0, Math.min(furniture.xAxisY
                                            + furnitureRoot.plot.tick_length + 3,
                                            furniture.height - height))
                }
            }
        }

        Repeater {
            model: furnitureRoot.plot.camera.ticks_y

            delegate: Item {
                required property var modelData
                readonly property real markHeight: furnitureRoot._deviceWidth(1)

                x: 0
                y: furnitureRoot._snapLine(
                       furnitureRoot.view.mapToScreen(0, modelData[0]).y, markHeight)
                   - markHeight / 2
                width: furniture.width
                height: markHeight

                Rectangle {
                    x: Math.max(0, Math.min(furniture.yAxisX - width,
                                            furniture.width - width))
                    width: furnitureRoot.plot.tick_length
                    height: markHeight
                    color: furnitureRoot.plot.tick_color
                }
                Text {
                    text: modelData[1]
                    color: furnitureRoot.plot.text_color
                    font.pixelSize: furnitureRoot.plot.tick_font_size
                    x: Math.max(3, Math.min(furniture.yAxisX - width - 3,
                                            furniture.width - width - 3))
                    // centred on the tick, but never half off the item
                    y: Math.max(-parent.y, Math.min(-Math.round(height / 2),
                                                    furniture.height - height - parent.y))
                }
            }
        }
    }

    Text {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: 6

        text: furnitureRoot.plot.title
        visible: text !== ""
        color: furnitureRoot.plot.title_color
        font.pixelSize: furnitureRoot.plot.title_font_size
        font.bold: furnitureRoot.plot.title_bold
    }

    // Axis labels are the Matplotlib vocabulary, not a second furniture system: plain Text
    // in the plot's text colour / font size, inside the item so they never affect its size.
    Text {
        objectName: "xlabelText"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 4

        text: furnitureRoot.plot.xlabel
        visible: text !== ""
        color: furnitureRoot.plot.text_color
        font.pixelSize: furnitureRoot.plot.font_size
    }

    Text {
        objectName: "ylabelText"
        text: furnitureRoot.plot.ylabel
        visible: text !== ""
        color: furnitureRoot.plot.text_color
        font.pixelSize: furnitureRoot.plot.font_size
        rotation: -90
        transformOrigin: Item.Center
        // Position the *rotated* bounding box: its visual width is `height`.
        x: 4 - width / 2 + height / 2
        y: (parent.height - height) / 2
    }
}
