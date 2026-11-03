package org.geogebra.common.euclidian.plot.interval;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import org.geogebra.common.SuiteSubApp;
import org.geogebra.common.awt.GShape;
import org.geogebra.common.kernel.geos.GeoFunction;
import org.geogebra.common.kernel.interval.Interval;
import org.geogebra.common.kernel.interval.IntervalSet;
import org.geogebra.common.kernel.interval.IntervalSetOps;
import org.geogebra.common.kernel.interval.function.GeoFunctionConverter;
import org.geogebra.common.kernel.interval.function.IntervalFunctionSupport;
import org.geogebra.common.kernel.interval.function.IntervalTuple;
import org.geogebra.common.kernel.interval.function.IntervalTupleList;
import org.geogebra.common.kernel.interval.samplers.FunctionSampler;
import org.geogebra.common.euclidian.EuclidianView;
import org.geogebra.common.euclidian.plot.CurvePlotter;
import org.geogebra.common.euclidian.plot.Gap;
import org.geogebra.common.euclidian.plot.PathPlotter;
import org.geogebra.common.kernel.MyPoint;
import org.geogebra.common.kernel.SegmentType;
import org.geogebra.common.kernel.matrix.CoordSys;
import org.geogebra.common.awt.GPoint;
import org.geogebra.test.BaseAppTestSetup;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * TEMPORARY GGB oracle harness. Copy this file into
 * source/shared/common-jre/src/test/java/org/geogebra/common/euclidian/plot/interval/,
 * run the common-jre test, then delete it from the GGB tree.
 */
class GgbOracleTest extends BaseAppTestSetup {
    private static final String OUT = "D:/Projects/QMLMathPlot/tests/fixtures/ggb_oracle";
    private static final String COMMIT = "ae21d678407";

    private final GeoFunctionConverter converter = new GeoFunctionConverter();

    private record Case(String id, String expression, double xmin, double xmax,
                        double ymin, double ymax, int width, int height) {
    }

    @BeforeEach
    void setUp() {
        setupApp(SuiteSubApp.GRAPHING);
    }

    @Test
    void writeOracle() throws IOException {
        Path dir = Path.of(OUT);
        Files.createDirectories(dir);
        for (Case c : cases()) {
            String json;
            try {
                json = Oracle.capture(this, converter, c);
            } catch (RuntimeException e) {
                json = errorJson(c, e);
                System.err.println("oracle " + c.id + " FAILED: " + e);
            }
            Files.writeString(dir.resolve(c.id + ".json"), json);
            System.out.println("oracle " + c.id + " " + json.length() + " bytes");
        }
    }

    private static String errorJson(Case c, RuntimeException e) {
        return "{\n  \"ggb_commit\": \"" + COMMIT + "\",\n"
                + "  \"id\": \"" + esc(c.id) + "\",\n"
                + "  \"expression\": \"" + esc(c.expression) + "\",\n"
                + "  \"supported\": null,\n"
                + "  \"error\": \"" + esc(String.valueOf(e)) + "\",\n"
                + "  \"columns\": [],\n  \"segments\": []\n}\n";
    }

