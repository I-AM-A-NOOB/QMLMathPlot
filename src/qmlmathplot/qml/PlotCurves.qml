// One QRhi pass for every curve. The curve list is read by the Python renderer; the
// camera and the viewport are the only per-frame QML inputs.
import QtQuick
import QmlMathPlot 1.0

Item {
    id: curvesRoot

    property Item view

    CurveRhiItem {
        anchors.fill: parent
        curves: curvesRoot.view.plot.curves
        camera: Qt.vector4d(curvesRoot.view.plot.camera.centre.x,
                            curvesRoot.view.plot.camera.centre.y,
                            curvesRoot.view.plot.camera.scale_x,
                            curvesRoot.view.plot.camera.scale_y)
    }
}
