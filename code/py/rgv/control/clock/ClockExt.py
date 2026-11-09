"""Tempo / phase engine: Internal, Tap, packet slew for future Pro DJ Link."""
import math
import time

from TDStoreTools import StorageManager
import TDFunctions as TDF


def _wrap01(x):
	return x - math.floor(x)


def _wrap_err(target, local):
	"""Wrapped distance target-local in [-0.5, 0.5]."""
	return _wrap01(target - local + 0.5) - 0.5


def _clamp(v, lo, hi):
	return lo if v < lo else hi if v > hi else v


class ClockExt:
	"""Owns beat/bar phase and publishes clock/* channels."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._beat_phase = 0.0
		self._bar_phase = 0.0
		self._prev_beat = 0.0
		self._prev_bar = 0.0
		self._session_bpm = 120.0
		self._packet_mode = False
		self._last_packet_t = 0.0
		self._pending_packet = None
		self._tap_times = []
		self._chans = None
		self._nudge = 0.0

		storedItems = [
			{'name': 'BeatPhase', 'default': 0.0, 'readOnly': False,
			 'property': True, 'dependable': True},
			{'name': 'BarPhase', 'default': 0.0, 'readOnly': False,
			 'property': True, 'dependable': True},
			{'name': 'SessionBpm', 'default': 120.0, 'readOnly': False,
			 'property': True, 'dependable': True},
		]
		self.stored = StorageManager(self, ownerComp, storedItems)

	def onInitTD(self):
		self._session_bpm = float(self.ownerComp.par.Bpm)
		self.stored.SessionBpm = self._session_bpm
		self._ensure_channels()

	def Hello(self):
		return f'ClockExt on {self.ownerComp.path}'

	def _feel(self):
		try:
			return float(self.ownerComp.par.Feel.eval())
		except Exception:
			return 1.0

	def _sig_beats(self):
		return max(1, int(self.ownerComp.par.Signature1))

	def _ensure_channels(self):
		chop = self.ownerComp.op('script_clock')
		if chop is None:
			return
		names = [
			'clock/bpm', 'clock/feel', 'clock/beat', 'clock/bar',
			'clock/beat_pulse', 'clock/bar_pulse',
		]
		if self._chans is not None and chop.numChans == len(names):
			return
		chop.clear()
		chop.rate = abs(self.ownerComp.time.rate) or 60
		chop.numSamples = 1
		self._chans = {}
		for n in names:
			self._chans[n] = chop.appendChan(n)

	def Tap(self):
		"""Register a tap; average last 4–8, drop >15% outliers, 2s gap reset."""
		now = time.time()
		if self._tap_times and (now - self._tap_times[-1]) > 2.0:
			self._tap_times = []
		self._tap_times.append(now)
		# keep last 8
		self._tap_times = self._tap_times[-8:]
		if len(self._tap_times) < 2:
			return float(self.ownerComp.par.Bpm)

		intervals = [
			self._tap_times[i] - self._tap_times[i - 1]
			for i in range(1, len(self._tap_times))
		]
		# use last 4–8 intervals
		intervals = intervals[-7:]
		med = sorted(intervals)[len(intervals) // 2]
		kept = [iv for iv in intervals if abs(iv - med) / med <= 0.15]
		if not kept:
			kept = [med]
		avg = sum(kept) / len(kept)
		bpm = 60.0 / avg if avg > 1e-6 else float(self.ownerComp.par.Bpm)
		bpm = _clamp(bpm, 20.0, 300.0)
		self.ownerComp.par.Bpm.val = bpm
		self._session_bpm = bpm
		self.stored.SessionBpm = bpm
		# leave Source as Tap if user selected it; also fine on Internal
		return bpm

	def Resync(self):
		self._beat_phase = 0.0
		self._bar_phase = 0.0
		self.stored.BeatPhase = 0.0
		self.stored.BarPhase = 0.0

	def Half(self):
		feel = self._feel()
		# cycle down: 2 -> 1 -> 0.5 (stay)
		order = [2.0, 1.0, 0.5]
		try:
			i = order.index(feel)
		except ValueError:
			i = 1
		i = min(i + 1, len(order) - 1)
		self.ownerComp.par.Feel.val = str(order[i]).replace('.0', '') if order[i] != 0.5 else '0.5'
		# menu names are '0.5','1','2'
		self.ownerComp.par.Feel.val = {2.0: '2', 1.0: '1', 0.5: '0.5'}[order[i]]

	def Double(self):
		feel = self._feel()
		order = [0.5, 1.0, 2.0]
		try:
			i = order.index(feel)
		except ValueError:
			i = 1
		i = min(i + 1, len(order) - 1)
		self.ownerComp.par.Feel.val = {0.5: '0.5', 1.0: '1', 2.0: '2'}[order[i]]

	def Nudge(self, direction):
		"""direction: -1 left, +1 right. Small phase step (~10ms of beat)."""
		bpm = max(self._effective_bpm_for_ramp(), 1.0)
		step = (0.010 * bpm / 60.0) * float(direction)
		self._beat_phase = _wrap01(self._beat_phase + step)
		# bar follows signature
		sig = self._sig_beats()
		self._bar_phase = _wrap01(self._bar_phase + step / sig)

	def PushPacket(self, bpm, beat_phase, bar_phase=None, now=None):
		"""Ingest a network tempo packet. Pro DJ Link will call this later."""
		if now is None:
			now = time.time()
		self._pending_packet = {
			'bpm': float(bpm),
			'beat': _wrap01(float(beat_phase)),
			'bar': None if bar_phase is None else _wrap01(float(bar_phase)),
			't': now,
		}
		self._packet_mode = True
		self._last_packet_t = now
		self.ownerComp.par.Syncwarning.val = False
		return self._pending_packet

	def InjectTestPacket(self):
		"""Synthetic packet for acceptance: jump phase by ~0.3 and set BPM."""
		bpm = float(self.ownerComp.par.Bpm)
		# force a visible phase error relative to current free-run
		target = _wrap01(self._beat_phase + 0.3)
		return self.PushPacket(bpm, target, bar_phase=self._bar_phase)

	def _effective_bpm_for_ramp(self):
		feel = self._feel()
		if self._packet_mode and not bool(self.ownerComp.par.Syncwarning.eval()):
			# session bpm untouched; feel scales beat ramp only
			return self._session_bpm * feel
		# Internal / Tap / dropout free-run: felt tempo
		return float(self.ownerComp.par.Bpm) * feel

	def _publish_bpm(self):
		feel = self._feel()
		if self._packet_mode and not bool(self.ownerComp.par.Syncwarning.eval()):
			return self._session_bpm  # session tempo
		return float(self.ownerComp.par.Bpm) * feel  # felt

	def Evaluate(self):
		self._ensure_channels()
		par = self.ownerComp.par
		dt = max(1.0 / max(abs(self.ownerComp.time.rate), 1), 1e-4)
		now = time.time()
		sig = self._sig_beats()
		feel = self._feel()

		# Dropout: no packet for 2s while in packet mode
		if self._packet_mode and (now - self._last_packet_t) > 2.0:
			src = str(par.Source.eval())
			if src in ('internal', 'tap'):
				# Phase-2 sources: leave packet test mode and resume local feel BPM
				self._packet_mode = False
				par.Syncwarning.val = False
			else:
				par.Syncwarning.val = True
				# hold last session bpm, free-run until packets return

		pkt = self._pending_packet
		self._pending_packet = None

		if pkt is not None:
			self._session_bpm = pkt['bpm']
			self.stored.SessionBpm = self._session_bpm
			self._last_packet_t = pkt['t']
			par.Syncwarning.val = False
			# slew beat phase toward packet
			err = _wrap_err(pkt['beat'], self._beat_phase)
			step = 0.25 * err
			bpm_eff = max(self._session_bpm * feel, 1.0)
			max_step = 0.010 * bpm_eff / 60.0
			step = _clamp(step, -max_step, max_step)
			self._beat_phase = _wrap01(self._beat_phase + step)
			if pkt['bar'] is not None:
				berr = _wrap_err(pkt['bar'], self._bar_phase)
				bstep = _clamp(0.25 * berr, -max_step / sig, max_step / sig)
				self._bar_phase = _wrap01(self._bar_phase + bstep)

		bpm_ramp = max(self._effective_bpm_for_ramp(), 1e-6)
		# free-run advance every frame (also after slew)
		self._beat_phase = _wrap01(self._beat_phase + (bpm_ramp / 60.0) * dt)
		# bar advances at session/felt beat rate without feel scale on packet?
		# Plan: clock/bar stays on source signature. Bar rate = bpm_session/60/sig
		# On Internal, felt bpm drives bar too so downbeats match felt tempo.
		if self._packet_mode and not bool(par.Syncwarning.eval()):
			bar_bpm = self._session_bpm  # session
		else:
			bar_bpm = float(par.Bpm) * feel
		self._bar_phase = _wrap01(self._bar_phase + (bar_bpm / 60.0) * dt / sig)

		latency_ms = float(par.Latency)
		lat_beats = latency_ms * bpm_ramp / 60000.0
		display_beat = _wrap01(self._beat_phase + lat_beats)
		display_bar = _wrap01(self._bar_phase + lat_beats / sig)

		beat_pulse = 1.0 if display_beat < self._prev_beat else 0.0
		bar_pulse = 1.0 if display_bar < self._prev_bar else 0.0
		self._prev_beat = display_beat
		self._prev_bar = display_bar

		self.stored.BeatPhase = display_beat
		self.stored.BarPhase = display_bar

		vals = {
			'clock/bpm': self._publish_bpm(),
			'clock/feel': feel,
			'clock/beat': display_beat,
			'clock/bar': display_bar,
			'clock/beat_pulse': beat_pulse,
			'clock/bar_pulse': bar_pulse,
		}
		for n, v in vals.items():
			ch = self._chans.get(n) if self._chans else None
			if ch is not None:
				ch[0] = float(v)
		return vals

	def onParPulse(self, par):
		name = par.name
		if name == 'Tap':
			self.Tap()
		elif name == 'Resync':
			self.Resync()
		elif name == 'Half':
			self.Half()
		elif name == 'Double':
			self.Double()
		elif name == 'Nudgel':
			self.Nudge(-1)
		elif name == 'Nudger':
			self.Nudge(1)
		elif name == 'Injectpacket':
			self.InjectTestPacket()
