"""MIDI learn, factory profiles, base writes, LED feedback."""
import json
import os
import time

from TDStoreTools import StorageManager
import TDFunctions as TDF

MAP_HEADERS = [
	'device', 'channel', 'msg', 'number', 'mode', 'dest_kind', 'dest',
	'pickup', 'min', 'max', 'curve', 'source',
]

FEEDBACK_IGNORE_S = 0.05
REL_FAST_MS = 30.0
REL_STEP = 1.0 / 128.0
CC_RELEASE_MS = 80.0


def _clamp01(v):
	return 0.0 if v < 0.0 else 1.0 if v > 1.0 else float(v)


def _norm_msg(message):
	m = str(message or '').lower()
	if 'control' in m or m == 'cc':
		return 'cc'
	if 'note' in m:
		return 'note'
	return m.replace(' ', '_')


def _is_note_on(message, value):
	m = str(message or '').lower()
	if 'note off' in m:
		return False
	if 'note on' in m:
		return float(value) > 0
	return False


def _is_note_off(message, value):
	m = str(message or '').lower()
	if 'note off' in m:
		return True
	if 'note on' in m:
		return float(value) <= 0
	return False


def _apply_curve(u, curve):
	u = _clamp01(u)
	c = (curve or 'lin').lower()
	if c == 'exp':
		return u * u
	if c == 'log':
		import math
		return math.log(1.0 + u * 9.0) / math.log(10.0)
	return u


