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
    // zoom shortcuts anchor at the viewport centre. Every mapping is on camera.input, so a
    // host can rebind it; an empty string disables a command.
    function _matches(event, shortcut) {
        return root.plot.camera.input.matches(event.key, event.modifiers, shortcut);
    }

    function _handleKey(event) {
        const camera = root.plot.camera;
        const input = camera.input;
        if (!input.keyboardEnabled)
            return false;
        const dx = input.keyPanStep * root.width;
        const dy = input.keyPanStep * root.height;
        const duration = camera.animationDuration;
        if (camera.panEnabled) {
            if (_matches(event, input.panLeftShortcut)) {
                camera.animate_pan(dx, 0, duration);
                return true;
            }
            if (_matches(event, input.panRightShortcut)) {
                camera.animate_pan(-dx, 0, duration);
                return true;
            }
            if (_matches(event, input.panUpShortcut)) {
                camera.animate_pan(0, dy, duration);
                return true;
            }
            if (_matches(event, input.panDownShortcut)) {
                camera.animate_pan(0, -dy, duration);
                return true;
            }
        }
        if (camera.zoomEnabled) {
            if (_matches(event, input.zoomInShortcut)) {
                camera.animate_zoom(120, 0.5, 0.5, root.width, root.height, duration);
                return true;
            }
            if (_matches(event, input.zoomOutShortcut)) {
                camera.animate_zoom(-120, 0.5, 0.5, root.width, root.height, duration);
                return true;
            }
        }
        return false;
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
