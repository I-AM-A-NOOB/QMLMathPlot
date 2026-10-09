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
        // The app side owns the model: it adds its own curve (the component's own default
        // curve is only added when nothing did, so this one wins).
        view.plot.add_curve(window.initialExpression);
        aspectBox.currentIndex = 1;             // start at 1:1: no distortion on resize
        view.plot.camera.aspect = aspectBox.currentValue;
    }

    function draw() {
        const curve = view.plot.curves.at(0);
        curve.expression = expressionField.text;
        errorLabel.text = curve.error;
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
                    { text: "Auto (widget shape)", value: "auto" },
                    { text: "1:1 (square)", value: 1.0 },
                    { text: "2:1", value: 2.0 },
                    { text: "1:2", value: 0.5 }
                ]
                onActivated: {
                    view.plot.camera.aspect = currentValue;
                    window.logLine("aspect -> " + currentText);
                }
            }
            SpinBox {
                id: themeBox
                objectName: "themeBox"
                from: 0
                to: Math.max(0, view.plot.available_themes.length - 1)
                value: Math.max(0, view.plot.available_themes.indexOf(view.plot.theme))
                textFromValue: function (value) {
                    return view.plot.available_themes[value] !== undefined
                           ? view.plot.available_themes[value] : "";
                }
                valueFromText: function (text) {
                    const index = view.plot.available_themes.indexOf(text);
                    return index < 0 ? value : index;
                }
                onValueModified: {
                    view.plot.theme = view.plot.available_themes[value];
                    window.logLine("theme -> " + view.plot.theme);
                }
            }
            Button {
                text: "Reset view"
                onClicked: view.plot.camera.reset()
            }
            // Filled in by draw(): a binding on curves.at(0) would be evaluated before the
            // app's own curve exists, and a failed binding is never re-evaluated.
            Label {
                id: errorLabel
                color: "#ff8080"
            }
        }
    }

    SplitView {
        // ApplicationWindow reserves the header/footer space inside contentItem,
        // so filling the parent is enough.
        anchors.fill: parent
        orientation: Qt.Horizontal

        PlotView {
            id: view
            objectName: "plot"
            SplitView.preferredWidth: 880
            SplitView.fillHeight: true

            // Overlay: no pointer handlers, so mouse events pass straight through
            Rectangle {
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.margins: 12
                width: overlayLabel.implicitWidth + 16
                height: overlayLabel.implicitHeight + 8
                radius: 4
                color: "#18000000"
                border.color: "#60000000"

                Text {
                    id: overlayLabel
                    anchors.centerIn: parent
                    color: "#333333"
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
            Label { text: "plot " + view.width.toFixed(0) + "x" + view.height.toFixed(0) }
        }
    }

    function viewBounds(index) {
        const camera = view.plot.camera;
        return index === 0 ? camera.xlim.x : index === 1 ? camera.xlim.y
             : index === 2 ? camera.ylim.x : camera.ylim.y;
    }

    Connections {
        target: window
        function onActiveFocusItemChanged() {
            window.logLine("focus -> " + window.activeFocusItem);
        }
    }
}
