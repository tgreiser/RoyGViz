"""Minimal Phase-3 UI: slot editor writes mod_matrix; sync inspector readouts."""
from TDStoreTools import StorageManager
import TDFunctions as TDF

STUB_ADDRESS = 'layer1/gen@stub/scale'


class UiExt:
	def __init__(self, ownerComp):
		self.ownerComp = ownerComp

	def onInitTD(self):
		self.SyncFromMatrix()

	def Hello(self):
		return 'UiExt'

	def SyncFromMatrix(self):
		matrix = parent.Rgv.op('data/mod_matrix')
		if matrix is None:
			return
		for r in range(1, matrix.numRows):
			if matrix[r, 'address'].val.strip() != STUB_ADDRESS:
				continue
			if int(float(matrix[r, 'slot'].val or 0)) != 0:
				continue
			par = self.ownerComp.par
			try:
				par.Slotsource.val = matrix[r, 'source'].val
			except Exception:
				pass
			par.Slotdepth.val = float(matrix[r, 'depth'].val or 0)
			try:
				par.Slotmode.val = matrix[r, 'mode'].val
			except Exception:
				pass
			par.Slotsmooth.val = float(matrix[r, 'smooth_ms'].val or 0)
			return

	def ApplySlot(self):
		"""Write slot 0 for the stub Scale address from UI pars."""
		matrix = parent.Rgv.op('data/mod_matrix')
		if matrix is None:
			return
		par = self.ownerComp.par
		source = str(par.Slotsource.eval())
		depth = float(par.Slotdepth.eval())
		mode = str(par.Slotmode.eval())
		smooth = float(par.Slotsmooth.eval())

		# find existing slot 0 row or append
		target_row = None
		for r in range(1, matrix.numRows):
			if matrix[r, 'address'].val.strip() != STUB_ADDRESS:
				continue
			if int(float(matrix[r, 'slot'].val or 0)) != 0:
				continue
			target_row = r
			break
		if target_row is None:
			matrix.appendRow([
				STUB_ADDRESS, '0', source, str(depth), mode, str(smooth), '', '', '0'
			])
		else:
			matrix[target_row, 'source'] = source
			matrix[target_row, 'depth'] = str(depth)
			matrix[target_row, 'mode'] = mode
			matrix[target_row, 'smooth_ms'] = str(smooth)

		meval = parent.Rgv.op('control/mod_eval')
		if meval is not None:
			meval.Rebuild()
			meval.Evaluate()
		return True

	def onParPulse(self, par):
		if par.name == 'Applyslot':
			self.ApplySlot()
