// A vertical interval [lo, hi] at this pixel's y, with a one-pixel feather at its edges.
float _span_cov(float y, float lo, float hi, float sy) {
    return clamp((y - lo) * sy + 1.0, 0.0, 1.0)
         * clamp((hi - y) * sy + 1.0, 0.0, 1.0);
}
