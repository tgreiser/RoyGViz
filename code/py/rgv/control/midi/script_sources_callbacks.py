# me - this DAT
# scriptOp - the OP which is cooking

def onSetupParameters(scriptOp):
	return

def onPulse(par):
	return

def onCook(scriptOp):
	midi = parent.Midi
	if midi is None:
		scriptOp.clear()
		return
	midi.OnSourcesCook(scriptOp)
