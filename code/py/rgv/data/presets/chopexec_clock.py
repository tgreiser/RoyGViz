"""Quantize boundary: beat or bar pulse commits a waiting preset."""


def onOffToOn(channel, sampleIndex, val, prev):
	# .ext forces init. A promoted name is missing until the extension exists.
	presets = parent().ext.PresetsExt
	name = channel.name
	if name == 'clock/bar_pulse':
		presets.OnClockPulse('bar')
	elif name == 'clock/beat_pulse':
		presets.OnClockPulse('beat')


def onOnToOff(channel, sampleIndex, val, prev):
	return


def onWhileOn(channel, sampleIndex, val, prev):
	return


def onWhileOff(channel, sampleIndex, val, prev):
	return


def onValueChange(channel, sampleIndex, val, prev):
	return