class MidiExt:
	"""Event-driven MIDI learn and dispatch."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._bindings = {}  # key -> row dict
		self._factory = {}  # key -> row dict
		self._pending = None  # (dest_kind, dest, mode)
		self._shift = False
		self._feedback = {}  # key -> expire time
		self._touch = {}  # address -> True while held
		self._cc_release = {}  # key -> (address, expire)
		self._rel_last_t = {}  # key -> time
		self._bus_vals = {}  # channel -> 0..1
		self._led_sent = {}  # note -> velocity last sent
		self._learn_flash = False
		self._profile_id = 'apc_mini_mk2'
		self._profile_meta = {}
		self._device_name = ''
		TDF.createProperty(self, 'ShiftHeld', value=False, dependable=True, readOnly=False)
		TDF.createProperty(self, 'BindingCount', value=0, dependable=True, readOnly=False)

	def onInitTD(self):
		self._ensure_map_headers()
		self._ensure_led_headers()
		self.LoadActiveProfile()
		self.UpdateLeds()

	def Hello(self):
		return f'MidiExt bindings={len(self._bindings)}'

	# ---- tables / paths -------------------------------------------------

	def _rgv(self):
		return parent.Rgv

	def _map_dat(self):
		return self._rgv().op('data/midi_map')

	def _project_folder(self):
		return project.folder.replace('\\', '/')

	def _profile_path(self, profile_id=None):
		pid = profile_id or self._profile_id
		return os.path.join(self._project_folder(), 'controllers', f'{pid}.json')

	def _ensure_map_headers(self):
		dat = self._map_dat()
		if dat is None:
			return
		if dat.numRows == 0 or dat[0, 0].val != 'device':
			dat.clear()
			dat.appendRow(MAP_HEADERS)

	def _ensure_led_headers(self):
		led = self.ownerComp.op('table_led')
		if led is None:
			return
		if led.numRows == 0 or led[0, 0].val != 'note':
			led.clear()
			led.appendRow(['note', 'channel', 'velocity', 'role'])

	def _key(self, device, channel, msg, number):
		return f'{device}|{int(channel)}|{msg}|{int(number)}'

	def _row_key(self, row):
		return self._key(row['device'], row['channel'], row['msg'], row['number'])

	def _parse_row(self, cells):
		# cells keyed by header name
		return {
			'device': cells.get('device', ''),
			'channel': int(float(cells.get('channel', 1) or 1)),
			'msg': str(cells.get('msg', 'cc')).lower(),
			'number': int(float(cells.get('number', 1) or 1)),
			'mode': str(cells.get('mode', 'absolute')).lower(),
			'dest_kind': str(cells.get('dest_kind', 'noop')).lower(),
			'dest': str(cells.get('dest', '')),
			'pickup': str(cells.get('pickup', 'armed')).lower(),
			'min': float(cells.get('min', 0) or 0),
			'max': float(cells.get('max', 1) or 1),
			'curve': str(cells.get('curve', 'lin')).lower(),
			'source': str(cells.get('source', 'factory')).lower(),
		}

	# ---- profile load / save --------------------------------------------

	def LoadActiveProfile(self):
		rg = self._rgv()
		try:
			self._profile_id = str(rg.par.Midiprofile.eval())
		except Exception:
			self._profile_id = 'apc_mini_mk2'
		path = self._profile_path()
		if not os.path.isfile(path):
			debug(f'MidiExt: missing profile {path}')
			self._factory = {}
			self._bindings = {}
			self._sync_map_table()
			return False
		with open(path, 'r', encoding='utf-8') as f:
			data = json.load(f)
		self._profile_meta = {
			'id': data.get('id', self._profile_id),
			'label': data.get('label', self._profile_id),
			'device_in': data.get('device_in', ''),
			'device_out': data.get('device_out', data.get('device_in', '')),
			'channel': int(data.get('channel', 1)),
		}
		self._device_name = self._profile_meta['device_in'] or self._profile_id
		self._apply_device_table()
		self._factory = {}
		for raw in data.get('bindings', []):
			row = self._parse_row(raw)
			if not row['device']:
				row['device'] = self._device_name
			row['source'] = row.get('source') or 'factory'
			self._factory[self._row_key(row)] = row
		# Start from factory; overlay learned from saved profile bindings marked learned
		self._bindings = dict(self._factory)
		for raw in data.get('learned', []):
			row = self._parse_row(raw)
			if not row['device']:
				row['device'] = self._device_name
			row['source'] = 'learned'
			self._bindings[self._row_key(row)] = row
		self._bus_vals = {}
		self._rebuild_bus_sources()
		self._sync_map_table()
		self.BindingCount = len(self._bindings)
		self.UpdateLeds()
		return True

	def SaveActiveProfile(self):
		path = self._profile_path()
		os.makedirs(os.path.dirname(path), exist_ok=True)
		learned = []
		for key, row in self._bindings.items():
			if row.get('source') != 'learned':
				continue
			learned.append({
				'device': row['device'],
				'channel': row['channel'],
				'msg': row['msg'],
				'number': row['number'],
				'mode': row['mode'],
				'dest_kind': row['dest_kind'],
				'dest': row['dest'],
				'pickup': row['pickup'],
				'min': row['min'],
				'max': row['max'],
				'curve': row['curve'],
				'source': 'learned',
			})
		# Preserve factory bindings from file; only rewrite learned + pickup on factory
		existing = {}
		if os.path.isfile(path):
			with open(path, 'r', encoding='utf-8') as f:
				existing = json.load(f)
		# Update pickup state on factory bindings from live map
		factory_out = []
		for raw in existing.get('bindings', []):
			row = self._parse_row(raw)
			if not row['device']:
				row['device'] = self._device_name
			key = self._row_key(row)
			live = self._bindings.get(key)
			if live and live.get('source') != 'learned':
				row['pickup'] = live.get('pickup', row['pickup'])
			factory_out.append({
				'device': row['device'],
				'channel': row['channel'],
				'msg': row['msg'],
				'number': row['number'],
				'mode': row['mode'],
				'dest_kind': row['dest_kind'],
				'dest': row['dest'],
				'pickup': row['pickup'],
				'min': row['min'],
				'max': row['max'],
				'curve': row['curve'],
				'source': 'factory',
			})
		out = {
			'id': existing.get('id', self._profile_id),
			'label': existing.get('label', self._profile_id),
			'device_in': existing.get('device_in', self._profile_meta.get('device_in', '')),
			'device_out': existing.get('device_out', self._profile_meta.get('device_out', '')),
			'channel': existing.get('channel', self._profile_meta.get('channel', 1)),
			'bindings': factory_out or existing.get('bindings', []),
			'learned': learned,
			'led': existing.get('led', {}),
		}
		with open(path, 'w', encoding='utf-8') as f:
			json.dump(out, f, indent=2)
			f.write('\n')
		return path

	def _apply_device_table(self):
		dev = self.ownerComp.op('device')
		if dev is None:
			return
		in_name = self._profile_meta.get('device_in', '')
		out_name = self._profile_meta.get('device_out', in_name)
		ch = int(self._profile_meta.get('channel', 1))
		dev.clear()
		dev.appendRow(['id', 'indevice', 'outdevice', 'definition', 'channel'])
		dev.appendRow(['1', in_name, out_name, '', str(ch)])

	def _sync_map_table(self):
		dat = self._map_dat()
		if dat is None:
			return
		self._ensure_map_headers()
		while dat.numRows > 1:
			dat.deleteRow(1)
		for key in sorted(self._bindings.keys()):
			r = self._bindings[key]
			dat.appendRow([
				str(r['device']), str(r['channel']), str(r['msg']), str(r['number']),
				str(r['mode']), str(r['dest_kind']), str(r['dest']),
				str(r['pickup']), str(r['min']), str(r['max']),
				str(r['curve']), str(r['source']),
			])
		self.BindingCount = len(self._bindings)

	# ---- learn ----------------------------------------------------------

	def ArmDest(self, dest_kind, dest, mode='absolute'):
		"""UI calls this while Learn is on."""
		rg = self._rgv()
		if not bool(rg.par.Learn.eval()):
			return False
		self._pending = (str(dest_kind), str(dest), str(mode))
		rg.par.Learnarmed.val = f'{dest_kind}:{dest}'
		self.UpdateLeds()
		return True

	def ClearPending(self):
		self._pending = None
		try:
			self._rgv().par.Learnarmed.val = ''
		except Exception:
			pass

	def ClearBinding(self, device, channel, msg, number):
		key = self._key(device, channel, msg, number)
		factory = self._factory.get(key)
		if factory:
			self._bindings[key] = dict(factory)
		elif key in self._bindings:
			del self._bindings[key]
		self._rebuild_bus_sources()
		self._sync_map_table()
		self.SaveActiveProfile()
		self.UpdateLeds()
		return True

	def ClearArmedOrSelected(self):
		"""Clear binding for Learnarmed physical target if encoded as midi key."""
		armed = str(self._rgv().par.Learnarmed.eval() or '')
		if armed.startswith('midi:'):
			parts = armed.split(':')
			if len(parts) >= 5:
				return self.ClearBinding(parts[1], parts[2], parts[3], parts[4])
		return False

	# ---- MIDI event entry ----------------------------------------------

	def OnMidi(self, message, channel, index, value, input_flag=True):
		if not input_flag:
			return
		msg = _norm_msg(message)
		if msg not in ('cc', 'note'):
			return
		device = self._device_name or self._profile_id
		number = int(index)
		channel = int(channel)
		value = float(value)
		key = self._key(device, channel, msg, number)

		# Ignore our own LED feedback echoes
		expire = self._feedback.get(key)
		if expire and time.time() < expire:
			return

		# Hardware Shift latch (dest_kind == shift)
		row = self._bindings.get(key)
		if row and row.get('dest_kind') == 'shift':
			if msg == 'note':
				self._shift = _is_note_on(message, value)
				self.ShiftHeld = self._shift
			return

		rg = self._rgv()
		learning = bool(rg.par.Learn.eval())

		# Learn capture
		if learning and self._pending is not None:
			if msg == 'note' and _is_note_off(message, value):
				return
			if msg == 'cc' and value <= 0:
				return
			dest_kind, dest, mode = self._pending
			new_row = {
				'device': device,
				'channel': channel,
				'msg': msg,
				'number': number,
				'mode': mode or 'absolute',
				'dest_kind': dest_kind,
				'dest': dest,
				'pickup': 'armed',
				'min': 0.0,
				'max': 1.0,
				'curve': 'lin',
				'source': 'learned',
			}
			self._bindings[key] = new_row
			# Learned control leaves the bus
			self._bus_vals.pop(self._bus_channel_name(device, msg, number), None)
			self._rebuild_bus_sources()
			self._sync_map_table()
			self.SaveActiveProfile()
			self.ClearPending()
			self.UpdateLeds()
			return

		# Toggle Learn from factory binding (note or CC)
		if row and row.get('dest_kind') == 'latch' and row.get('dest') == 'learn':
			fire = False
			if msg == 'note':
				fire = _is_note_on(message, value)
			elif msg == 'cc':
				fire = float(value) > 0
			if fire:
				rg.par.Learn.val = not bool(rg.par.Learn.eval())
				if not rg.par.Learn.eval():
					self.ClearPending()
				self.UpdateLeds()
				self._schedule_learn_flash()
			return

		if row is None:
			# Unbound → bus source
			self._publish_bus(device, msg, number, value)
			return

		if row.get('dest_kind') == 'bus_source':
			self._publish_bus(device, msg, number, value)
			return

		self._dispatch(row, message, value, key)

	def _bus_channel_name(self, device, msg, number):
		safe = ''.join(c if c.isalnum() else '_' for c in str(device))
		return f'midi/{safe}/{msg}{int(number)}'

	def _publish_bus(self, device, msg, number, value):
		# MIDI In DAT value is typically 0-127 for notes/cc when not normalized
		v = float(value)
		if v > 1.0:
			v = v / 127.0
		chan = self._bus_channel_name(device, msg, number)
		self._bus_vals[chan] = _clamp01(v)
		self._ensure_schema_channel(chan)
		self._publish_sources_chop()

	def _ensure_schema_channel(self, chan):
		schema = self._rgv().op('data/bus_schema')
		if schema is None:
			return
		for r in range(1, schema.numRows):
			if schema[r, 'name'].val == chan:
				return
		schema.appendRow([chan, '0', '1', 'uni', '1'])

	def _rebuild_bus_sources(self):
		# Drop bus vals for controls that now write bases
		keep = {}
		for key, row in self._bindings.items():
			if row.get('dest_kind') in ('bus_source',):
				chan = self._bus_channel_name(row['device'], row['msg'], row['number'])
				keep[chan] = self._bus_vals.get(chan, 0.0)
		# Unbound runtime values already in _bus_vals stay until rebound
		for chan, v in list(self._bus_vals.items()):
			if chan.startswith('midi/'):
				# keep unbound dynamics
				keep.setdefault(chan, v)
		# Remove channels that are now bound to non-bus destinations
		for key, row in self._bindings.items():
			if row.get('dest_kind') not in ('bus_source', 'noop', ''):
				chan = self._bus_channel_name(row['device'], row['msg'], row['number'])
				keep.pop(chan, None)
		self._bus_vals = keep
		self._publish_sources_chop()

	def OnSourcesCook(self, scriptOp):
		scriptOp.clear()
		scriptOp.rate = abs(self.ownerComp.time.rate) or 60
		scriptOp.numSamples = 1
		for name in sorted(self._bus_vals.keys()):
			ch = scriptOp.appendChan(name)
			ch[0] = float(self._bus_vals[name])

	def _publish_sources_chop(self):
		# Write bus source channels outside cook (requires modoutsidecook).
		script = self.ownerComp.op('script_sources')
		if script is None:
			return
		script.clear()
		script.rate = abs(self.ownerComp.time.rate) or 60
		script.numSamples = 1
		for name in sorted(self._bus_vals.keys()):
			ch = script.appendChan(name)
			ch[0] = float(self._bus_vals[name])

	# ---- dispatch ------------------------------------------------------

	def _dispatch(self, row, message, value, key):
		kind = row.get('dest_kind')
		mode = row.get('mode', 'absolute')
		dest = row.get('dest', '')

		if kind == 'noop':
			return

		if kind == 'latch':
			if row['msg'] == 'note' and not _is_note_on(message, value):
				return
			if row['msg'] == 'cc' and float(value) <= 0:
				return
			self._do_latch(dest)
			return

		if kind == 'pulse':
			if row['msg'] == 'note' and not _is_note_on(message, value):
				return
			if row['msg'] == 'cc' and float(value) <= 0:
				return
			self._do_pulse(dest)
			return

		if kind == 'select':
			if row['msg'] == 'note' and not _is_note_on(message, value):
				return
			self._do_select(dest)
			return

		if kind == 'par':
			u = self._incoming_01(row, value)
			self._write_par_path(dest, u, mode, row, key, message, value)
			return

		if kind == 'factory_slot':
			addr = self._factory_slot_address(int(float(dest or 0)))
			if not addr:
				return
			self._write_base_address(addr, mode, row, key, message, value)
			return

		if kind == 'base':
			self._write_base_address(dest, mode, row, key, message, value)
			return

		if kind == 'slot_depth':
			self._write_slot_depth(dest, mode, row, key, message, value)
			return

	def _incoming_01(self, row, value):
		v = float(value)
		if v > 1.0:
			v = v / 127.0
		v = _clamp01(v)
		lo = float(row.get('min', 0))
		hi = float(row.get('max', 1))
		if hi <= lo:
			hi = lo + 1e-6
		u = lo + v * (hi - lo)
		return _apply_curve(_clamp01(u), row.get('curve', 'lin'))

	def _write_base_address(self, address, mode, row, key, message, value):
		pe = self._rgv()
		if mode == 'relative':
			step = self._relative_step(key, value)
			cur = pe.GetBase(address)
			if cur is None:
				return
			pe.SetBase(address, _clamp01(cur + step))
			self._touch_grab(address, True)
			self._schedule_cc_release(key, address)
			return

		if mode == 'toggle':
			if row['msg'] == 'note' and not _is_note_on(message, value):
				return
			cur = pe.GetBase(address) or 0.0
			pe.SetBase(address, 0.0 if cur >= 0.5 else 1.0)
			return

		if mode in ('momentary', 'trigger'):
			on = True
			if row['msg'] == 'note':
				on = _is_note_on(message, value)
			else:
				on = float(value) > 0
			if mode == 'trigger':
				if on:
					pe.FirePulse(address)
				return
			pe.SetToggle(address, on)
			return

		# absolute
		u = self._incoming_01(row, value)
		if row.get('pickup') != 'live':
			base = pe.GetBase(address)
			if base is None:
				return
			# Soft takeover: wait until incoming crosses stored base
			if abs(u - base) > 0.02 and not self._crossed(key, u, base):
				self._touch[key + ':last'] = u
				return
			row['pickup'] = 'live'
			self._bindings[key] = row
		pe.SetBase(address, u)
		self._touch_grab(address, True)
		if row['msg'] == 'note' and _is_note_off(message, value):
			self._touch_grab(address, False)
		else:
			self._schedule_cc_release(key, address)

	def _crossed(self, key, u, base):
		prev = self._touch.get(key + ':last')
		self._touch[key + ':last'] = u
		if prev is None:
			return abs(u - base) <= 0.02
		return (prev - base) * (u - base) <= 0

	def _relative_step(self, key, value):
		v = float(value)
		# Common relative encodings: 1..63 up, 65..127 down; or signed around 64
		if v > 1.0:
			if v >= 65:
				delta = -(v - 64)
			else:
				delta = v
		else:
			delta = 1.0 if v >= 0.5 else -1.0
		now = time.time()
		last = self._rel_last_t.get(key)
		self._rel_last_t[key] = now
		mult = 4.0 if last is not None and (now - last) * 1000.0 < REL_FAST_MS else 1.0
		return REL_STEP * mult * (1.0 if delta >= 0 else -1.0) * max(1.0, abs(delta))

	def _touch_grab(self, address, held):
		meval = self._rgv().op('control/mod_eval')
		if meval is None:
			return
		if held:
			meval.SetGrab(address, 0.0)
			self._touch[address] = True
		else:
			meval.ReleaseGrab(address, 250.0)
			self._touch.pop(address, None)

	def _schedule_cc_release(self, key, address):
		self._cc_release[key] = (address, time.time() + CC_RELEASE_MS / 1000.0)
		run("op('{}').ReleaseTouches()".format(self.ownerComp.path),
			delayMilliSeconds=CC_RELEASE_MS + 5)

	def ReleaseTouches(self):
		now = time.time()
		done = []
		for key, (address, exp) in self._cc_release.items():
			if now >= exp:
				self._touch_grab(address, False)
				done.append(key)
		for k in done:
			self._cc_release.pop(k, None)

	def _factory_slot_address(self, slot_index):
		rg = self._rgv()
		layer = int(rg.par.Selectedlayer.eval())
		token = str(rg.par.Selectedmodule.eval())
		mod_path = rg.ResolveModulePath(layer, token)
		addrs = rg.FloatIntAddresses(mod_path)
		if slot_index < 0 or slot_index >= len(addrs):
			return None
		return addrs[slot_index]

	def _write_par_path(self, dest, u, mode, row, key, message, value):
		"""dest like control/mods.Macro1, or Globalmodamount on RGV."""
		if '.' in dest:
			op_path, par_name = dest.split('.', 1)
			target = self._rgv().op(op_path) if op_path else self._rgv()
		else:
			target = self._rgv()
			par_name = dest
		if target is None:
			return
		par = getattr(target.par, par_name, None)
		if par is None:
			return
		if mode == 'relative':
			step = self._relative_step(key, value)
			par.val = float(par.eval()) + step
			return
		if mode == 'toggle':
			if row['msg'] == 'note' and not _is_note_on(message, value):
				return
			par.val = not bool(par.eval())
			return
		# Map 0-1 into par range when possible
		try:
			lo = float(par.normMin)
			hi = float(par.normMax)
			par.val = lo + _clamp01(u) * (hi - lo)
		except Exception:
			par.val = u

	def _write_slot_depth(self, dest, mode, row, key, message, value):
		# dest: address#slotIndex
		if '#' not in dest:
			return
		address, slot_s = dest.rsplit('#', 1)
		slot = int(float(slot_s))
		u = self._incoming_01(row, value)
		if mode == 'relative':
			u = None
		matrix = self._rgv().op('data/mod_matrix')
		if matrix is None:
			return
		for r in range(1, matrix.numRows):
			if matrix[r, 'address'].val.strip() != address:
				continue
			if int(float(matrix[r, 'slot'].val or 0)) != slot:
				continue
			if u is None:
				cur = float(matrix[r, 'depth'].val or 0)
				matrix[r, 'depth'] = str(_clamp01(cur + self._relative_step(key, value)))
			else:
				# depth is -1..1; map from 0-1 incoming to -1..1
				depth = u * 2.0 - 1.0
				matrix[r, 'depth'] = str(depth)
			meval = self._rgv().op('control/mod_eval')
			if meval:
				meval.Rebuild()
			return

	def _do_latch(self, dest):
		rg = self._rgv()
		d = dest.lower()
		if d == 'blackout':
			rg.par.Blackout.val = not bool(rg.par.Blackout.eval())
		elif d == 'freeze':
			rg.par.Freeze.val = not bool(rg.par.Freeze.eval())
		elif d == 'learn':
			rg.par.Learn.val = not bool(rg.par.Learn.eval())
			if not rg.par.Learn.eval():
				self.ClearPending()
			self._schedule_learn_flash()
		self.UpdateLeds()

	def _do_pulse(self, dest):
		clock = self._rgv().op('control/clock')
		d = dest.lower()
		if clock is None:
			return
		if d == 'tap':
			clock.Tap()
		elif d == 'resync':
			clock.Resync()
		elif d == 'half':
			clock.Half()
		elif d == 'double':
			clock.Double()

	def _do_select(self, dest):
		# dest: layer:1 or module:gen
		rg = self._rgv()
		if dest.startswith('layer:'):
			rg.par.Selectedlayer.val = int(dest.split(':', 1)[1])
			self._retarget_factory_pickups()
		elif dest.startswith('module:'):
			rg.par.Selectedmodule.val = dest.split(':', 1)[1]
			self._retarget_factory_pickups()
		self.UpdateLeds()

	def _retarget_factory_pickups(self):
		"""On selection change, factory absolute bindings re-arm pickup."""
		for key, row in list(self._bindings.items()):
			if row.get('source') == 'learned':
				continue
			if row.get('mode') != 'absolute':
				continue
			if row.get('dest_kind') not in ('factory_slot', 'base', 'par'):
				continue
			row = dict(row)
			row['pickup'] = 'armed'
			self._bindings[key] = row
		self._sync_map_table()

	# ---- LED ------------------------------------------------------------

	def _stamp_feedback(self, channel, msg, number):
		key = self._key(self._device_name or self._profile_id, channel, msg, number)
		self._feedback[key] = time.time() + FEEDBACK_IGNORE_S

	def UpdateLeds(self):
		led = self.ownerComp.op('table_led')
		mout = self.ownerComp.op('midiout')
		if led is None or mout is None:
			return
		self._ensure_led_headers()
		while led.numRows > 1:
			led.deleteRow(1)
		rg = self._rgv()
		learn = bool(rg.par.Learn.eval())
		blackout = bool(rg.par.Blackout.eval())
		freeze = bool(rg.par.Freeze.eval())
		layer = int(rg.par.Selectedlayer.eval())
		desired = {}  # note -> (channel, velocity, role)

		# From profile led map roles
		path = self._profile_path()
		led_map = {}
		if os.path.isfile(path):
			with open(path, 'r', encoding='utf-8') as f:
				led_map = json.load(f).get('led', {})

		def put(role, note, channel, vel):
			desired[int(note)] = (int(channel), int(vel), role)

		# Learn button flash
		if 'learn' in led_map:
			info = led_map['learn']
			vel = 0
			if learn:
				vel = 6 if self._learn_flash else 3  # APC color-ish
			put('learn', info['note'], info.get('channel', 1), vel)

		if 'blackout' in led_map:
			info = led_map['blackout']
			put('blackout', info['note'], info.get('channel', 1), 5 if blackout else 0)

		if 'freeze' in led_map:
			info = led_map['freeze']
			put('freeze', info['note'], info.get('channel', 1), 5 if freeze else 0)

		# Layer select clip-stops
		for i in range(1, 5):
			role = f'layer{i}'
			if role in led_map:
				info = led_map[role]
				put(role, info['note'], info.get('channel', 1), 5 if layer == i else 1 if i == 1 else 0)

		# Tap flash driven externally via OnBeatPulse
		if 'tap' in led_map:
			info = led_map['tap']
			vel = desired.get(int(info['note']), (1, 0, 'tap'))[1]
			# keep previous unless beat sets it
			if not hasattr(self, '_tap_vel'):
				self._tap_vel = 0
			put('tap', info['note'], info.get('channel', 1), self._tap_vel)

		for note, (ch, vel, role) in desired.items():
			led.appendRow([str(note), str(ch), str(vel), role])
			prev = self._led_sent.get(note)
			if prev == vel:
				continue
			self._led_sent[note] = vel
			self._stamp_feedback(ch, 'note', note)
			try:
				if vel > 0:
					mout.sendNoteOn(ch, note, vel)
				else:
					mout.sendNoteOff(ch, note, 0)
			except Exception as e:
				debug(f'MidiExt LED send failed: {e}')

	def OnBeatPulse(self):
		self._tap_vel = 5
		self.UpdateLeds()
		run("op('{}')._clear_tap_flash()".format(self.ownerComp.path), delayMilliSeconds=80)

	def _clear_tap_flash(self):
		self._tap_vel = 0
		self.UpdateLeds()

	def _schedule_learn_flash(self):
		if bool(self._rgv().par.Learn.eval()):
			run("op('{}')._tick_learn_flash()".format(self.ownerComp.path), delayMilliSeconds=250)

	def _tick_learn_flash(self):
		if not bool(self._rgv().par.Learn.eval()):
			self._learn_flash = False
			self.UpdateLeds()
			return
		self._learn_flash = not self._learn_flash
		self.UpdateLeds()
		self._schedule_learn_flash()

	# ---- parameter callbacks -------------------------------------------

	def onParValueChange(self, par, prev):
		name = par.name
		if name == 'Midiprofile':
			self.LoadActiveProfile()
		elif name in ('Learn', 'Blackout', 'Freeze', 'Selectedlayer', 'Selectedmodule'):
			if name in ('Selectedlayer', 'Selectedmodule'):
				self._retarget_factory_pickups()
			if name == 'Learn':
				if not bool(par.eval()):
					self.ClearPending()
				else:
					self._schedule_learn_flash()
			self.UpdateLeds()

	def onParPulse(self, par):
		pass
