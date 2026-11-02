#version 440
layout(location = 0) in vec4 vColor;
layout(location = 1) in vec2 vLocal;
layout(location = 0) out vec4 fragColor;
void main() {
    // Segment quads carry local = (0, 0); a join quad carries [-1, 1] and is clipped to a
    // disc, which rounds the stroke at a bend (the segment quads alone leave a square).
    if (dot(vLocal, vLocal) > 1.0) discard;
    fragColor = vColor;
}
