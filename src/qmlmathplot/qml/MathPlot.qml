// View layer: the reusable plotting component (implicit per-pixel drawing).
//
// Usage (the app side creates the ViewModel and injects it; MVVM):
//     MathPlot { controller: myPlotController }
// It also works standalone without injection — the component brings its own PlotController.
//
// The curve is drawn by a fragment shader that decides per pixel the first-order screen-space
// distance to the curve: the per-frame cost is proportional to the pixel count and independent
// of the function's frequency (an oscillating function cannot drag the frame rate down through
// aliasing); a column whose pixel packs several oscillations instead fills the [min, max]
// envelope band (see model.FRAGMENT_TEMPLATE).
import QtQuick
import QmlMathPlot 1.0

Item {
    id: root

    implicitWidth: 800
    implicitHeight: 600

    // ViewModel (injected by the app; the component brings its own so it stays usable
    // standalone)
    property PlotController controller: PlotController {}

    property alias expression: root.controller.expression
    property alias view: root.controller.view
    property alias error: root.controller.error

    // "view" = the shape follows the widget's aspect ratio; a number keeps the ratio of the
    // y-unit to the x-unit fixed (1.0 = square units), expanding the view instead of
    // distorting it. The controller needs the size to do that.
    property alias aspect: root.controller.aspect

    property real lineWidth: 1.5
    property color curveColor: "#33ccff"
    property color backgroundColor: "#14141e"

    Component.onCompleted: root.controller.setViewport(width, height)
    onWidthChanged: root.controller.setViewport(width, height)
    onHeightChanged: root.controller.setViewport(width, height)

    Rectangle {
        anchors.fill: parent
        color: root.backgroundColor
    }

    ShaderEffect {
        id: plot
        anchors.fill: parent
        property vector4d view: root.controller.view
        property vector2d size: Qt.vector2d(width, height)
        property real lineWidth: root.lineWidth
        property color color: root.curveColor
        vertexShader: root.controller.vertexShader
        fragmentShader: root.controller.fragmentShader
    }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        // Panning must win over any ancestor Flickable/ScrollView: without this the
        // Flickable steals the grab once the drag passes its threshold, and the plot
        // only moves a few pixels before panning stops.
        preventStealing: true

        property real lastX: 0
        property real lastY: 0

        onPressed: (mouse) => {
            lastX = mouse.x;
            lastY = mouse.y;
        }
        onPositionChanged: (mouse) => {
            root.controller.panPixels(mouse.x - lastX, mouse.y - lastY, width, height);
            lastX = mouse.x;
            lastY = mouse.y;
        }
    }

    WheelHandler {
        // The cursor position is the anchor; delta is the raw wheel delta, 120 = one notch.
        // Note: Qt 6's QML WheelEvent has no position, only x/y (relative to this item).
        onWheel: (event) => {
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y;
            root.controller.zoom(d, event.x / root.width, event.y / root.height);
            event.accepted = true;
        }
    }
}
