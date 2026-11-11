// One QRhi pass for every curve. The curve list is read by the Python renderer; the
// camera and the viewport are the only per-frame QML inputs.
import QtQuick
import QmlMathPlot 2.0

Item {
    id: curvesRoot

    property Item view
    property string renderMode: "gpu"

    CurveRhiItem {
        anchors.fill: parent
        visible: curvesRoot.renderMode !== "cpu"
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
        active: curvesRoot.renderMode === "cpu"
        sourceComponent: cpuCurvesComponent
    }
}
