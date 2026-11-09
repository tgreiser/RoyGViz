// Mod ring meter: knob = base (inner tick), ring = resolved (arc).
uniform float uBase;
uniform float uResolved;

out vec4 fragColor;

void main() {
	vec2 uv = vUV.st * 2.0 - 1.0;
	float r = length(uv);
	float ang = atan(uv.y, uv.x); // -pi..pi, 0 = +x
	// map to 0..1 clockwise from top
	float t = fract((-ang / 6.28318530718) + 0.25);

	vec3 col = vec3(0.08, 0.08, 0.1);
	float a = 1.0;

	// outer track
	float track = smoothstep(0.02, 0.0, abs(r - 0.78) - 0.04);
	col = mix(col, vec3(0.2), track);

	// resolved arc
	float arc = step(t, clamp(uResolved, 0.0, 1.0)) * smoothstep(0.02, 0.0, abs(r - 0.78) - 0.035);
	col = mix(col, vec3(0.95, 0.55, 0.15), arc);

	// base knob tick
	float bt = clamp(uBase, 0.0, 1.0);
	float bang = (0.25 - bt) * 6.28318530718;
	vec2 bdir = vec2(cos(bang), sin(bang));
	float tick = smoothstep(0.04, 0.0, abs(dot(vec2(-bdir.y, bdir.x), uv))) *
		smoothstep(0.55, 0.6, r) * smoothstep(0.92, 0.85, r);
	col = mix(col, vec3(0.95, 0.95, 1.0), tick);

	// center disc
	float disc = smoothstep(0.42, 0.38, r);
	col = mix(col, vec3(0.12, 0.12, 0.14), disc);

	fragColor = TDOutputSwizzle(vec4(col, a));
}
