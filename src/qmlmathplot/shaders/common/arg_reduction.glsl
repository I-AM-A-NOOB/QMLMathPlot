// Argument reduction: the GPU's built-in sin/cos are unreliable for large arguments;
// fold into [0,2π) before calling
#define TAU 6.283185307179586
float _wrap(float t) { return t - TAU * floor(t * (1.0 / TAU)); }
#define SIN(t) sin(_wrap(t))
#define COS(t) cos(_wrap(t))
