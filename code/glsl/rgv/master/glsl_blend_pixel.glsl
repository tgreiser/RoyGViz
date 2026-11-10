// RGV master premultiplied blend.
// Input 0 = foreground (upper layer, already opacity-scaled, premultiplied)
// Input 1 = background (stack so far, premultiplied)
// uMode: 0=over, 1=add, 2=screen, 3=multiply, 4=difference

uniform float uMode;

out vec4 fragColor;

void main() {
	vec4 fg = texture(sTD2DInputs[0], vUV.st);
	vec4 bg = texture(sTD2DInputs[1], vUV.st);
	vec3 rgb;
	float a;

	if (uMode < 0.5) {
		// Over (premultiplied)
		rgb = fg.rgb + bg.rgb * (1.0 - fg.a);
		a = fg.a + bg.a * (1.0 - fg.a);
	} else if (uMode < 1.5) {
		// Add — RGB add+clamp, alpha = max
		rgb = min(fg.rgb + bg.rgb, vec3(1.0));
		a = max(fg.a, bg.a);
	} else if (uMode < 2.5) {
		// Screen — alpha of upper
		rgb = vec3(1.0) - (vec3(1.0) - fg.rgb) * (vec3(1.0) - bg.rgb);
		a = fg.a;
	} else if (uMode < 3.5) {
		// Multiply — alpha of upper
		rgb = fg.rgb * bg.rgb;
		a = fg.a;
	} else {
		// Difference — alpha of upper
		rgb = abs(fg.rgb - bg.rgb);
		a = fg.a;
	}

	fragColor = TDOutputSwizzle(vec4(rgb, a));
}
