"""Snap morphed bases once the beat ramp reaches 1."""


def onOffToOn(channel, sampleIndex, val, prev):
	parent().ext.PresetsExt.OnMorphDone()


def onOnToOff(channel, sampleIndex, val, prev):
	return


def onWhileOn(channel, sampleIndex, val, prev):
	return


def onWhileOff(channel, sampleIndex, val, prev):
	return


def onValueChange(channel, sampleIndex, val, prev):
	return
