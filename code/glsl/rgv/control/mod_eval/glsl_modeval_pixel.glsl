// RGV mod_eval — fixed slot evaluator.
// Inputs: 0=sPack, 1=sSources, 2=sFeedback (sTD2DInputs).
// Output 512x6: y0 = resolved/mapped; y1-4 = smooth state; y5 = freeze hold.

uniform float uGlobalMod;
uniform float uFreeze;
uniform float uDt;
uniform float uNumSources;

out vec4 fragColor;

#define sPack sTD2DInputs[0]
#define sSources sTD2DInputs[1]
#define sFeedback sTD2DInputs[2]

#define ADDR_W 512.0
#define SRC_W 64.0

float adaptSource(float raw, float polarity, float mode) {
	// polarity: 0=uni, 1=bi. mode: 1=add_bipolar expects bi; others expect uni.
	if (mode < 1.5) {
		// add_bipolar
		if (polarity < 0.5) return raw * 2.0 - 1.0;
		return raw;
	}
	if (polarity > 0.5) return raw * 0.5 + 0.5;
	return raw;
}

float applySlot(float v, float mode, float d, float src) {
	if (mode < 0.5) return v;
	if (mode < 1.5) {
		// add_bipolar
		if (src >= 0.0) return v + d * src * (1.0 - v);
		return v + d * src * v;
	}
	if (mode < 2.5) {
		// add_unipolar
		if (d >= 0.0) return v + d * src * (1.0 - v);
		return v + d * src * v;
	}
	if (mode < 3.5) {
		// multiply
		float m = (d >= 0.0) ? src : (1.0 - src);
		return v * ((1.0 - abs(d)) + abs(d) * m);
	}
	// replace
	float up = (d >= 0.0) ? src : (1.0 - src);
	return v + abs(d) * (up - v);
}

float applyCurve(float v, float curve, float mapMin, float mapMax) {
	v = clamp(v, 0.0, 1.0);
	if (curve > 0.5 && curve < 1.5) {
		// exp
		if (mapMin > 0.0 && mapMax > 0.0)
			return mapMin * pow(mapMax / mapMin, v);
		return mapMin + (mapMax - mapMin) * v * v;
	}
	if (curve > 1.5) {
		// log
		return mapMin + (mapMax - mapMin) * (log(1.0 + v * 9.0) / log(10.0));
	}
	return mapMin + v * (mapMax - mapMin);
}

