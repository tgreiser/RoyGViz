# me - this DAT
# chop execute — fires on clock/beat_pulse rising edge via null_bus

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
