"""RGV Stack — bank index, FX chain apply, layercount, module load."""
from TDStoreTools import StorageManager
import TDFunctions as TDF


BLEND_MODES = ('over', 'add', 'screen', 'multiply', 'difference')
LAYER_FX_SLOTS = 6
MASTER_FX_SLOTS = 8
BANK_SIZE = 8


class StackExt:
	def __init__(self, ownerComp):
		self.ownerComp = ownerComp

	def onInitTD(self):
		self.ApplyAllFxChains()
		self.LoadAllModules()

	def Hello(self):
		return 'StackExt'

	def _layers(self):
		return self.ownerComp.op('layers')

	def _layer(self, index):
		return self.ownerComp.op(f'layers/layer{int(index)}')

	def Layercount(self):
		try:
			return int(self.ownerComp.par.Layercount.eval())
		except Exception:
			return 4

	def SetLayercount(self, n):
		n = 3 if int(n) <= 3 else 4
		self.ownerComp.par.Layercount.val = str(n)
		return n

	def ActiveBankSlot(self, layerIndex):
		layer = self._layer(layerIndex)
		if layer is None:
			return None
		bank = layer.op('gen_bank')
		if bank is None:
			return None
		idx = int(round(float(layer.par.Bankindex.eval())))
		idx = max(0, min(BANK_SIZE - 1, idx))
		slot = bank.op(f'slot{idx}')
		if slot is not None:
			return slot
		for i in range(BANK_SIZE):
			s = bank.op(f'slot{i}')
			if s is not None and s.op('params') is not None:
				return s
		return None

	def SetBankIndex(self, layerIndex, index, beats=None):
		layer = self._layer(layerIndex)
		if layer is None:
			return False
		index = max(0, min(BANK_SIZE - 1, int(index)))
		layer.par.Bankindex.val = index
		if beats is not None:
			layer.par.Transitionbeats.val = max(0.0, float(beats))
		# Re-publish active gen address
		if hasattr(self.ownerComp, 'LoadModule') and self.ActiveBankSlot(layerIndex):
			slot = self.ActiveBankSlot(layerIndex)
			self.ownerComp.LoadModule(slot.path, addressPrefix=f'layers/layer{int(layerIndex)}/gen')
		return True

	def SetLayerOpacity(self, layerIndex, value01):
		layer = self._layer(layerIndex)
		if layer is None:
			return False
		layer.par.Opacity.val = max(0.0, min(1.0, float(value01)))
		return True

	def ToggleLayerBypass(self, layerIndex):
		layer = self._layer(layerIndex)
		if layer is None:
			return False
		layer.par.Bypass.val = not bool(layer.par.Bypass.eval())
		return True

	def ToggleFxBypass(self, layerIndex, slot):
		layer = self._layer(layerIndex)
		if layer is None:
			return False
		table = layer.op('fx_chain')
		if table is None:
			return False
		slot = int(slot)
		for r in range(1, table.numRows):
			try:
				if int(float(table[r, 'slot'].val)) != slot:
					continue
			except Exception:
				continue
			cur = int(float(table[r, 'bypass'].val or 0))
			table[r, 'bypass'] = '0' if cur else '1'
			self.ApplyFxChain(layer.path)
			return True
		return False

	def ApplyFxChain(self, ownerPath):
		"""Read fx_chain table and set bypass switches / wet selects."""
		owner = op(ownerPath)
		if owner is None:
			return False
		table = owner.op('fx_chain')
		if table is None:
			return False

		is_master = owner.name == 'master'
		nslots = MASTER_FX_SLOTS if is_master else LAYER_FX_SLOTS

		rows = {}
		for r in range(1, table.numRows):
			try:
				slot = int(float(table[r, 'slot'].val))
			except Exception:
				continue
			mid = (table[r, 'module_id'].val or '').strip()
			try:
				bypass = int(float(table[r, 'bypass'].val or 0))
			except Exception:
				bypass = 0
			rows[slot] = {'module_id': mid, 'bypass': bypass}

		for slot in range(1, nslots + 1):
			info = rows.get(slot, {'module_id': '', 'bypass': 1})
			sw = owner.op(f'switch_fx{slot}')
			cross = owner.op(f'cross_fx{slot}')
			sel = owner.op(f'select_fx{slot}')
			mod = owner.op(f'fx{slot}')
			occupied = bool(info['module_id']) and mod is not None
			bypassed = (not occupied) or bool(info['bypass'])
			if sw is not None:
				if bypassed and not occupied:
					sw.par.index.mode = ParMode.CONSTANT
					sw.par.index.val = 0
				else:
					sw.par.index.expr = (
						f"0 if parent.Rgv.par.Fxkill else "
						f"(0 if op('fx_chain')[{slot}, 'bypass'] in ('1','1.0') else 1)"
					)
			if sel is not None and occupied:
				sel.par.top.expr = f"op('fx{slot}/out1')"
			if cross is not None and occupied and mod is not None:
				cross.par.cross.expr = (
					f"op('fx{slot}').par.Mix if hasattr(op('fx{slot}').par, 'Mix') else 1"
				)
		return True

	def ApplyAllFxChains(self):
		layers = self._layers()
		if layers is not None:
			for i in range(1, 5):
				layer = layers.op(f'layer{i}')
				if layer is not None:
					self.ApplyFxChain(layer.path)
		master = self.ownerComp.op('master')
		if master is not None:
			self.ApplyFxChain(master.path)

	def LoadAllModules(self):
		pe = self.ownerComp
		if not hasattr(pe, 'LoadModule'):
			return
		for i in range(1, 5):
			layer = self._layer(i)
			if layer is None:
				continue
			bank = layer.op('gen_bank')
			if bank is not None:
				for s in range(BANK_SIZE):
					slot = bank.op(f'slot{s}')
					if slot is not None and slot.op('params') is not None:
						prefix = f'layers/layer{i}/gen_bank/slot{s}'
						pe.LoadModule(slot.path, addressPrefix=prefix)
						if int(round(float(layer.par.Bankindex.eval()))) == s:
							pe.LoadModule(slot.path, addressPrefix=f'layers/layer{i}/gen')
			for slot in range(1, LAYER_FX_SLOTS + 1):
				fx = layer.op(f'fx{slot}')
				if fx is not None and fx.op('params') is not None:
					pe.LoadModule(fx.path, addressPrefix=f'layers/layer{i}/fx{slot}')
		master = self.ownerComp.op('master')
		if master is not None:
			for slot in range(1, MASTER_FX_SLOTS + 1):
				fx = master.op(f'fx{slot}')
				if fx is not None and fx.op('params') is not None:
					pe.LoadModule(fx.path, addressPrefix=f'master/fx{slot}')

	def OnResolutionChanged(self):
		"""Pulse Reset on Feedback TOPs under layers/master (stubs may have none)."""
		for path in ('layers', 'master'):
			comp = self.ownerComp.op(path)
			if comp is None:
				continue
			for child in comp.findChildren(type=feedbackTOP, maxDepth=8):
				try:
					child.par.resetpulse.pulse()
				except Exception:
					pass

	def BlendModeIndex(self, name):
		name = (name or 'over').lower()
		try:
			return BLEND_MODES.index(name)
		except ValueError:
			return 0
