// Legend for labelled, visible curves. It is model-driven (not host chrome) so exports include it.
import QtQuick
import QtQuick.Window

Item {
    id: legendRoot
    objectName: "plotLegend"

    property Item view

    readonly property var plot: view.plot
    readonly property real _dpr: Screen.devicePixelRatio > 0 ? Screen.devicePixelRatio : 1
    readonly property bool _right: plot.legend_position === "best"
                                   || plot.legend_position.indexOf("right") >= 0
    readonly property bool _bottom: plot.legend_position.indexOf("lower") >= 0

    function _deviceWidth(logicalWidth) {
        return Math.max(1, Math.round(logicalWidth * _dpr)) / _dpr;
    }

    Rectangle {
        id: frame
        objectName: "legendFrame"

        visible: legendRoot.plot.legend && entries.implicitHeight > 0
        x: legendRoot._right ? parent.width - width - 8 : 8
        y: legendRoot._bottom ? parent.height - height - 8 : 8
        width: entries.implicitWidth + 16
        height: entries.implicitHeight + 12
        color: legendRoot.plot.background
        opacity: 0.94
        radius: 3
        border.width: legendRoot._deviceWidth(legendRoot.plot.axis_width)
        border.color: legendRoot.plot.axis_color

        Column {
            id: entries
            x: 8
            y: 6
            spacing: 2

            Repeater {
                objectName: "legendEntries"
                model: legendRoot.plot.curves

                delegate: Row {
                    visible: model.visible && model.label !== ""
                    spacing: 6

                    Rectangle {
                        width: 18
                        height: Math.max(1, legendRoot._deviceWidth(model.lineWidth))
                        color: model.color
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        objectName: "legendLabel"
                        text: model.label
                        color: legendRoot.plot.text_color
                        font.pixelSize: legendRoot.plot.font_size
                    }
                }
            }
        }
    }
}
