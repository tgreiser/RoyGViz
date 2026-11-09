"""Phase 3 mod_eval: pack bases/slots/sources into TOPs, publish named CHOPs from GLSL out.

Slot math lives in glsl_modeval. This extension only marshalls data.
"""
from __future__ import annotations

import math

import numpy as np
from TDStoreTools import StorageManager
import TDFunctions as TDF

ADDR_W = 512
SRC_W = 64
PACK_H = 10
EVAL_H = 6

MODE_CODES = {
	'add_bipolar': 1.0,
	'add_unipolar': 2.0,
	'multiply': 3.0,
	'replace': 4.0,
}
TYPE_CODES = {
	'float': 0.0,
	'int': 1.0,
	'toggle': 2.0,
	'menu': 3.0,
	'pulse': 4.0,
}
CURVE_CODES = {
	'lin': 0.0,
	'exp': 1.0,
	'log': 2.0,
}


def _f(cell, default=0.0):
	try:
		return float(cell)
	except Exception:
		return default


# --- parity helpers (tests / reference; GLSL is source of truth at runtime) ---

def _adapt_source(n, polarity, mode):
	if mode == 'add_bipolar':
		if polarity == 'uni':
			return 2.0 * n - 1.0
		return n
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
	return v


def _apply_curve(v, curve, map_min, map_max):
	v = max(0.0, min(1.0, v))
	if curve == 'exp':
		if map_min > 0 and map_max > 0:
			return map_min * ((map_max / map_min) ** v)
		return map_min + (map_max - map_min) * (v ** 2)
	if curve == 'log':
		return map_min + (map_max - map_min) * (math.log(1.0 + v * 9.0) / math.log(10.0))
	return map_min + v * (map_max - map_min)


