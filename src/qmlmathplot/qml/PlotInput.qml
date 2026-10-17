// Pointer input: mouse drag pans, wheel zooms at the cursor, one finger pans, two fingers
// pinch and pan. Direct manipulation only; keyboard lives on PlotView (it needs the root's
// active focus). Every handler cancels an in-flight eased move.
import QtQuick

Item {
    id: inputRoot

    property Item view

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        // Panning must win over any ancestor Flickable/ScrollView: without this the Flickable
        // steals the grab once the drag passes its threshold, and the plot only moves a few
        // pixels before panning stops.
        preventStealing: true

        property real lastX: 0
        property real lastY: 0

        onPressed: (mouse) => {
            inputRoot.view.forceActiveFocus();
            inputRoot.view.plot.camera.stop_animation();
            lastX = mouse.x;
            lastY = mouse.y;
        }
        onPositionChanged: (mouse) => {
            if (!inputRoot.view.plot.camera.panEnabled)
                return;
            inputRoot.view.plot.camera.pan_pixels(mouse.x - lastX, mouse.y - lastY);
            lastX = mouse.x;
            lastY = mouse.y;
        }
    }

    // Touch: one finger pans, two fingers pinch (scale) and pan together. The centroid is
    // kept under the fingers, so a pinch that also drifts behaves like a map gesture.
    MultiPointTouchArea {
        anchors.fill: parent
        minimumTouchPoints: 1
        maximumTouchPoints: 2
        mouseEnabled: false
        enabled: inputRoot.view.plot.camera.panEnabled
                 || inputRoot.view.plot.camera.zoomEnabled
        touchPoints: [TouchPoint {}, TouchPoint {}]

        property int lastCount: 0
        property real lastCx: 0
        property real lastCy: 0
        property real lastSpread: 0

        function _centroidX(points) {
            let sum = 0;
            for (let i = 0; i < points.length; ++i)
                sum += points[i].x;
            return points.length > 0 ? sum / points.length : 0;
        }

        function _centroidY(points) {
            let sum = 0;
            for (let i = 0; i < points.length; ++i)
                sum += points[i].y;
            return points.length > 0 ? sum / points.length : 0;
        }

        function _spread(points) {
            if (points.length < 2)
                return 0;
            const dx = points[0].x - points[1].x;
            const dy = points[0].y - points[1].y;
            return Math.sqrt(dx * dx + dy * dy);
        }

        function _baseline(points) {
            lastCount = points.length;
            lastCx = _centroidX(points);
            lastCy = _centroidY(points);
            lastSpread = _spread(points);
        }

        onPressed: (points) => {
            inputRoot.view.forceActiveFocus();
            inputRoot.view.plot.camera.stop_animation();
            _baseline(points);
        }
        onUpdated: (points) => {
            if (points.length === 0 || points.length !== lastCount) {
                _baseline(points);
                return;
            }
            const camera = inputRoot.view.plot.camera;
            const width = inputRoot.view.width;
            const height = inputRoot.view.height;
            const cx = _centroidX(points);
            const cy = _centroidY(points);
            if (points.length >= 2 && camera.zoomEnabled && width > 0 && height > 0) {
                const spread = _spread(points);
                if (lastSpread > 0 && spread > 0) {
                    camera.zoom_at(spread / lastSpread, lastCx / width, lastCy / height,
                                   width, height);
                }
            }
            if (camera.panEnabled && (cx !== lastCx || cy !== lastCy))
                camera.pan_pixels(cx - lastCx, cy - lastCy);
            lastCx = cx;
            lastCy = cy;
            lastSpread = _spread(points);
        }
        onReleased: (points) => {
            lastCount = 0;
        }
        onCanceled: (points) => {
            lastCount = 0;
        }
    }

    WheelHandler {
        enabled: inputRoot.view.plot.camera.zoomEnabled

        // The cursor position is the anchor; delta is the raw wheel delta, 120 = one notch.
        // Note: Qt 6's QML WheelEvent has no position, only x/y (relative to this item).
        onWheel: (event) => {
            const camera = inputRoot.view.plot.camera;
            const d = event.angleDelta.y !== 0 ? event.angleDelta.y : event.pixelDelta.y;
            inputRoot.view.forceActiveFocus();
            camera.animate_zoom(d, event.x / inputRoot.view.width,
                                event.y / inputRoot.view.height,
                                inputRoot.view.width, inputRoot.view.height,
                                camera.animationDuration);
            event.accepted = true;
        }
    }
}
