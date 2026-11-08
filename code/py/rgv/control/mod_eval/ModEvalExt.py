"""Skeleton mod_eval: CHOP stub of the parameter contract. Replaced by GLSL in Phase 3."""
from TDStoreTools import StorageManager
import TDFunctions as TDF
import math


def _f(cell, default=0.0):
	try:
		return float(cell)
	except Exception:
		return default


def _adapt_source(n, polarity, mode):
	"""Adapt bus sample to the mode's expected polarity."""
	if mode == 'add_bipolar':
		# expects n in -1..1
		if polarity == 'uni':
			return 2.0 * n - 1.0
		return n
	# other modes expect u in 0..1
	if polarity == 'bi':
		return n * 0.5 + 0.5
	return n


def _apply_slot(v, mode, d, source, polarity):
	n_or_u = _adapt_source(source, polarity, mode)
	if mode == 'add_bipolar':
		n = n_or_u
		if n >= 0:
			return v + d * n * (1.0 - v)
		return v + d * n * v
	if mode == 'add_unipolar':
		u = n_or_u
		if d >= 0:
			return v + d * u * (1.0 - v)
		return v + d * u * v
	if mode == 'multiply':
		u = n_or_u
		m = u if d >= 0 else (1.0 - u)
		return v * ((1.0 - abs(d)) + abs(d) * m)
	if mode == 'replace':
		u = n_or_u
		u_prime = u if d >= 0 else (1.0 - u)
		return v + abs(d) * (u_prime - v)
	# unknown mode: leave base
	return v


def _apply_curve(v, curve, map_min, map_max):
	v = max(0.0, min(1.0, v))
	if curve == 'exp':
		if map_min > 0 and map_max > 0:
			return map_min * ((map_max / map_min) ** v)
		return map_min + (map_max - map_min) * (v ** 2)
	if curve == 'log':
		return map_min + (map_max - map_min) * (math.log(1.0 + v * 9.0) / math.log(10.0))
	# lin
	return map_min + v * (map_max - map_min)


class ModEvalExt:
	"""Evaluate mod_matrix rows into resolved (0-1) and mapped channels."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._script = None
		self._bus = None
		self._matrix = None
		self._schema = None
		self._engine = None

	def onInitTD(self):
		self._script = self.ownerComp.op('script_eval')
		self._bus = parent.Rgv.op('control/bus/null_bus')
		self._matrix = parent.Rgv.op('data/mod_matrix')
		self._schema = parent.Rgv.op('data/bus_schema')
		self._engine = parent.Rgv

	def _polarity(self, channel):
		schema = self._schema or parent.Rgv.op('data/bus_schema')
		if schema is None:
			return 'uni'
		try:
			return schema[channel, 'polarity'].val
		except Exception:
			return 'uni'

	def _bus_val(self, channel):
		bus = self._bus or parent.Rgv.op('control/bus/null_bus')
		if bus is None or bus.numChans == 0:
			return 0.0
		try:
			return float(bus[channel])
		except Exception:
			return 0.0

	def Evaluate(self):
		"""Cook slot math into script_eval. Call once per frame."""
		script = self._script or self.ownerComp.op('script_eval')
		matrix = self._matrix or parent.Rgv.op('data/mod_matrix')
		engine = self._engine or parent.Rgv
		if script is None or matrix is None:
			return

		# Group rows by address (slot index order)
		by_addr = {}
		for r in range(1, matrix.numRows):
			address = matrix[r, 'address'].val.strip()
			if not address:
				continue
			slot = int(_f(matrix[r, 'slot'].val, 0))
			by_addr.setdefault(address, []).append((slot, r))

		for address in by_addr:
			by_addr[address].sort(key=lambda t: t[0])

		resolved = {}
		mapped = {}

		registry = {}
		try:
			registry = engine.Registry or {}
		except Exception:
			registry = {}

		for address, slots in by_addr.items():
			info = registry.get(address, {})
			chan = info.get('channel') or engine.SanitizeChannel(address)
			base = engine.GetBase(address)
			if base is None:
				base = 0.5

			v = float(base)
			curve = info.get('curve', 'lin')
			map_min = float(info.get('tableMin', 0.0))
			map_max = float(info.get('tableMax', 1.0))
			ptype = info.get('type', 'float')

			for slot, r in slots:
				source = matrix[r, 'source'].val.strip()
				mode = matrix[r, 'mode'].val.strip().lower()
				d = _f(matrix[r, 'depth'].val, 0.0)
				# optional per-row curve/range override
				try:
					c = matrix[r, 'curve'].val.strip()
					if c:
						curve = c
				except Exception:
					pass
				try:
					mm = matrix[r, 'map_min'].val.strip()
					if mm != '':
						map_min = _f(mm, map_min)
					Mx = matrix[r, 'map_max'].val.strip()
					if Mx != '':
						map_max = _f(Mx, map_max)
				except Exception:
					pass

				src_val = self._bus_val(source)
				polarity = self._polarity(source)
				v = _apply_slot(v, mode, d, src_val, polarity)

				# optional swing clamp after slot
				try:
					smin = matrix[r, 'swing_min'].val.strip()
					smax = matrix[r, 'swing_max'].val.strip()
					if smin != '' or smax != '':
						lo = _f(smin, 0.0) if smin != '' else 0.0
						hi = _f(smax, 1.0) if smax != '' else 1.0
						v = max(lo, min(hi, v))
				except Exception:
					pass

			v = max(0.0, min(1.0, v))
			p = _apply_curve(v, curve, map_min, map_max)
			if ptype == 'int':
				p = float(round(p))

			resolved[chan] = v
			mapped['m_' + chan] = p

		# Also emit bases with no matrix rows so consumers can still read
		for address, info in registry.items():
			chan = info.get('channel')
			if not chan or chan in resolved:
				continue
			base = engine.GetBase(address)
			if base is None:
				continue
			v = max(0.0, min(1.0, float(base)))
			curve = info.get('curve', 'lin')
			map_min = float(info.get('tableMin', 0.0))
			map_max = float(info.get('tableMax', 1.0))
			p = _apply_curve(v, curve, map_min, map_max)
			resolved[chan] = v
			mapped['m_' + chan] = p

		script.clear()
		script.numSamples = 1
		for name, val in resolved.items():
			c = script.appendChan(name)
			c[0] = val
		for name, val in mapped.items():
			c = script.appendChan(name)
			c[0] = val

		return len(resolved)

	def Hello(self):
		return 'ModEval'
