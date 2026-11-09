# me - this DAT
# chop - the CHOP that changed

def onOffToOn(channel, sampleIndex, val, prev):
	if channel.name != 'clock/beat_pulse':
		return
	midi = parent.Midi
	if midi is not None:
		midi.OnBeatPulse()


def onOnToOff(channel, sampleIndex, val, prev):
	return


def onWhileOn(channel, sampleIndex, val, prev):
	return


def onWhileOff(channel, sampleIndex, val, prev):
	return


def onValueChange(channel, sampleIndex, val, prev):
	return
