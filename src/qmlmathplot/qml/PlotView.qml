// View layer composition: background, one shader pass per curve, screen-space furniture and
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

    // Keyboard navigation is opt-in by focus: a click (or a host focusing the QQuickWidget)
    // makes this the active item, and the keys are handled only then, so a host's shortcuts
    // keep working while the plot is idle. `focus: true` is the default *within* this scene,
    // not a grab: the QQuickWidget is still ClickFocus and does not enter the Tab chain.
    focus: true
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
            root._clearPan();
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
    // zoom shortcuts anchor at the viewport centre. Every mapping is on camera.input, so a
    // host can rebind it; an empty string disables a command.
    //
    // Held keys are tracked here rather than relying on the OS auto-repeat (which begins with
    // a ~0.5 s delay and then repeats in bursts): a Timer moves once per interval while any
    // direction is held, and the directions combine (diagonal movement is normalised). The
    // first press still moves immediately, so a tap moves exactly one step.
    property var _heldPan: ({})

    Timer {
        id: panTimer
        interval: root.plot.camera.input.keyPanRepeatInterval
        repeat: true
        onTriggered: root._panTick()
    }

    Connections {
        target: root.plot.camera
        function onPanEnabledChanged() {
            if (!root.plot.camera.panEnabled)
                root._clearPan();
        }
    }
    Connections {
        target: root.plot.camera.input
        function onKeyboardEnabledChanged() {
            if (!root.plot.camera.input.keyboardEnabled)
                root._clearPan();
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

    function _clearPan() {
        root._heldPan = ({});
        panTimer.stop();
    }

    function _panTick() {
        const camera = root.plot.camera;
        const input = camera.input;
        if (!input.keyboardEnabled || !camera.panEnabled) {
            root._clearPan();
            return;
        }
        let dx = 0;
        let dy = 0;
        if (root._heldPan.left)
            dx += 1;
        if (root._heldPan.right)
            dx -= 1;
        if (root._heldPan.up)
            dy += 1;
        if (root._heldPan.down)
            dy -= 1;
        if (dx === 0 && dy === 0) {
            panTimer.stop();
            return;
        }
        if (dx !== 0 && dy !== 0) {
            dx *= Math.SQRT1_2;                       // diagonal speed = straight speed
            dy *= Math.SQRT1_2;
        }
        camera.animate_pan(input.keyPanStep * root.width * dx,
                           input.keyPanStep * root.height * dy,
                           camera.animationDuration);
    }

    function _handleKeyPress(event) {
        const command = root._commandFor(event, true);
        if (command === "")
            return false;
        if (event.isAutoRepeat)                       // the Timer owns repeats
            return true;
        const camera = root.plot.camera;
        const input = camera.input;
        if (root._isPanCommand(command)) {
            root._heldPan[command] = true;
            if (!panTimer.running)
                panTimer.start();
            root._panTick();                          // immediate first step (tap semantics)
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
        if (root._isPanCommand(command)) {
            delete root._heldPan[command];
            if (Object.keys(root._heldPan).length === 0)
                panTimer.stop();
        }
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
    }

    PlotFurniture {
        anchors.fill: parent
        view: root
    }

    PlotInput {
        anchors.fill: parent
        view: root
    }
}
