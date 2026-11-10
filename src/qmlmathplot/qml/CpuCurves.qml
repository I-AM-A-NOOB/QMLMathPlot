// CPU fallback: one QPainterPath per curve. Used when RHI/compute is unavailable.
import QtQuick
import QmlMathPlot 2.0

Item {
    id: cpuRoot
    property Item view

    Repeater {
        model: cpuRoot.view.plot.curves
        delegate: PathCurveItem {
            anchors.fill: parent
            visible: model.visible
            expression: model.expression
            mathSource: model.mathSource
            color: model.color
            lineWidth: model.lineWidth
            camera: Qt.vector4d(cpuRoot.view.plot.camera.centre.x,
                                cpuRoot.view.plot.camera.centre.y,
                                cpuRoot.view.plot.camera.scale_x,
                                cpuRoot.view.plot.camera.scale_y)
        }
    }
}
