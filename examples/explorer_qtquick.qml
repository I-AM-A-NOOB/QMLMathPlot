// Qt Quick explorer demo: a function input field plus the neighbours that usually fight
// over events, to check that the plot coexists with them.
//
// This is the Qt Quick counterpart of examples/explorer_qtwidgets.py. What it exercises:
//   * TextField: press Enter (or the Draw button) to re-plot. The plot never joins the
//     tab-focus chain, so typing keeps focus.
//   * SplitView: drag the handle and resize; the plot fills its pane at any size. (The
//     "does the plot steal the wheel/drag from a scrollable parent?" case needs an
//     oversized plot, so it lives in tests/test_qtquick_coexistence.py and
//     tests/test_widget.py instead of in this demo.)
//   * Overlay: a translucent Item drawn over the plot. A plain Item has no pointer handlers,
//     so it does not swallow mouse events — no QtWidgets-style "transparent for mouse
//     events" flag is needed here.
//   * Log pane: focus / draw events are appended as they happen.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QmlMathPlot 1.0

ApplicationWindow {
    id: window

    width: 1200
    height: 760
    visible: true
    title: "QMLMathPlot — Qt Quick coexistence check"

    // Single source of truth for the startup expression: the field and the plot must agree.
    readonly property string initialExpression: "sin(1/x)"

    readonly property var samples: [
        "sin(x)", "sin(1/x)", "tan(x)", "log(x)", "1/x",
        "x^2", "exp(-x*x)*sin(10*x)", "asin(x)", "sqrt(x)"
    ]

    function logLine(text) {
        log.text += text + "\n";
    }

    Component.onCompleted: {
        plot.expression = window.initialExpression;
        aspectBox.currentIndex = 1;             // start at 1:1: no distortion on resize
        plot.aspect = aspectBox.currentValue;
    }

    function draw() {
        plot.expression = expressionField.text;
        logLine("draw " + JSON.stringify(expressionField.text));
    }

    header: ToolBar {
        RowLayout {
            anchors.fill: parent

            Label { text: "f(x) =" }
            TextField {
                id: expressionField
                objectName: "expressionField"
                Layout.fillWidth: true
                text: window.initialExpression
                focus: true
                placeholderText: "sympy syntax: sin(1/x) / tan(x) / log(x) …"
                onAccepted: window.draw()
            }
            Button {
                text: "Draw"
                onClicked: window.draw()
            }
            ComboBox {
                id: sampleBox
                model: window.samples
                onActivated: {
                    expressionField.text = window.samples[currentIndex];
                    window.draw();
                }
            }
            ComboBox {
                id: aspectBox
                objectName: "aspectBox"
                textRole: "text"
                valueRole: "value"
                model: [
                    { text: "Follow view", value: "view" },
                    { text: "1:1 (square)", value: 1.0 },
                    { text: "2:1", value: 2.0 },
                    { text: "1:2", value: 0.5 }
                ]
                onActivated: {
                    plot.aspect = currentValue;
                    window.logLine("aspect -> " + currentText);
                }
            }
            Button {
                text: "Reset view"
                onClicked: plot.controller.resetView()
            }
            Label {
                text: plot.error
                color: "#ff8080"
            }
        }
    }

    SplitView {
        // ApplicationWindow reserves the header/footer space inside contentItem,
        // so filling the parent is enough.
        anchors.fill: parent
        orientation: Qt.Horizontal

        MathPlot {
            id: plot
            objectName: "plot"
            SplitView.preferredWidth: 880
            SplitView.fillHeight: true
            lineWidth: 1.5

            // Overlay: no pointer handlers, so mouse events pass straight through
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: 12
                width: overlayLabel.implicitWidth + 16
                height: overlayLabel.implicitHeight + 8
                radius: 4
                color: "#22ffffff"
                border.color: "#66ffffff"

                Text {
                    id: overlayLabel
                    anchors.centerIn: parent
                    color: "#dddddd"
                    text: "Overlay Item (translucent, click-through)"
                }
            }
        }

        TextArea {
            id: log
            readOnly: true
            wrapMode: TextEdit.Wrap
            text: "Event log (focus / draw):\n"
                  + "· wheel over the plot = zoom, drag over it = pan\n"
                  + "· focus stays in the input field; the plot never joins the tab chain\n"
        }
    }

    footer: ToolBar {
        RowLayout {
            anchors.fill: parent

            Label {
                id: status
                text: "x ∈ [" + viewBounds(0).toFixed(4) + ", " + viewBounds(1).toFixed(4) + "]"
                      + "   y ∈ [" + viewBounds(2).toFixed(4) + ", " + viewBounds(3).toFixed(4) + "]"
                      + "   (drag to pan / wheel to zoom at the cursor)"
            }
            Item { Layout.fillWidth: true }
            Label { text: "plot " + plot.width.toFixed(0) + "x" + plot.height.toFixed(0) }
        }
    }

    function viewBounds(index) {
        const v = plot.view;
        return index === 0 ? v.x : index === 1 ? v.y : index === 2 ? v.z : v.w;
    }

    Connections {
        target: window
        function onActiveFocusItemChanged() {
            window.logLine("focus -> " + window.activeFocusItem);
        }
    }
}
