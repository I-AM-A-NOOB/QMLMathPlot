// GLSL pow() is undefined for a negative base; odd roots are real there (x**(1/3)).
float _real_pow(float v, float p) {
    return v < 0.0 ? -pow(-v, p) : pow(v, p);
}
