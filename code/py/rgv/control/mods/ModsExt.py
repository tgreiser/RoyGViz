"""Staged modulators: LFOs, envelopes, sequencers, random, macros → bus channels.

Reads audio/clock from sibling nulls and last-frame mapped values via feedback_mapped
so modulator parameters that are matrix targets stay one frame late (cycle breaker).
"""
from __future__ import annotations

import math
import random

from TDStoreTools import StorageManager
import TDFunctions as TDF


def _clamp(v, lo, hi):
	return lo if v < lo else hi if v > hi else v


def _wrap01(x):
	return x - math.floor(x)


def _par_f(par, default=0.0):
	try:
		return float(par.eval())
	except Exception:
		return default


def _par_b(par, default=False):
	try:
		return bool(par.eval())
	except Exception:
		return default


def _wave(phase, shape):
	"""phase in 0..1 → bipolar -1..1."""
	p = _wrap01(phase)
	if shape == 'triangle':
		if p < 0.5:
			return p * 4.0 - 1.0
		return 3.0 - p * 4.0
	if shape == 'saw':
		return p * 2.0 - 1.0
	if shape == 'square':
		return 1.0 if p < 0.5 else -1.0
	if shape == 'random':
		return (hash(int(p * 64)) % 1000) / 500.0 - 1.0
	return math.sin(p * math.pi * 2.0)


TRIG_SOURCES = (
	'audio/kick',
	'audio/hat',
	'clock/beat_pulse',
	'clock/bar_pulse',
	'audio/breakdown',
)


