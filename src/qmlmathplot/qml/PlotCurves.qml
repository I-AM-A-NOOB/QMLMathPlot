// One QRhi pass for every curve. The curve list is read by the Python renderer; the
// camera and the viewport are the only per-frame QML inputs.
import QtQuick
import QmlMathPlot 1.0

Item {
    id: curvesRoot

    property Item view
    property bool cpuRenderer: false

    CurveRhiItem {
        anchors.fill: parent
        visible: !curvesRoot.cpuRenderer
        curves: curvesRoot.view.plot.curves
        camera: Qt.vector4d(curvesRoot.view.plot.camera.centre.x,
                            curvesRoot.view.plot.camera.centre.y,
                            curvesRoot.view.plot.camera.scale_x,
                            curvesRoot.view.plot.camera.scale_y)
    }

    Component {
        id: cpuCurvesComponent
        CpuCurves { view: curvesRoot.view }
    }
    Loader {
        anchors.fill: parent
        active: curvesRoot.cpuRenderer
        sourceComponent: cpuCurvesComponent
    }
}