class ModEvalExt:
	"""Pack TOPs + publish resolved/mapped CHOPs. No per-frame slot math."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._addr_list = []  # ordered addresses in texture columns
		self._addr_index = {}
		self._source_list = []
		self._grab = {}  # address -> gain 0..1
		self._grab_release = {}  # address -> (start_gain, start_time, release_ms)
		self._over_cap = False
		self._last_publish = {}  # channel -> value (resolved + mapped) for freeze hold
		TDF.createProperty(self, 'OverCap', value=False, dependable=True, readOnly=False)
		TDF.createProperty(self, 'AddressCount', value=0, dependable=True, readOnly=False)

	def onInitTD(self):
		self.Rebuild()

	def Hello(self):
		return 'ModEval'

	def SetGrab(self, address, gain):
		"""Phase 4 entry: set grab gain (0 while touched)."""
		self._grab[address] = max(0.0, min(1.0, float(gain)))
		self._grab_release.pop(address, None)

	def ReleaseGrab(self, address, release_ms=250.0):
		"""Linear return of grab gain to 1 over release_ms."""
		cur = self._grab.get(address, 1.0)
		self._grab_release[address] = (cur, absTime.seconds, float(release_ms))

	def _update_grab_releases(self):
		now = absTime.seconds
		done = []
		for address, (start, t0, ms) in self._grab_release.items():
			if ms <= 0:
				self._grab[address] = 1.0
				done.append(address)
				continue
			t = (now - t0) * 1000.0 / ms
			if t >= 1.0:
				self._grab[address] = 1.0
				done.append(address)
			else:
				self._grab[address] = start + (1.0 - start) * t
		for a in done:
			self._grab_release.pop(a, None)

	def _schema_row(self, channel):
		schema = parent.Rgv.op('data/bus_schema')
		if schema is None:
			return {'polarity': 'uni', 'modulatable': True, 'min': 0.0, 'max': 1.0}
		try:
			return {
				'polarity': schema[channel, 'polarity'].val,
				'modulatable': schema[channel, 'modulatable'].val not in ('0', 'false', 'False'),
				'min': _f(schema[channel, 'min'].val, 0.0),
				'max': _f(schema[channel, 'max'].val, 1.0),
			}
		except Exception:
			return {'polarity': 'uni', 'modulatable': True, 'min': 0.0, 'max': 1.0}

	def _bus_val(self, channel):
		rgv = parent.Rgv
		# Prefer live publisher nulls (always current); fall back to merged bus.
		for path in (
			'control/mods/null_mods',
			'control/audio/null_audio',
			'control/clock/null_clock',
			'control/bus/null_bus',
		):
			chop = rgv.op(path)
			if chop is None:
				continue
			try:
				return float(chop[channel])
			except Exception:
				continue
		return 0.0

	def Rebuild(self):
		"""Rebuild address column list and source strip from Registry + mod_matrix."""
		engine = parent.Rgv
		matrix = engine.op('data/mod_matrix')
		registry = {}
		try:
			registry = dict(engine.Registry or {})
		except Exception:
			registry = {}

		# All registered addresses first (stable order)
		addrs = sorted(registry.keys())
		# Ensure matrix targets are included even if somehow missing
		if matrix is not None:
			for r in range(1, matrix.numRows):
				a = matrix[r, 'address'].val.strip()
				if a and a not in registry:
					# still allocate a column so route is visible; base stays 0
					addrs.append(a)
		addrs = list(dict.fromkeys(addrs))  # unique preserve order

		self._over_cap = len(addrs) > ADDR_W
		self.OverCap = self._over_cap
		if self._over_cap:
			addrs = addrs[:ADDR_W]
		self._addr_list = addrs
		self._addr_index = {a: i for i, a in enumerate(addrs)}
		self.AddressCount = len(addrs)

		# Unique sources referenced by matrix (modulatable only)
		sources = []
		seen = set()
		if matrix is not None:
			for r in range(1, matrix.numRows):
				src = matrix[r, 'source'].val.strip()
				if not src or src in seen:
					continue
				info = self._schema_row(src)
				if not info['modulatable']:
					continue
				seen.add(src)
				sources.append(src)
		self._source_list = sources[:SRC_W]

		# Channel name table for debugging / UI
		table = self.ownerComp.op('table_channels')
		if table is not None:
			table.clear()
			table.appendRow(['index', 'address', 'channel'])
			for i, a in enumerate(self._addr_list):
				info = registry.get(a, {})
				chan = info.get('channel') or engine.SanitizeChannel(a)
				table.appendRow([str(i), a, chan])

		return len(self._addr_list)

	def Pack(self):
		"""Write script_pack and script_sources TOPs. Call before GLSL cooks."""
		self._update_grab_releases()
		pack = self.ownerComp.op('script_pack')
		sources_top = self.ownerComp.op('script_sources')
		if pack is None or sources_top is None:
			return

		engine = parent.Rgv
		matrix = engine.op('data/mod_matrix')
		registry = {}
		try:
			registry = dict(engine.Registry or {})
		except Exception:
			registry = {}

		if not self._addr_list:
			self.Rebuild()

		# Group matrix rows
		by_addr = {}
		if matrix is not None:
			for r in range(1, matrix.numRows):
				address = matrix[r, 'address'].val.strip()
				if not address:
					continue
				slot = int(_f(matrix[r, 'slot'].val, 0))
				by_addr.setdefault(address, []).append((slot, r))
			for a in by_addr:
				by_addr[a].sort(key=lambda t: t[0])

		src_index = {s: i for i, s in enumerate(self._source_list)}

		# pack array: (H, W, 4) float32 — TD copyNumpyArray expects this layout
		arr = np.zeros((PACK_H, ADDR_W, 4), dtype=np.float32)
		for i, address in enumerate(self._addr_list):
			info = registry.get(address, {})
			ptype = info.get('type', 'float')
			curve = info.get('curve', 'lin')
			map_min = float(info.get('tableMin', 0.0))
			map_max = float(info.get('tableMax', 1.0))
			base = engine.GetBase(address)
			if base is None:
				base = 0.0
			grab = self._grab.get(address, 1.0)

			arr[0, i] = [
				float(base),
				TYPE_CODES.get(ptype, 0.0),
				CURVE_CODES.get(curve, 0.0),
				1.0,
			]
			arr[1, i] = [map_min, map_max, float(grab), 0.0]

			slots = by_addr.get(address, [])
			for slot, r in slots:
				if slot < 0 or slot > 3:
					continue
				mode = matrix[r, 'mode'].val.strip().lower()
				depth = _f(matrix[r, 'depth'].val, 0.0)
				smooth = _f(matrix[r, 'smooth_ms'].val, 0.0)
				source = matrix[r, 'source'].val.strip()
				info_s = self._schema_row(source)
				if not info_s['modulatable']:
					continue
				si = src_index.get(source)
				if si is None:
					continue
				arr[2 + slot, i] = [
					depth,
					MODE_CODES.get(mode, 0.0),
					smooth,
					float(si + 1),
				]
				smin = matrix[r, 'swing_min'].val.strip()
				smax = matrix[r, 'swing_max'].val.strip()
				if smin != '' or smax != '':
					arr[6 + slot, i] = [
						_f(smin, 0.0) if smin != '' else 0.0,
						_f(smax, 1.0) if smax != '' else 1.0,
						1.0,
						0.0,
					]

		pack.copyNumpyArray(arr)

		src_arr = np.zeros((1, SRC_W, 4), dtype=np.float32)
		for i, name in enumerate(self._source_list):
			info = self._schema_row(name)
			raw = self._bus_val(name)
			pol = 1.0 if info['polarity'] == 'bi' else 0.0
			src_arr[0, i] = [raw, pol, 0.0, 1.0]
		sources_top.copyNumpyArray(src_arr)

	def Publish(self):
		"""Read null_eval TOP y0 → named resolved / mapped channels on script_eval."""
		script = self.ownerComp.op('script_eval')
		top = self.ownerComp.op('null_eval')
		engine = parent.Rgv
		if script is None or top is None:
			return 0

		registry = {}
		try:
			registry = dict(engine.Registry or {})
		except Exception:
			registry = {}

		try:
			pix = top.numpyArray()  # shape (h, w, 4) typically
		except Exception:
			return 0

		# TD numpyArray is often (h, w, 4) with y=0 at bottom or top — sample y0 row carefully
		if pix is None:
			return 0
		h, w = pix.shape[0], pix.shape[1]
		# y0 in shader is bottom or top depending on origin; our glsl writes vUV y=0 as first row.
		# In TD, numpyArray row 0 is usually bottom. vUV.y=0 is bottom. Good.
		row = pix[0] if h >= 1 else None
		if row is None:
			return 0

		script.clear()
		script.numSamples = 1
		n = 0
		for i, address in enumerate(self._addr_list):
			if i >= w:
				break
			info = registry.get(address, {})
			chan = info.get('channel') or engine.SanitizeChannel(address)
			v = float(row[i, 0])
			p = float(row[i, 1])
			# If inactive column (no registry and zero), still publish registry entries
			c = script.appendChan(chan)
			c[0] = v
			cm = script.appendChan('m_' + chan)
			cm[0] = p
			n += 1

		# Registry addresses beyond packed (shouldn't happen) — skip
		return n

	def _push_uniforms(self):
		glsl = self.ownerComp.op('glsl_modeval')
		if glsl is None:
			return
		rgv = parent.Rgv
		try:
			glsl.par.vec0valuex.val = float(rgv.par.Globalmodamount.eval())
		except Exception:
			glsl.par.vec0valuex.val = float(self.ownerComp.par.Globalmodamount.eval())
		try:
			glsl.par.vec1valuex.val = 1.0 if bool(rgv.par.Freeze.eval()) else 0.0
		except Exception:
			glsl.par.vec1valuex.val = 1.0 if bool(self.ownerComp.par.Freeze.eval()) else 0.0
		try:
			glsl.par.vec2valuex.val = 1.0 / max(abs(self.ownerComp.time.rate), 1.0)
		except Exception:
			glsl.par.vec2valuex.val = 1.0 / 60.0

	def _frozen(self):
		try:
			return bool(parent.Rgv.par.Freeze.eval())
		except Exception:
			return bool(self.ownerComp.par.Freeze.eval())

	def _publish_hold(self):
		"""Re-emit last published channels while Freeze is on."""
		script = self.ownerComp.op('script_eval')
		if script is None or not self._last_publish:
			return 0
		script.clear()
		script.numSamples = 1
		for name, val in self._last_publish.items():
			c = script.appendChan(name)
			c[0] = float(val)
		for name in ('select_resolved', 'select_mapped', 'null_resolved', 'null_mapped'):
			op_ = self.ownerComp.op(name)
			if op_ is not None:
				op_.cook(force=True)
		return len(self._last_publish)

	def Evaluate(self):
		"""Frame entry: uniforms → pack → cook GLSL → publish named CHOPs."""
		if self._frozen() and self._last_publish:
			# Keep GLSL smooth feedback from being force-cleared; hold in Python.
			return self._publish_hold()

		self._push_uniforms()
		self.Pack()
		self._push_uniforms()
		# Cook the chain explicitly so Publish sees this frame's GPU result.
		for name in ('script_pack', 'script_sources', 'glsl_modeval', 'null_eval'):
			op_ = self.ownerComp.op(name)
			if op_ is not None:
				op_.cook(force=True)
		# numpyArray can lag one download behind a just-forced cook; flush then re-read.
		null_eval = self.ownerComp.op('null_eval')
		if null_eval is not None:
			_ = null_eval.numpyArray()
			null_eval.cook(force=True)
		n = self.Publish()
		# Snapshot for freeze
		script = self.ownerComp.op('script_eval')
		if script is not None:
			self._last_publish = {c.name: float(c[0]) for c in script.chans()}
		# script_eval is written outside cook; force downstream selects/nulls to refresh.
		for name in ('select_resolved', 'select_mapped', 'null_resolved', 'null_mapped'):
			op_ = self.ownerComp.op(name)
			if op_ is not None:
				op_.cook(force=True)
		return n
