// View layer composition: background, one QRhi pass for all curves, screen-space furniture and
// pointer/keyboard input. The reusable pieces live in PlotCurves.qml, PlotFurniture.qml and
// PlotInput.qml; this file owns the model injection, the camera mapping and keyboard focus.
//
// Usage (the host creates the model and injects it; MVVM):
//     PlotView { plot: myPlot }
// It also works standalone: the component brings its own empty Plot (`defaultExpression` adds
// a curve). The view owns no state: everything it draws is read from the Plot.
import QtQuick
import QmlMathPlot 1.0

Item {
    id: root

    implicitWidth: 800
    implicitHeight: 600

    // Keyboard navigation is opt-in by focus: a click, Tab or a host focusing the QQuickWidget
    // makes this the active item, and the keys are handled only then, so a host's shortcuts
    // keep working while the plot is idle. `activeFocusOnTab` places it in QML's Tab chain;
    // the QQuickWidget is StrongFocus in the widgets chain.
    focus: true
    activeFocusOnTab: true
    Keys.onPressed: (event) => {
        if (root._handleKeyPress(event))
            event.accepted = true;
    }
    Keys.onReleased: (event) => {
        if (root._handleKeyRelease(event))
            event.accepted = true;
    }
    onActiveFocusChanged: {
        if (!activeFocus)
            root._clearHeld();
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
    property bool cpuRenderer: false

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
    // zoom shortcuts anchor at the viewport centre. Every mapping is on camera.input, so a
    // host can rebind it; an empty string disables a command.
    //
    // Held keys are tracked here rather than relying on the OS auto-repeat (which begins with
    // a ~0.5 s delay and then repeats in bursts). One frame-rate Timer advances both pan and
    // zoom by `dt / keyPanRepeatInterval` of a step, so movement is continuous and several
    // keys combine (diagonal movement is normalised). The press itself still animates one
    // step, so a tap keeps the one-step semantics.
    property var _heldKeys: ({})
    property real _lastTickMs: 0

    Timer {
        id: keyTimer
        interval: 16
        repeat: true
        onTriggered: root._keyTick()
    }

    Connections {
        target: root.plot.camera
        function onPanEnabledChanged() {
            if (!root.plot.camera.panEnabled) {
                root._dropHeld("left");
                root._dropHeld("right");
                root._dropHeld("up");
                root._dropHeld("down");
            }
        }
        function onZoomEnabledChanged() {
            if (!root.plot.camera.zoomEnabled) {
                root._dropHeld("zoomIn");
                root._dropHeld("zoomOut");
            }
        }
    }
    Connections {
        target: root.plot.camera.input
        function onKeyboardEnabledChanged() {
            if (!root.plot.camera.input.keyboardEnabled)
                root._clearHeld();
        }
    }

    // One command name for this event, or "" when it is not bound.
    function _commandFor(event, requireEnabled) {
        const camera = root.plot.camera;
        const input = camera.input;
        if (!input.keyboardEnabled)
            return "";
        if (input.matches(event.key, event.modifiers, input.panLeftShortcut))
            return (!requireEnabled || camera.panEnabled) ? "left" : "";
        if (input.matches(event.key, event.modifiers, input.panRightShortcut))
            return (!requireEnabled || camera.panEnabled) ? "right" : "";
        if (input.matches(event.key, event.modifiers, input.panUpShortcut))
            return (!requireEnabled || camera.panEnabled) ? "up" : "";
        if (input.matches(event.key, event.modifiers, input.panDownShortcut))
            return (!requireEnabled || camera.panEnabled) ? "down" : "";
        if (camera.zoomEnabled) {
            if (input.matches(event.key, event.modifiers, input.zoomInShortcut))
                return "zoomIn";
            if (input.matches(event.key, event.modifiers, input.zoomOutShortcut))
                return "zoomOut";
        }
        return "";
    }

    function _isPanCommand(command) {
        return command === "left" || command === "right"
            || command === "up" || command === "down";
    }

    function _clearHeld() {
        root._heldKeys = ({});
        keyTimer.stop();
    }

    function _dropHeld(command) {
        delete root._heldKeys[command];
        if (Object.keys(root._heldKeys).length === 0)
            keyTimer.stop();
    }

    function _ensureTimer() {
        if (!keyTimer.running) {
            root._lastTickMs = Date.now();
            keyTimer.start();
        }
    }

    // Frame-rate integration: a step per keyPanRepeatInterval, scaled by the real elapsed
    // time (a delayed frame moves farther instead of slowing the pan down).
    function _keyTick() {
        const camera = root.plot.camera;
        const input = camera.input;
        if (!input.keyboardEnabled) {
            root._clearHeld();
            return;
        }
        const now = Date.now();
        const dt = Math.min(100, Math.max(0, now - root._lastTickMs));
        root._lastTickMs = now;
        if (dt <= 0)
            return;
        const fraction = dt / Math.max(1, input.keyPanRepeatInterval);

        let dx = 0;
        let dy = 0;
        if (root._heldKeys.left)
            dx += 1;
        if (root._heldKeys.right)
            dx -= 1;
        if (root._heldKeys.up)
            dy += 1;
        if (root._heldKeys.down)
            dy -= 1;
        if ((dx !== 0 || dy !== 0) && camera.panEnabled) {
            if (dx !== 0 && dy !== 0) {
                dx *= Math.SQRT1_2;
                dy *= Math.SQRT1_2;
            }
            camera.pan_pixels(input.keyPanStep * root.width * dx * fraction,
                              input.keyPanStep * root.height * dy * fraction);
        }

        let zoom = 0;
        if (root._heldKeys.zoomIn)
            zoom += 1;
        if (root._heldKeys.zoomOut)
            zoom -= 1;
        if (zoom !== 0 && camera.zoomEnabled) {
            camera.zoom_by(120 * zoom * fraction, 0.5, 0.5, root.width, root.height);
        }
    }

    function _handleKeyPress(event) {
        const command = root._commandFor(event, true);
        if (command === "")
            return false;
        if (event.isAutoRepeat)                       // the Timer owns repeats
            return true;
        const camera = root.plot.camera;
        const input = camera.input;
        root._heldKeys[command] = true;
        root._ensureTimer();
        if (root._isPanCommand(command)) {
            // One eased step for a tap; the frame timer cancels it on the first held frame
            // and takes over continuously.
            const step = input.keyPanStep;
            if (command === "left")
                camera.animate_pan(step * root.width, 0, camera.animationDuration);
            else if (command === "right")
                camera.animate_pan(-step * root.width, 0, camera.animationDuration);
            else if (command === "up")
                camera.animate_pan(0, step * root.height, camera.animationDuration);
            else
                camera.animate_pan(0, -step * root.height, camera.animationDuration);
        } else if (command === "zoomIn") {
            camera.animate_zoom(120, 0.5, 0.5, root.width, root.height,
                                camera.animationDuration);
        } else if (command === "zoomOut") {
            camera.animate_zoom(-120, 0.5, 0.5, root.width, root.height,
                                camera.animationDuration);
        }
        return true;
    }

    function _handleKeyRelease(event) {
        const command = root._commandFor(event, false);
        if (command === "")
            return false;
        if (event.isAutoRepeat)
            return true;
        root._dropHeld(command);
        if (Object.keys(root._heldKeys).length > 0)
            root._ensureTimer();
        return true;
    }

    // The first report resolves the home view for the real size; later ones keep the scales, so
    // nothing zooms while a window or a splitter is dragged.
    Component.onCompleted: {
        if (defaultExpression !== "" && curves.rowCount() === 0)
            add_curve(defaultExpression);
        root.plot.camera.set_viewport(width, height);
    }
    onWidthChanged: root.plot.camera.set_viewport(width, height)
    onHeightChanged: root.plot.camera.set_viewport(width, height)

    Rectangle {
        anchors.fill: parent
        color: root.plot.background
    }

    PlotCurves {
        anchors.fill: parent
        view: root
        cpuRenderer: root.cpuRenderer
    }

    PlotFurniture {
        anchors.fill: parent
        view: root
    }

    PlotOverlay {
        anchors.fill: parent
        view: root
    }

    PlotLegend {
        anchors.fill: parent
        view: root
    }

    PlotInput {
        anchors.fill: parent
        view: root
    }
}
