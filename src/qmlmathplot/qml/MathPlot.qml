// View 层：可复用的绘图组件（隐式逐像素绘图）。
//
// 用法（App 侧创建 ViewModel 并注入，MVVM）：
//     MathPlot { controller: myPlotController }
// 不注入也能单独用——组件会自带一个 PlotController。
//
// 曲线由片元着色器逐像素判"到曲线的一阶屏幕空间距离"画出：每帧代价 ∝ 像素数，
// 与函数频率无关（振荡函数不会因为混叠把帧率拖垮）；一个像素里塞进多个振荡的
// 列改填 [min, max] 包络带（见 model.FRAGMENT_TEMPLATE）。
import QtQuick
import QmlMathPlot 1.0

Item {
    id: root

    implicitWidth: 800
    implicitHeight: 600

    // ViewModel（App 注入；未注入时自带一个，保证组件可独立使用）
    property PlotController controller: PlotController {}

    property alias expression: root.controller.expression
    property alias view: root.controller.view
    property alias error: root.controller.error

    property real lineWidth: 1.5
    property color curveColor: "#33ccff"
    property color backgroundColor: "#14141e"

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
        // 光标位置为锚点；delta 传原始滚轮增量，120 = 一档。
        // 注意：Qt 6 的 QML WheelEvent 没有 position，只有 x/y（相对本 item）。
        onWheel: (event) => {
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y;
            root.controller.zoom(d, event.x / root.width, event.y / root.height);
            event.accepted = true;
        }
    }
}
