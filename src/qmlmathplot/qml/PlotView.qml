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
import QtQuick.Window
import QmlMathPlot 1.0

Item {
    id: root

    implicitWidth: 800
    implicitHeight: 600

    // Keyboard navigation is opt-in by focus: a click (or a host focusing the QQuickWidget)
    // makes this the active item, and the keys are handled only then, so a host's shortcuts
    // keep working while the plot is idle. `focus: true` is the default *within* this scene,
    // not a grab: the QQuickWidget is still ClickFocus and does not enter the Tab chain.
    focus: true
    Keys.onPressed: (event) => {
        if (root._handleKey(event))
            event.accepted = true;
    }

    // The model (injected by the host). Without injection the component brings its own plot;
    // it adds no curve by itself. A standalone user opts into one with `defaultExpression`
    // (``PlotView { defaultExpression: "sin(x)" }``) — no hidden default, and a host that
    // injects a model or adds its own curves never races this handler.
    property Plot plot: defaultPlot
    property string defaultExpression: ""
    Plot {
        id: defaultPlot
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

    // Keyboard: arrow keys move the view (the camera moves, so the content follows), the
    // zoom shortcuts anchor at the viewport centre. Every mapping is a property on the camera,
    // so a host can rebind them; an empty string disables a command.
    function _matches(event, shortcut) {
        return root.plot.camera.matchesShortcut(event.key, event.modifiers, shortcut);
    }

    function _handleKey(event) {
        const camera = root.plot.camera;
        if (!camera.keyboardEnabled)
            return false;
        const dx = camera.keyPanStep * root.width;
        const dy = camera.keyPanStep * root.height;
        const duration = camera.animationDuration;
        if (camera.panEnabled) {
            if (_matches(event, camera.panLeftShortcut)) {
                camera.animate_pan(dx, 0, duration);
                return true;
            }
            if (_matches(event, camera.panRightShortcut)) {
                camera.animate_pan(-dx, 0, duration);
                return true;
            }
            if (_matches(event, camera.panUpShortcut)) {
                camera.animate_pan(0, dy, duration);
                return true;
            }
            if (_matches(event, camera.panDownShortcut)) {
                camera.animate_pan(0, -dy, duration);
                return true;
            }
        }
        if (camera.zoomEnabled) {
            if (_matches(event, camera.zoomInShortcut)) {
                camera.animate_zoom(120, 0.5, 0.5, root.width, root.height, duration);
                return true;
            }
            if (_matches(event, camera.zoomOutShortcut)) {
                camera.animate_zoom(-120, 0.5, 0.5, root.width, root.height, duration);
                return true;
            }
        }
        return false;
    }

    // Screen-space furniture is drawn in logical QML coordinates, but its edges must land on
    // device pixels: a 1.07-logical-px axis is 1.6 physical px at DPR 1.5, so an unsnapped
    // rectangle is rasterised as 1 or 2 px depending on the zoom (the world position drifts
    // across the pixel grid). Snap the line *center* so its edges are integral device pixels,
    // and use an integral device width. This is the axis/grid equivalent of the grid
    // hairline's half-pixel rule, generalised to a styled line of any DPR.
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

    // The first report resolves the home view for the real size; later ones keep the scales, so
    // nothing zooms while a window or a splitter is dragged.
    Component.onCompleted: {
        if (defaultExpression !== "" && curves.rowCount() === 0)
            add_curve(defaultExpression);
        root.plot.camera.setViewport(width, height);
    }
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
            ctx.lineWidth = root._deviceWidth(root.plot.grid_width);
            ctx.strokeStyle = root.plot.grid_color;
            ctx.globalAlpha = root.plot.grid_alpha;
            ctx.setLineDash(root._gridDash(root.plot.grid_style));
            ctx.beginPath();
            for (const tick of root.plot.camera.ticks_x) {
                const px = root._snapLine(root.mapToScreen(tick[0], 0).x, ctx.lineWidth);
                ctx.moveTo(px, 0);
                ctx.lineTo(px, height);
            }
            for (const tick of root.plot.camera.ticks_y) {
                const py = root._snapLine(root.mapToScreen(0, tick[0]).y, ctx.lineWidth);
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
        readonly property real axisThickness: root._deviceWidth(root.plot.axis_width)

        Rectangle {
            visible: furniture.atZero
            x: 0
            width: parent.width
            height: furniture.axisThickness
            y: root._snapLine(furniture.xAxisY, furniture.axisThickness) - height / 2
            color: root.plot.axis_color
        }
        Rectangle {
            visible: furniture.atZero
            x: root._snapLine(furniture.yAxisX, furniture.axisThickness) - width / 2
            y: 0
            width: furniture.axisThickness
            height: parent.height
            color: root.plot.axis_color
        }

        Repeater {
            model: root.plot.camera.ticks_x

            // The mark and the label ride the x axis; both are clamped so they stay on screen
            // when the axis is pinned to an edge (a label may never leave the item).
            delegate: Item {
                required property var modelData
                readonly property real markWidth: root._deviceWidth(1)

                x: root._snapLine(root.mapToScreen(modelData[0], 0).x, markWidth) - markWidth / 2
                width: markWidth
                height: furniture.height

                Rectangle {
                    y: Math.max(0, Math.min(furniture.xAxisY, furniture.height - height))
                    width: markWidth
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
                readonly property real markHeight: root._deviceWidth(1)

                x: 0
                y: root._snapLine(root.mapToScreen(0, modelData[0]).y, markHeight) - markHeight / 2
                width: furniture.width
                height: markHeight

                Rectangle {
                    x: Math.max(0, Math.min(furniture.yAxisX - width, furniture.width - width))
                    width: root.plot.tick_length
                    height: markHeight
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

    // Axis labels are the Matplotlib vocabulary, not a second furniture system: plain Text
    // in the plot's text colour / font size, inside the item so they never affect its size.
    Text {
        objectName: "xlabelText"
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 4

        text: root.plot.xlabel
        visible: text !== ""
        color: root.plot.text_color
        font.pixelSize: root.plot.font_size
    }

    Text {
        objectName: "ylabelText"
        text: root.plot.ylabel
        visible: text !== ""
        color: root.plot.text_color
        font.pixelSize: root.plot.font_size
        rotation: -90
        transformOrigin: Item.Center
        // Position the *rotated* bounding box: its visual width is `height`.
        x: 4 - width / 2 + height / 2
        y: (parent.height - height) / 2
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        // Panning must win over any ancestor Flickable/ScrollView: without this the Flickable
        // steals the grab once the drag passes its threshold, and the plot only moves a few
        // pixels before panning stops.
        preventStealing: true

        property real lastX: 0
        property real lastY: 0

        onPressed: (mouse) => {
            root.forceActiveFocus();
            root.plot.camera.stop_animation();
            lastX = mouse.x;
            lastY = mouse.y;
        }
        onPositionChanged: (mouse) => {
            if (!root.plot.camera.panEnabled)
                return;
            root.plot.camera.pan_pixels(mouse.x - lastX, mouse.y - lastY);
            lastX = mouse.x;
            lastY = mouse.y;
        }
    }

    // Touch: one finger pans, two fingers pinch (scale) and pan together. Direct
    // manipulation is deliberately not animated: the fingers are the animation. The centroid
    // is kept under the fingers, so a pinch that also drifts behaves like a map gesture.
    MultiPointTouchArea {
        id: touchArea
        anchors.fill: parent
        minimumTouchPoints: 1
        maximumTouchPoints: 2
        mouseEnabled: false
        enabled: root.plot.camera.panEnabled || root.plot.camera.zoomEnabled
        touchPoints: [TouchPoint {}, TouchPoint {}]

        property int lastCount: 0
        property real lastCx: 0
        property real lastCy: 0
        property real lastSpread: 0

        function _centroidX(points) {
            let sum = 0;
            for (let i = 0; i < points.length; ++i)
                sum += points[i].x;
            return points.length > 0 ? sum / points.length : 0;
        }

        function _centroidY(points) {
            let sum = 0;
            for (let i = 0; i < points.length; ++i)
                sum += points[i].y;
            return points.length > 0 ? sum / points.length : 0;
        }

        function _spread(points) {
            if (points.length < 2)
                return 0;
            const dx = points[0].x - points[1].x;
            const dy = points[0].y - points[1].y;
            return Math.sqrt(dx * dx + dy * dy);
        }

        function _baseline(points) {
            lastCount = points.length;
            lastCx = _centroidX(points);
            lastCy = _centroidY(points);
            lastSpread = _spread(points);
        }

        onPressed: (points) => {
            root.forceActiveFocus();
            root.plot.camera.stop_animation();
            _baseline(points);
        }
        onUpdated: (points) => {
            if (points.length === 0 || points.length !== lastCount) {
                _baseline(points);
                return;
            }
            const camera = root.plot.camera;
            const cx = _centroidX(points);
            const cy = _centroidY(points);
            if (points.length >= 2 && camera.zoomEnabled && root.width > 0 && root.height > 0) {
                const spread = _spread(points);
                if (lastSpread > 0 && spread > 0) {
                    camera.zoom_at(spread / lastSpread, lastCx / root.width,
                                   lastCy / root.height, root.width, root.height);
                }
            }
            if (camera.panEnabled && (cx !== lastCx || cy !== lastCy))
                camera.pan_pixels(cx - lastCx, cy - lastCy);
            lastCx = cx;
            lastCy = cy;
            lastSpread = _spread(points);
        }
        onReleased: (points) => {
            lastCount = 0;
        }
        onCanceled: (points) => {
            lastCount = 0;
        }
    }

    WheelHandler {
        enabled: root.plot.camera.zoomEnabled

        // The cursor position is the anchor; delta is the raw wheel delta, 120 = one notch.
        // Note: Qt 6's QML WheelEvent has no position, only x/y (relative to this item).
        onWheel: (event) => {
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y;
            root.forceActiveFocus();
            root.plot.camera.animate_zoom(d, event.x / root.width, event.y / root.height,
                                          root.width, root.height,
                                          root.plot.camera.animationDuration);
            event.accepted = true;
        }
    }
}