class ModsExt:
	"""Publish lfo*, env*, seq*, rand*, macro* onto script_mods."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._chans = None
		self._lfo_phase = [0.0] * 8
		self._env = [0.0] * 4
		self._env_dir = [0] * 4
		self._seq_step = [0, 0]
		self._seq_prev_div = [0.0, 0.0]
		self._rand = [0.0] * 4
		self._rand_prev_div = [0.0] * 4
		self._prev_trig = {s: 0.0 for s in TRIG_SOURCES}
		self._retrigger_armed = [False] * 8

	def onInitTD(self):
		self._ensure_channels()

	def Hello(self):
		return f'ModsExt on {self.ownerComp.path}'

	def _ensure_channels(self):
		chop = self.ownerComp.op('script_mods')
		if chop is None:
			return
		names = (
			[f'lfo{i}' for i in range(1, 9)]
			+ [f'env{i}' for i in range(1, 5)]
			+ ['seq1', 'seq2']
			+ [f'rand{i}' for i in range(1, 5)]
			+ [f'macro{i}' for i in range(1, 9)]
		)
		if self._chans is not None and chop.numChans == len(names):
			return
		chop.clear()
		chop.rate = abs(self.ownerComp.time.rate) or 60
		chop.numSamples = 1
		self._chans = {}
		for n in names:
			self._chans[n] = chop.appendChan(n)

	def _bus_val(self, name, default=0.0):
		rgv = parent.Rgv
		for path in (
			'control/audio/null_audio',
			'control/clock/null_clock',
			'control/bus/null_bus',
		):
			chop = rgv.op(path)
			if chop is None:
				continue
			try:
				return float(chop[name])
			except Exception:
				continue
		return default

	def _mapped(self, channel, default):
		fb = self.ownerComp.op('feedback_mapped')
		if fb is None or fb.numChans == 0:
			return default
		try:
			return float(fb[channel])
		except Exception:
			return default

	def _lfo_rate_hz(self, i, base_rate, sync):
		bpm = max(self._bus_val('clock/bpm', 120.0), 1.0)
		beat_hz = bpm / 60.0
		if sync == 'beat':
			return max(base_rate, 1e-6) * beat_hz
		if sync == 'bar':
			clock = parent.Rgv.op('control/clock')
			beats = 4
			if clock is not None:
				try:
					beats = max(1, int(clock.par.Signature1))
				except Exception:
					beats = 4
			return max(base_rate, 1e-6) * beat_hz / beats
		return max(base_rate, 0.0)

	def onParPulse(self, par):
		name = par.name
		if name.startswith('Lfo') and name.endswith('retrigger'):
			try:
				idx = int(name[3]) - 1
			except Exception:
				return
			if 0 <= idx < 8:
				self._retrigger_armed[idx] = True

	def Evaluate(self):
		self._ensure_channels()
		par = self.ownerComp.par
		dt = max(1.0 / max(abs(self.ownerComp.time.rate), 1), 1e-4)
		vals = {}

		trig_now = {}
		trig_rise = {}
		for src in TRIG_SOURCES:
			v = self._bus_val(src, 0.0)
			trig_now[src] = v
			trig_rise[src] = v >= 0.5 and self._prev_trig.get(src, 0.0) < 0.5
			self._prev_trig[src] = v

		for i in range(8):
			n = i + 1
			on = _par_b(getattr(par, f'Lfo{n}on', None), True) if hasattr(par, f'Lfo{n}on') else True
			chan = f'lfo{n}'
			if not on:
				vals[chan] = 0.0
				continue

			rate_base = _par_f(getattr(par, f'Lfo{n}rate'), 0.25)
			depth_base = _par_f(getattr(par, f'Lfo{n}depth'), 1.0)
			rate = self._mapped(f'mods_lfo{n}_rate', rate_base)
			depth = _clamp(self._mapped(f'mods_lfo{n}_depth', depth_base), 0.0, 1.0)
			shape = str(getattr(par, f'Lfo{n}shape').eval()) if hasattr(par, f'Lfo{n}shape') else 'sine'
			sync = str(getattr(par, f'Lfo{n}sync').eval()) if hasattr(par, f'Lfo{n}sync') else 'free'

			if self._retrigger_armed[i]:
				self._lfo_phase[i] = 0.0
				self._retrigger_armed[i] = False

			hz = self._lfo_rate_hz(i, rate, sync)
			if sync == 'beat':
				beat = self._bus_val('clock/beat', 0.0)
				self._lfo_phase[i] = _wrap01(beat * max(rate, 1e-6))
			elif sync == 'bar':
				bar = self._bus_val('clock/bar', 0.0)
				self._lfo_phase[i] = _wrap01(bar * max(rate, 1e-6))
			else:
				self._lfo_phase[i] = _wrap01(self._lfo_phase[i] + hz * dt)

			vals[chan] = _wave(self._lfo_phase[i], shape) * depth

		for i in range(4):
			n = i + 1
			on = _par_b(getattr(par, f'Env{n}on'), True) if hasattr(par, f'Env{n}on') else True
			chan = f'env{n}'
			if not on:
				self._env[i] = 0.0
				vals[chan] = 0.0
				continue

			atk_ms = max(self._mapped(f'mods_env{n}_attack', _par_f(getattr(par, f'Env{n}attack'), 10.0)), 0.0)
			rel_ms = max(self._mapped(f'mods_env{n}_release', _par_f(getattr(par, f'Env{n}release'), 200.0)), 1.0)
			trig = str(getattr(par, f'Env{n}trig').eval()) if hasattr(par, f'Env{n}trig') else 'audio/kick'

			if trig_rise.get(trig, False):
				self._env_dir[i] = 1

			e = self._env[i]
			if self._env_dir[i] == 1:
				step = dt * 1000.0 / max(atk_ms, 0.1)
				e = min(1.0, e + step)
				if e >= 1.0:
					self._env_dir[i] = -1
			elif self._env_dir[i] == -1:
				step = dt * 1000.0 / max(rel_ms, 0.1)
				e = max(0.0, e - step)
				if e <= 0.0:
					self._env_dir[i] = 0
			self._env[i] = e
			vals[chan] = e

		for i in range(2):
			n = i + 1
			on = _par_b(getattr(par, f'Seq{n}on'), True) if hasattr(par, f'Seq{n}on') else True
			chan = f'seq{n}'
			if not on:
				vals[chan] = 0.0
				continue
			div = max(_par_f(getattr(par, f'Seq{n}div'), 1.0), 0.0625)
			beat = self._bus_val('clock/beat', 0.0)
			phase_div = _wrap01(beat / div) if div > 0 else beat
			if phase_div < self._seq_prev_div[i]:
				table = self.ownerComp.op(f'seq{n}_steps')
				length = 8
				if table is not None and table.numRows > 1:
					length = max(1, table.numRows - 1)
				self._seq_step[i] = (self._seq_step[i] + 1) % length
			self._seq_prev_div[i] = phase_div

			step_val = 0.0
			table = self.ownerComp.op(f'seq{n}_steps')
			if table is not None and table.numRows > 1:
				row = 1 + self._seq_step[i]
				if row < table.numRows:
					try:
						step_val = float(table[row, 0].val)
					except Exception:
						step_val = 0.0
			vals[chan] = _clamp(step_val, 0.0, 1.0)

		for i in range(4):
			n = i + 1
			on = _par_b(getattr(par, f'Rand{n}on'), True) if hasattr(par, f'Rand{n}on') else True
			chan = f'rand{n}'
			if not on:
				vals[chan] = 0.0
				continue
			div = max(_par_f(getattr(par, f'Rand{n}div'), 1.0), 0.0625)
			beat = self._bus_val('clock/beat', 0.0)
			phase_div = _wrap01(beat / div) if div > 0 else beat
			if phase_div < self._rand_prev_div[i]:
				self._rand[i] = random.random()
			self._rand_prev_div[i] = phase_div
			vals[chan] = self._rand[i]

		for i in range(8):
			n = i + 1
			base = _par_f(getattr(par, f'Macro{n}'), 0.0) if hasattr(par, f'Macro{n}') else 0.0
			vals[f'macro{n}'] = _clamp(self._mapped(f'mods_macro{n}', base), 0.0, 1.0)

		for name, v in vals.items():
			ch = self._chans.get(name) if self._chans else None
			if ch is not None:
				ch[0] = float(v)
		return vals
