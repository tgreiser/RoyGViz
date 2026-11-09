"""Audio analysis: frame-reduce, onset, breakdown, device fallback."""
from collections import deque
import math

from TDStoreTools import StorageManager
import TDFunctions as TDF


def _rms(samples):
	if not samples:
		return 0.0
	acc = 0.0
	for s in samples:
		acc += s * s
	return math.sqrt(acc / len(samples))


def _avg(samples):
	if not samples:
		return 0.0
	return sum(samples) / len(samples)


def _one_pole(prev, target, time_ms, dt):
	"""Attack/release one-pole toward target. time_ms is the time constant."""
	if time_ms <= 0 or dt <= 0:
		return target
	alpha = 1.0 - math.exp(-dt / (time_ms * 0.001))
	return prev + (target - prev) * alpha


class AudioExt:
	"""Frame-rate audio bus publisher + device fallback."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._env = {'level': 0.0, 'low': 0.0, 'mid': 0.0, 'high': 0.0}
		self._raw = {'level': 0.0, 'low': 0.0, 'mid': 0.0, 'high': 0.0}
		self._prev_raw = {'low': 0.0, 'high': 0.0}
		self._kick = 0.0
		self._hat = 0.0
		self._auto_peak = 0.01
		self._bd_buf = deque()
		self._device_fail_frames = 0
		self._chans = None

		storedItems = [
			{'name': 'AutoPeak', 'default': 0.01, 'readOnly': False,
			 'property': True, 'dependable': True},
		]
		self.stored = StorageManager(self, ownerComp, storedItems)

	def onInitTD(self):
		self._ensure_channels()
		self._wire_file_par()

	def Hello(self):
		return f'AudioExt on {self.ownerComp.path}'

	def _wire_file_par(self):
		fin = self.ownerComp.op('audiofilein')
		if fin is not None:
			fin.par.file.expr = 'parent.Audio.par.File'

	def _ensure_channels(self):
		chop = self.ownerComp.op('script_audio')
		if chop is None:
			return
		names = [
			'audio/level', 'audio/low', 'audio/mid', 'audio/high',
			'audio/kick', 'audio/hat', 'audio/breakdown',
		]
		if self._chans is not None and chop.numChans == len(names):
			return
		chop.clear()
		chop.rate = abs(self.ownerComp.time.rate) or 60
		chop.numSamples = 1
		self._chans = {}
		for n in names:
			self._chans[n] = chop.appendChan(n)

	def _samples(self, chop):
		if chop is None or chop.numChans < 1 or chop.numSamples < 1:
			return []
		chan = chop.chan(0)
		return [chan[i] for i in range(chop.numSamples)]

	def _check_device(self):
		par = self.ownerComp.par
		want_device = par.Inputsource.eval() == 'device'
		dev = self.ownerComp.op('audiodevicein')
		ok = True
		if want_device:
			if dev is None:
				ok = False
			else:
				err_txt = ''
				try:
					err_txt = str(dev.errors()) if callable(dev.errors) else str(dev.errors)
				except Exception:
					err_txt = ''
				rate = float(getattr(dev, 'rate', 0) or 0)
				# Busy/missing device: explicit error text, or no sample rate while active
				if err_txt and err_txt not in ('None', ''):
					ok = False
				elif rate <= 0:
					self._device_fail_frames += 1
					if self._device_fail_frames > 90:
						ok = False
				else:
					self._device_fail_frames = 0
					ok = True
		else:
			self._device_fail_frames = 0
			ok = True

		was = bool(par.Devicewarning.eval())
		now = want_device and not ok
		if now != was:
			par.Devicewarning.val = now

	def Evaluate(self):
		"""Per-frame: envelopes, onset, breakdown → script_audio."""
		self._ensure_channels()
		self._check_device()
		par = self.ownerComp.par
		dt = max(1.0 / max(abs(self.ownerComp.time.rate), 1), 1e-4)

		mono = self.ownerComp.op('null_mono')
		low = self.ownerComp.op('null_low')
		mid = self.ownerComp.op('null_mid')
		high = self.ownerComp.op('null_high')

		raw_level_samples = self._samples(mono)
		inst_peak = max((abs(s) for s in raw_level_samples), default=0.0)
		self._auto_peak = max(self._auto_peak * 0.999, inst_peak)
		self.stored.AutoPeak = self._auto_peak
		auto_amt = float(par.Autogain)
		auto_mul = 1.0
		if auto_amt > 0 and self._auto_peak > 1e-6:
			target = 0.25
			auto_mul = 1.0 + auto_amt * ((target / self._auto_peak) - 1.0)
			auto_mul = max(0.25, min(auto_mul, 8.0))
		scale = auto_mul

		bands = {
			'level': [s * scale for s in raw_level_samples],
			'low': [s * scale for s in self._samples(low)],
			'mid': [s * scale for s in self._samples(mid)],
			'high': [s * scale for s in self._samples(high)],
		}
		gains = {
			'level': 1.0,
			'low': float(par.Lowgain),
			'mid': float(par.Midgain),
			'high': float(par.Highgain),
		}

		attack = float(par.Attack)
		release = float(par.Release)
		gate = float(par.Noisegate)
		onset_th = float(par.Onsetthreshold)

		for name, samples in bands.items():
			rms = _rms(samples) * gains[name]
			self._raw[name] = rms
			prev = self._env[name]
			if rms >= prev:
				self._env[name] = _one_pole(prev, rms, attack, dt)
			else:
				self._env[name] = _one_pole(prev, rms, release, dt)
			if self._env[name] < gate:
				self._env[name] = 0.0
			self._env[name] = max(0.0, min(1.0, self._env[name]))

		low_d = self._raw['low'] - self._prev_raw['low']
		high_d = self._raw['high'] - self._prev_raw['high']
		self._kick = 1.0 if low_d > onset_th * 0.2 and self._raw['low'] > onset_th * 0.05 else 0.0
		self._hat = 1.0 if high_d > onset_th * 0.15 and self._raw['high'] > onset_th * 0.03 else 0.0
		self._prev_raw['low'] = self._raw['low']
		self._prev_raw['high'] = self._raw['high']

		low_avg = _avg(bands['low'])
		win = max(0.5, float(par.Breakdownwindow))
		self._bd_buf.append(low_avg)
		max_n = max(2, int(win / dt))
		while len(self._bd_buf) > max_n:
			self._bd_buf.popleft()
		depth = 0.0
		if self._bd_buf:
			depth = max(self._bd_buf) - min(self._bd_buf)
		breakdown = 1.0 if depth < float(par.Breakdownthreshold) else 0.0

		vals = {
			'audio/level': self._env['level'],
			'audio/low': self._env['low'],
			'audio/mid': self._env['mid'],
			'audio/high': self._env['high'],
			'audio/kick': self._kick,
			'audio/hat': self._hat,
			'audio/breakdown': breakdown,
		}
		for n, v in vals.items():
			ch = self._chans.get(n) if self._chans else None
			if ch is not None:
				ch[0] = float(v)

		return vals
