"""Route Presets custom pulses into the extension."""


def onValueChange(par, prev):
	return


def onPulse(par):
	comp = par.owner
	for ext in comp.extensions:
		fn = getattr(ext, 'onParPulse', None)
		if callable(fn):
			fn(par)
			return
	return
