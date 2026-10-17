// One full-screen fragment pass per curve. The expression lives in the shader, so a change
// swaps the .qsb and costs one pass; the camera uniform is the only per-frame input.
import QtQuick

Item {
    id: curvesRoot

    property Item view

    Repeater {
        model: curvesRoot.view.plot.curves

        delegate: ShaderEffect {
            anchors.fill: parent
            visible: model.visible

            property vector4d camera: Qt.vector4d(curvesRoot.view.plot.camera.centre.x,
                                                  curvesRoot.view.plot.camera.centre.y,
                                                  curvesRoot.view.plot.camera.scale_x,
                                                  curvesRoot.view.plot.camera.scale_y)
            property vector2d size: Qt.vector2d(curvesRoot.width, curvesRoot.height)
            property real lineWidth: model.lineWidth
            property color color: model.color

            vertexShader: model.vertexShader
            fragmentShader: model.fragmentShader
        }
    }
}
