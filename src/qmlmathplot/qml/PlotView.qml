// View layer: the plot on screen — background, grid, ticks and title in QML, one fragment
// shader pass per curve.
//
// Usage (the host creates the model and injects it; MVVM):
//     PlotView { plot: myPlot }
// It also works standalone: the component brings its own Plot and one sin(x) curve.
//
// The view owns no state: everything it draws is read from the Plot (the camera for the
// mapping, the curves model for the passes, the style properties for the dressing). Sizes are
// reported to the camera for the derived ranges only — the shader derives its own mapping from
// the camera and the item size, so drawing never depends on a size report arriving in order.
import QtQuick
import QmlMathPlot 1.0

Item {
    id: root

    implicitWidth: 800
    implicitHeight: 600

    // The model (injected by the host). Without injection the component brings its own plot
    // with one sin(x) curve, so it is usable standalone; a host that adds curves itself keeps
    // this handler from firing (it only acts on an empty model).
    property Plot plot: defaultPlot
    Plot {
        id: defaultPlot

        Component.onCompleted: {
            if (curves.rowCount() === 0)
                add_curve("sin(x)");
        }
    }

    // Forwarders, so a host can drive the component without knowing where the knob lives:
    // `view.camera.aspect = 1.0`, `view.curves.add_curve("tan(x)")`. These are plain
    // properties rather than QML aliases simply because an alias cannot reach through two
    // objects (the plot is an injected property, not an id).
    property QtObject camera: root.plot.camera
    property QtObject curves: root.plot.curves

    // World <-> screen. The camera maps the centre to the middle of the item, so screen x grows
    // right and screen y grows down (vUV is top-down in the shader too).
    function mapToScreen(x, y) {
        const camera = root.plot.camera;
        return Qt.point((x - camera.centre.x) / camera.scale_x + width / 2,
                        (camera.centre.y - y) / camera.scale_y + height / 2);
    }

    function mapFromScreen(point) {
        const camera = root.plot.camera;
        return Qt.point(camera.centre.x + (point.x - width / 2) * camera.scale_x,
                        camera.centre.y - (point.y - height / 2) * camera.scale_y);
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

    // The first report resolves the home view for the real size; later ones keep the scales, so
    // nothing zooms while a window or a splitter is dragged.
    Component.onCompleted: root.plot.camera.setViewport(width, height)
    onWidthChanged: root.plot.camera.setViewport(width, height)
    onHeightChanged: root.plot.camera.setViewport(width, height)

    Rectangle {
        anchors.fill: parent
        color: root.plot.background
    }

    // Grid: one line per tick, straight from the camera's tick values. Regenerated on camera
    // and style changes only — never per frame.
    Canvas {
        id: gridCanvas
        anchors.fill: parent
        visible: root.plot.grid

        onPaint: {
            const ctx = getContext("2d");
            ctx.clearRect(0, 0, width, height);
            ctx.lineWidth = root.plot.grid_width;
            ctx.strokeStyle = root.plot.grid_color;
            ctx.globalAlpha = root.plot.grid_alpha;
            ctx.setLineDash(root._gridDash(root.plot.grid_style));
            ctx.beginPath();
            for (const tick of root.plot.camera.ticks_x) {
                const px = Math.round(root.mapToScreen(tick[0], 0).x) + 0.5;
                ctx.moveTo(px, 0);
                ctx.lineTo(px, height);
            }
            for (const tick of root.plot.camera.ticks_y) {
                const py = Math.round(root.mapToScreen(0, tick[0]).y) + 0.5;
                ctx.moveTo(0, py);
                ctx.lineTo(width, py);
            }
            ctx.stroke();
        }

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        Connections {
            target: root.plot.camera
            function onViewChanged() { gridCanvas.requestPaint(); }
            function onTicksChanged() { gridCanvas.requestPaint(); }
        }
        Connections {
            target: root.plot
            function onGridChanged() { gridCanvas.requestPaint(); }
            function onGridColorChanged() { gridCanvas.requestPaint(); }
            function onGridWidthChanged() { gridCanvas.requestPaint(); }
            function onGridAlphaChanged() { gridCanvas.requestPaint(); }
            function onGridStyleChanged() { gridCanvas.requestPaint(); }
        }
    }

    // Curves: one full-screen fragment pass each. The expression lives in the shader, so a
    // change swaps the .qsb and costs one pass.
    Repeater {
        model: root.plot.curves

        delegate: ShaderEffect {
            anchors.fill: parent
            visible: model.visible

            property vector4d camera: Qt.vector4d(root.plot.camera.centre.x,
                                                  root.plot.camera.centre.y,
                                                  root.plot.camera.scale_x,
                                                  root.plot.camera.scale_y)
            property vector2d size: Qt.vector2d(root.width, root.height)
            property real lineWidth: model.lineWidth
            property color color: model.color

            vertexShader: model.vertexShader
            fragmentShader: model.fragmentShader
        }
    }

    // Ticks, labels, axes and title: screen-space furniture, so plain items.
    Item {
        id: furniture

        anchors.fill: parent
        visible: root.plot.ticks_visible

        // The axes are the lines through world (0,0). When 0 is off screen the axis sticks to
        // the nearest edge instead, so its labels are never lost (the grid stays where it is).
        // "edge" pins both axes to the item's edges and draws no axis line: the old behaviour.
        readonly property bool atZero: root.plot.axes_position === "zero"
        readonly property real xAxisY: atZero
                                       ? Math.max(0, Math.min(height, root.mapToScreen(0, 0).y))
                                       : height
        readonly property real yAxisX: atZero
                                       ? Math.max(0, Math.min(width, root.mapToScreen(0, 0).x))
                                       : 0

        Rectangle {
            visible: furniture.atZero
            x: 0
            width: parent.width
            height: root.plot.axis_width
            y: furniture.xAxisY - height / 2
            color: root.plot.axis_color
        }
        Rectangle {
            visible: furniture.atZero
            x: furniture.yAxisX - width / 2
            y: 0
            width: root.plot.axis_width
            height: parent.height
            color: root.plot.axis_color
        }

        Repeater {
            model: root.plot.camera.ticks_x

            // The mark and the label ride the x axis; both are clamped so they stay on screen
            // when the axis is pinned to an edge (a label may never leave the item).
            delegate: Item {
                required property var modelData

                x: Math.round(root.mapToScreen(modelData[0], 0).x)
                width: 1
                height: furniture.height

                Rectangle {
                    y: Math.max(0, Math.min(furniture.xAxisY, furniture.height - height))
                    width: 1
                    height: root.plot.tick_length
                    color: root.plot.tick_color
                }
                Text {
                    text: modelData[1]
                    color: root.plot.text_color
                    font.pixelSize: root.plot.tick_font_size
                    // centred on the tick, but never half off the item
                    x: Math.max(-parent.x, Math.min(-Math.round(width / 2),
                                                    furniture.width - width - parent.x))
                    y: Math.max(0, Math.min(furniture.xAxisY + root.plot.tick_length + 3,
                                            furniture.height - height))
                }
            }
        }

        Repeater {
            model: root.plot.camera.ticks_y

            delegate: Item {
                required property var modelData

                x: 0
                y: Math.round(root.mapToScreen(0, modelData[0]).y)
                width: furniture.width
                height: 1

                Rectangle {
                    x: Math.max(0, Math.min(furniture.yAxisX - width, furniture.width - width))
                    width: root.plot.tick_length
                    height: 1
                    color: root.plot.tick_color
                }
                Text {
                    text: modelData[1]
                    color: root.plot.text_color
                    font.pixelSize: root.plot.tick_font_size
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

        text: root.plot.title
        visible: text !== ""
        color: root.plot.title_color
        font.pixelSize: root.plot.title_font_size
        font.bold: root.plot.title_bold
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        enabled: root.plot.camera.panEnabled
        // Panning must win over any ancestor Flickable/ScrollView: without this the Flickable
        // steals the grab once the drag passes its threshold, and the plot only moves a few
        // pixels before panning stops.
        preventStealing: true

        property real lastX: 0
        property real lastY: 0

        onPressed: (mouse) => {
            lastX = mouse.x;
            lastY = mouse.y;
        }
        onPositionChanged: (mouse) => {
            root.plot.camera.pan_pixels(mouse.x - lastX, mouse.y - lastY);
            lastX = mouse.x;
            lastY = mouse.y;
        }
    }

    WheelHandler {
        enabled: root.plot.camera.zoomEnabled

        // The cursor position is the anchor; delta is the raw wheel delta, 120 = one notch.
        // Note: Qt 6's QML WheelEvent has no position, only x/y (relative to this item).
        onWheel: (event) => {
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y;
            root.plot.camera.zoom_by(d, event.x / root.width, event.y / root.height,
                                     root.width, root.height);
            event.accepted = true;
        }
    }
}