    private static List<Case> cases() {
        List<Case> cases = new ArrayList<>();
        cases.add(new Case("tan_default", "tan(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("inv_default", "1/x", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("ln_default", "ln(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("sin_inv_default", "sin(1/x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("mixed_default",
                "-x^2/2 + x*tan(x) - ln(cos(x)^(-2))/2", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("mixed_wide",
                "-x^2/2 + x*tan(x) - ln(cos(x)^(-2))/2",
                -122.7308, 83.6330, -75.9195, 83.5434, 880, 680));
        cases.add(new Case("tan_wide_y", "tan(x)", -6, 6, 50, 60, 900, 600));
        cases.add(new Case("exp_tan_wide", "e^(tan(x))", -172, 170, -13.7, 250, 900, 600));
        cases.add(new Case("rational", "x/(1+x^2)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("rational_sq", "1/(1+x^2)^2", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("dependent", "x/sqrt(x^2)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("abs_quotient", "x/abs(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("floor", "floor(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("sign", "sign(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("sqrt_tan_cot", "sqrt(tan(cot(x)))", 5, 8, -5, 5, 50, 50));
        cases.add(new Case("cot_sec", "cot(sec(x))", 5, 8, -5, 5, 50, 50));
        cases.add(new Case("ln_cosh", "ln(cosh(x))", -750, 750, -50, 750, 200, 200));
        cases.add(new Case("cube_root", "x^(1/3)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("tan_double", "tan(2*x)", -6, 6, -2, 2, 900, 600));
        // CurvePlotter (GGB's fallback for unsupported expressions).
        cases.add(new Case("curve_mixed_default",
                "-x^2/2 + x*tan(x) - ln(cos(x)^(-2))/2", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_mixed_wide",
                "-x^2/2 + x*tan(x) - ln(cos(x)^(-2))/2",
                -122.7308, 83.6330, -75.9195, 83.5434, 880, 680));
        cases.add(new Case("curve_rational", "x/(1+x^2)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_dependent", "x/sqrt(x^2)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_abs_quotient", "x/abs(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_floor", "floor(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_sign", "sign(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_x_sin", "x*sin(x)", -6, 6, -2, 2, 900, 600));
        cases.add(new Case("curve_sin_plus_x", "sin(x)+x", -6, 6, -2, 2, 900, 600));
        return cases;
    }

    /** A bounds implementation with the production screen mapping, not identity. */
    static final class Bounds implements EuclidianViewBounds {
        private final double xmin;
        private final double xmax;
        private final double ymin;
        private final double ymax;
        private final int width;
        private final int height;

        Bounds(double xmin, double xmax, double ymin, double ymax, int width, int height) {
            this.xmin = xmin;
            this.xmax = xmax;
            this.ymin = ymin;
            this.ymax = ymax;
            this.width = width;
            this.height = height;
        }

        @Override public int getWidth() { return width; }
        @Override public int getHeight() { return height; }
        @Override public Interval domain() { return new Interval(xmin, xmax); }
        @Override public Interval range() { return new Interval(ymin, ymax); }
        @Override public double getXmin() { return xmin; }
        @Override public double getXmax() { return xmax; }
        @Override public double getYmin() { return ymin; }
        @Override public double getYmax() { return ymax; }

        @Override public Interval toScreenIntervalX(Interval x) {
            return new Interval(toScreenCoordXd(x.getLow()), toScreenCoordXd(x.getHigh()));
        }

        @Override public Interval toScreenIntervalY(Interval y) {
            return new Interval(toScreenCoordYd(y.getHigh()), toScreenCoordYd(y.getLow()));
        }

        @Override public boolean isOnView(double x, double y) {
            return x >= 0 && x <= width && y >= 0 && y <= height;
        }

        @Override public double toScreenCoordXd(double x) {
            return (x - xmin) / (xmax - xmin) * width;
        }

        @Override public double toScreenCoordYd(double y) {
            return (ymax - y) / (ymax - ymin) * height;
        }

        @Override public double toRealWorldCoordX(double x) {
            return xmin + x / width * (xmax - xmin);
        }

        @Override public double toRealWorldCoordY(double y) {
            return ymax - y / height * (ymax - ymin);
        }

        @Override public boolean isOnView(Interval y) {
            return y.getLow() <= ymax && y.getHigh() >= ymin;
        }

        @Override public double getInvXscale() { return (xmax - xmin) / width; }
        @Override public double getInvYscale() { return (ymax - ymin) / height; }
        @Override public double getXZero() { return toScreenCoordXd(0); }
        @Override public double getYZero() { return toScreenCoordYd(0); }
        @Override public GShape getBoundingPath() { return null; }
        @Override public double getXScale() { return width / (xmax - xmin); }
        @Override public double getYScale() { return height / (ymax - ymin); }
    }

    /** Records the path operations in screen space, with the production plotter's clip. */
    static final class Recorder implements IntervalPathPlotter {
        private final double height;
        private final List<String> entries = new ArrayList<>();

        Recorder(double height) {
            this.height = height;
        }

        @Override public void reset() { entries.clear(); }

        @Override public void moveTo(double x, double y) {
            entries.add("{\"op\":\"MOVE_TO\",\"x\":" + n(x) + ",\"y\":" + n(y) + "}");
        }

        @Override public void lineTo(double x, double y) {
            entries.add("{\"op\":\"LINE_TO\",\"x\":" + n(x) + ",\"y\":" + n(y) + "}");
        }

        @Override public void segment(double x1, double y1, double x2, double y2) {
            entries.add("{\"op\":\"SEGMENT\",\"x1\":" + n(x1) + ",\"y1\":" + n(y1)
                    + ",\"x2\":" + n(x2) + ",\"y2\":" + n(y2) + "}");
        }

        @Override public void segment(EuclidianViewBounds bounds,
                                     double x1, double y1, double x2, double y2) {
            segment(bounds.toScreenCoordXd(x1), bounds.toScreenCoordYd(y1),
                    bounds.toScreenCoordXd(x2), bounds.toScreenCoordYd(y2));
        }

        @Override public void draw(org.geogebra.common.awt.GGraphics2D g2) {
            // no-op
        }

        @Override public void leftToTop(EuclidianViewBounds bounds, Interval x, Interval y) {
            segment(bounds, x.getLow(), y.getLow(), x.middle(), bounds.getYmax());
        }

        @Override public void leftToBottom(EuclidianViewBounds bounds, Interval x, Interval y) {
            segment(bounds, x.getLow(), y.getHigh(), x.middle(), bounds.getYmin());
        }

        String json() {
            return String.join(",", entries);
        }
    }

    /** Captures one expression at one view. */
    static final class Oracle {
        static String capture(GgbOracleTest test, GeoFunctionConverter converter, Case c)
                throws IOException {
            GeoFunction function = test.evaluateGeoElement(c.expression);
            Bounds bounds = new Bounds(c.xmin, c.xmax, c.ymin, c.ymax, c.width, c.height);
            boolean supported = IntervalFunctionSupport.isSupported(function);
            StringBuilder sb = new StringBuilder();
            sb.append("{\n");
            sb.append("  \"ggb_commit\": \"").append(COMMIT).append("\",\n");
            sb.append("  \"id\": \"").append(esc(c.id)).append("\",\n");
            sb.append("  \"expression\": \"").append(esc(c.expression)).append("\",\n");
            sb.append("  \"supported\": ").append(supported).append(",\n");
            sb.append("  \"bounds\": {\"xmin\":").append(n(c.xmin))
                    .append(",\"xmax\":").append(n(c.xmax))
                    .append(",\"ymin\":").append(n(c.ymin))
                    .append(",\"ymax\":").append(n(c.ymax)).append("},\n");
            sb.append("  \"size\": {\"width\":").append(c.width)
                    .append(",\"height\":").append(c.height).append("},\n");
            sb.append("  \"columns\": [");
            String columns = "";
            String segments = "[]";
            if (supported) {
                IntervalTupleList tuples = new IntervalTupleList();
                IntervalFunctionData data = new IntervalFunctionData(
                        function, converter, bounds, tuples);
                new FunctionSampler(data, bounds);
                List<String> cols = new ArrayList<>();
                for (IntervalTuple tuple : tuples) {
                    cols.add(columnJson(tuple));
                }
                columns = String.join(",\n    ", cols);
                Recorder recorder = new Recorder(c.height);
                IntervalPath path = new IntervalPath(recorder, bounds,
                        new QueryFunctionDataImpl(tuples));
                path.update();
                segments = "[" + recorder.json() + "]";
            }
            sb.append("\n    ").append(columns).append("\n  ],\n");
            sb.append("  \"segments\": ").append(segments).append("\n");
            sb.append("}\n");
            return sb.toString();
        }

        private static String columnJson(IntervalTuple tuple) {
            IntervalSet x = tuple.xSet();
            Interval xv = IntervalSetOps.connectedInterval(x);
            IntervalSet y = tuple.ySet();
            StringBuilder sb = new StringBuilder();
            sb.append("{\"x\":[").append(n(xv.getLow())).append(",").append(n(xv.getHigh()))
                    .append("],\"kind\":\"").append(y.kind()).append("\"");
            if (y.isConnected()) {
                Interval v = IntervalSetOps.connectedInterval(y);
                sb.append(",\"lo\":").append(n(v.getLow())).append(",\"hi\":").append(n(v.getHigh()));
            } else if (y.isInverted()) {
                Interval v = IntervalSetOps.invertedGap(y);
                sb.append(",\"lo\":").append(n(v.getLow())).append(",\"hi\":").append(n(v.getHigh()));
            }
            sb.append("}");
            return sb.toString();
        }
    }

    @Test
    void writeCurvePlotterOracle() throws IOException {
        Path dir = Path.of("D:/Projects/QMLMathPlot/tests/fixtures/ggb_curve_plotter");
        Files.createDirectories(dir);
        EuclidianView view = getApp().getActiveEuclidianView();
        for (Case c : cases()) {
            if (!c.id.startsWith("curve_")) {
                continue;
            }
            String expression = c.expression;
            try {
                GeoFunction function = evaluateGeoElement(expression);
                double xscale = view.getWidth() / (c.xmax - c.xmin);
                double yscale = view.getHeight() / (c.ymax - c.ymin);
                view.setCoordSystem(-c.xmin * xscale, c.ymax * yscale, xscale, yscale);
                CurveRecorder recorder = new CurveRecorder();
                GPoint label = CurvePlotter.plotCurve(function, c.xmin, c.xmax, view, recorder,
                        false, Gap.MOVE_TO);
                String json = "{\n  \"ggb_commit\": \"" + COMMIT + "\",\n"
                        + "  \"id\": \"" + esc(c.id) + "\",\n"
                        + "  \"expression\": \"" + esc(expression) + "\",\n"
                        + "  \"view\": {\"xmin\":" + n(c.xmin) + ",\"xmax\":" + n(c.xmax)
                        + ",\"ymin\":" + n(c.ymin) + ",\"ymax\":" + n(c.ymax)
                        + ",\"width\":" + view.getWidth() + ",\"height\":" + view.getHeight() + "},\n"
                        + "  \"label\": " + (label == null ? "null"
                            : "{\"x\":" + n(label.x) + ",\"y\":" + n(label.y) + "}") + ",\n"
                        + "  \"operations\": [" + recorder.json() + "]\n}\n";
                Files.writeString(dir.resolve(c.id + ".json"), json);
                System.out.println("curve oracle " + c.id + " " + recorder.size() + " ops");
            } catch (RuntimeException e) {
                System.err.println("curve oracle " + c.id + " FAILED: " + e);
                Files.writeString(dir.resolve(c.id + ".json"),
                        "{\n  \"id\": \"" + esc(c.id) + "\",\n  \"error\": \""
                                + esc(String.valueOf(e)) + "\",\n  \"operations\": []\n}\n");
            }
        }
    }

    /** Records CurvePlotter's screen-space path operations. */
    static final class CurveRecorder implements PathPlotter {
        private final List<String> entries = new ArrayList<>();

        @Override public void drawTo(double[] pos, SegmentType type) {
            add(type == SegmentType.LINE_TO ? "LINE_TO" : "MOVE_TO", pos);
        }

        @Override public void lineTo(double[] pos) { add("LINE_TO", pos); }
        @Override public void moveTo(double[] pos) { add("MOVE_TO", pos); }
        @Override public void corner() { entries.add("{\"op\":\"CORNER\"}"); }
        @Override public void corner(double[] pos) { add("CORNER", pos); }

        @Override public void firstPoint(double[] pos, Gap gap) {
            add(gap == Gap.MOVE_TO ? "MOVE_TO" : "LINE_TO", pos);
        }

        @Override public double[] newDoubleArray() { return new double[2]; }

        @Override public boolean copyCoords(MyPoint point, double[] ret, CoordSys transformSys) {
            return false;
        }

        @Override public void endPlot() { entries.add("{\"op\":\"END\"}"); }
        @Override public boolean supports(CoordSys transformSys) { return true; }

        private void add(String op, double[] pos) {
            entries.add("{\"op\":\"" + op + "\",\"x\":" + n(pos[0]) + ",\"y\":" + n(pos[1]) + "}");
        }

        int size() { return entries.size(); }
        String json() { return String.join(",", entries); }
    }

    private static String n(double v) {
        if (Double.isNaN(v)) return "\"NaN\"";
        if (Double.isInfinite(v)) return v > 0 ? "\"Infinity\"" : "\"-Infinity\"";
        return Double.toString(v);
    }

    private static String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
