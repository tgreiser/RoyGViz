# me - this DAT
#
# dat - the DAT that received the event
# rowIndex - the row number that was added
# message - a readable description of the event
# channel - the numeric value of the event channel
# index - the numeric value of the event index
# value - the numeric value of the event value
# input - true when the event was received
# bytes - a byte array of the received event

def onReceiveMIDI(dat, rowIndex, message, channel, index, value, input, bytes):
	midi = parent.Midi
	if midi is None:
		return
	midi.OnMidi(message, channel, index, value, input)
