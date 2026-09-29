// QML 前端：片元着色器逐像素画曲线（隐式绘图），着色器由 Python 侧生成并烘成 .qsb。
//
// 与 QWidget 前端的区别只在"宿主"：那边是顶点着色器算 y + GL_LINE_STRIP 折线，
// 这边是每个像素算一次 |距离| —— 每帧代价 ∝ 像素数，与函数频率无关（振荡函数
// 不会因为混叠把帧率拖垮），代价是每像素一次 f(x) 求值。
import QtQuick
import QmlMathPlot 1.0

Item {
    id: root

    implicitWidth: 800
    implicitHeight: 600

    // sympy 语法的表达式；改这个属性会重新生成 GLSL 并烘焙 .qsb
    property alias expression: backend.expression
    // 视图：Qt.vector4d(xmin, xmax, ymin, ymax)
    property alias view: backend.view
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
        property vector4d view: backend.view
        property vector2d size: Qt.vector2d(width, height)
        property real lineWidth: root.lineWidth
        property color color: root.curveColor
        vertexShader: backend.vertexShader
        fragmentShader: backend.fragmentShader
    }


    PlotController {
        id: backend
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
            backend.panPixels(mouse.x - lastX, mouse.y - lastY, width, height);
            lastX = mouse.x;
            lastY = mouse.y;
        }
    }

    WheelHandler {
        // 光标位置为锚点；delta 传原始滚轮增量，120 = 一档。
        // 注意：Qt 6 的 QML WheelEvent 没有 position，只有 x/y（相对本 item）。
        onWheel: (event) => {
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y;
            backend.zoom(d, event.x / root.width, event.y / root.height);
            event.accepted = true;
        }
    }
}
