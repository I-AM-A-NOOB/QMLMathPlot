layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec4 camera;      // centre.x, centre.y, world units per logical pixel (x, y)
    vec2 size;        // viewport size (logical pixels)
    float lineWidth;  // line width (logical pixels)
    vec4 color;       // non-premultiplied rgba
};
