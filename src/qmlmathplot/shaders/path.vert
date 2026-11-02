#version 440
layout(location = 0) in vec2 pos;
layout(location = 1) in vec4 color;
layout(location = 2) in vec2 local;
layout(location = 0) out vec4 vColor;
layout(location = 1) out vec2 vLocal;
void main() {
    gl_Position = vec4(pos, 0.0, 1.0);
    vColor = color;
    vLocal = local;
}