void main() {
	vec2 uv = vUV.st;
	int x = int(uv.x * ADDR_W);
	int y = int(uv.y * 6.0);
	float fx = (float(x) + 0.5) / ADDR_W;

	vec4 prev0 = texture(sFeedback, vec2(fx, (0.5) / 6.0));
	vec4 prevHold = texture(sFeedback, vec2(fx, (5.5) / 6.0));

	vec4 meta = texture(sPack, vec2(fx, (0.5) / 10.0));
	vec4 range = texture(sPack, vec2(fx, (1.5) / 10.0));

	float isActive = meta.a;
	float base = meta.r;
	float ptype = meta.g;
	float curve = meta.b;
	float mapMin = range.r;
	float mapMax = range.g;
	float grab = range.b;

	// Inactive / over cap: pass zeros
	if (isActive < 0.5) {
		fragColor = TDOutputSwizzle(vec4(0.0));
		return;
	}

	// Freeze: repeat previous resolved/mapped (prefer y0 feedback; hold row is backup)
	if (uFreeze > 0.5) {
		vec4 frozen = prev0;
		if (frozen.b < 0.5) frozen = prevHold;
		if (y == 0) {
			fragColor = TDOutputSwizzle(vec4(frozen.r, frozen.g, 1.0, 1.0));
			return;
		}
		if (y == 5) {
			fragColor = TDOutputSwizzle(vec4(frozen.r, frozen.g, frozen.b, 1.0));
			return;
		}
		// hold smooth state
		float sy = (float(y) + 0.5) / 6.0;
		fragColor = TDOutputSwizzle(texture(sFeedback, vec2(fx, sy)));
		return;
	}

	float v = base;
	float smoothOut0 = 0.0;
	float smoothOut1 = 0.0;
	float smoothOut2 = 0.0;
	float smoothOut3 = 0.0;

	// Menu: base only
	if (ptype > 2.5 && ptype < 3.5) {
		v = base;
	} else if (ptype > 3.5) {
		// Pulse: rising edge of first active slot source
		vec4 slot = texture(sPack, vec2(fx, (2.5) / 10.0));
		float srcIdx = slot.a;
		float src = 0.0;
		if (srcIdx > 0.5) {
			float sx = (srcIdx - 1.0 + 0.5) / SRC_W;
			src = texture(sSources, vec2(sx, 0.5)).r;
		}
		float prevSrc = prev0.b; // stash prev source in B of hold row0 historically — use hold.b
		prevSrc = prevHold.b;
		float fire = (src >= 0.5 && prevSrc < 0.5) ? 1.0 : 0.0;
		v = fire;
		// store current src into mapped channel side for next edge detect via hold.b
		float p = fire;
		if (y == 0) {
			fragColor = TDOutputSwizzle(vec4(v, p, 1.0, 1.0));
			return;
		}
		if (y == 5) {
			fragColor = TDOutputSwizzle(vec4(v, p, src, 1.0));
			return;
		}
		fragColor = TDOutputSwizzle(vec4(0.0));
		return;
	} else if (ptype > 1.5 && ptype < 2.5) {
		// Toggle: optional gate on slot0
		vec4 slot = texture(sPack, vec2(fx, (2.5) / 10.0));
		float srcIdx = slot.a;
		float src = 0.0;
		float polarity = 0.0;
		if (srcIdx > 0.5) {
			float sx = (srcIdx - 1.0 + 0.5) / SRC_W;
			vec4 s = texture(sSources, vec2(sx, 0.5));
			src = s.r;
			polarity = s.g;
			src = adaptSource(src, polarity, 2.0); // unipolar
		}
		v = base;
		if (srcIdx > 0.5 && src >= 0.5) v = 1.0;
	} else {
		// float / int — four slots
		for (int i = 0; i < 4; i++) {
			float row = (2.5 + float(i)) / 10.0;
			vec4 slot = texture(sPack, vec2(fx, row));
			float depth = slot.r * uGlobalMod * grab;
			float mode = slot.g;
			float smoothMs = slot.b;
			float srcIdx = slot.a;
			if (srcIdx < 0.5 || mode < 0.5) {
				continue;
			}
			float sx = (srcIdx - 1.0 + 0.5) / SRC_W;
			vec4 s = texture(sSources, vec2(sx, 0.5));
			float raw = s.r;
			float polarity = s.g;
			float adapted = adaptSource(raw, polarity, mode);

			// one-pole smooth on source
			float prevS = texture(sFeedback, vec2(fx, (float(i) + 1.5) / 6.0)).r;
			float smoothed = adapted;
			if (smoothMs > 0.001) {
				float a = 1.0 - exp(-uDt * 1000.0 / max(smoothMs, 0.001));
				smoothed = mix(prevS, adapted, clamp(a, 0.0, 1.0));
			}
			if (i == 0) smoothOut0 = smoothed;
			else if (i == 1) smoothOut1 = smoothed;
			else if (i == 2) smoothOut2 = smoothed;
			else smoothOut3 = smoothed;

			v = applySlot(v, mode, depth, smoothed);

			vec4 swing = texture(sPack, vec2(fx, (6.5 + float(i)) / 10.0));
			if (swing.b > 0.5) {
				v = clamp(v, swing.r, swing.g);
			}
		}
		v = clamp(v, 0.0, 1.0);
	}

	float p = applyCurve(v, curve, mapMin, mapMax);
	if (ptype > 0.5 && ptype < 1.5) {
		// int
		p = floor(p + 0.5);
	}

	if (y == 0) {
		fragColor = TDOutputSwizzle(vec4(v, p, 1.0, 1.0));
		return;
	}
	if (y == 1) {
		fragColor = TDOutputSwizzle(vec4(smoothOut0, 0.0, 0.0, 1.0));
		return;
	}
	if (y == 2) {
		fragColor = TDOutputSwizzle(vec4(smoothOut1, 0.0, 0.0, 1.0));
		return;
	}
	if (y == 3) {
		fragColor = TDOutputSwizzle(vec4(smoothOut2, 0.0, 0.0, 1.0));
		return;
	}
	if (y == 4) {
		fragColor = TDOutputSwizzle(vec4(smoothOut3, 0.0, 0.0, 1.0));
		return;
	}
	// y == 5 hold
	fragColor = TDOutputSwizzle(vec4(v, p, 0.0, 1.0));
}
