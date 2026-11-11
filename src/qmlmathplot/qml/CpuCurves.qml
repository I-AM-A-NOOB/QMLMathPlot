// CPU fallback: one QPainterPath per curve. Used when RHI/compute is unavailable.
import QtQuick
import QmlMathPlot 2.0

Item {
    id: cpuRoot
    property Item view

    Repeater {
        model: cpuRoot.view.plot.curves
        delegate: Item {
            anchors.fill: parent
            visible: model.visible

            PathCurveItem {
                anchors.fill: parent
                visible: !model.implicit
                mathSource: model.mathSource
                color: model.color
                lineWidth: model.lineWidth
                camera: Qt.vector4d(cpuRoot.view.plot.camera.centre.x,
                                    cpuRoot.view.plot.camera.centre.y,
                                    cpuRoot.view.plot.camera.scale_x,
                                    cpuRoot.view.plot.camera.scale_y)
            }

            // A level set has no 1-D polyline; say so instead of leaving a silent blank.
            Text {
                visible: model.implicit
                anchors.centerIn: parent
                color: "#c0392b"
                text: "implicit curves need the GPU renderer"
            }
        }
    }
}
