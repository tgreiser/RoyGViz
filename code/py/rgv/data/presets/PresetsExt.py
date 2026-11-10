"""Preset player: module, layer, and scene snapshots.

Structural state cuts on the quantize boundary. Float and int bases then morph
in beats through null_morph exports. Session latches are not stored.
"""
import json
import os
import random

import TDFunctions as TDF


SCHEMA = 1
SCENE_SLOTS = 16
BANK_SIZE = 8
LAYER_FX_SLOTS = 6
MASTER_FX_SLOTS = 8
BLEND_MODES = ('over', 'add', 'screen', 'multiply', 'difference')
MOD_COLS = (
	'address', 'slot', 'source', 'depth', 'mode',
	'smooth_ms', 'swing_min', 'swing_max', 'follow_role',
)


def _slug(name):
	text = ''.join(c if c.isalnum() or c in '-_' else '_' for c in (name or '').strip())
	return text.strip('_') or 'untitled'


def _module_id_of(address):
	if '@' not in address:
		return ''
	return address.split('@', 1)[1].split('/', 1)[0]


class PresetsExt:
	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		self._pending = None
		self._morph = {}
		self._undo = None
		self._flash_armed = False
		self._integrator_armed = False
		self._morph_slot = None
		TDF.createProperty(self, 'Morphing', value=0, dependable=True, readOnly=False)
		TDF.createProperty(self, 'Sceneflash', value=0, dependable=True, readOnly=False)

	def onInitTD(self):
		self._ensure_tables()
		self._scan_scenes()

	def Hello(self):
		return 'Presets'

	# ---- paths ----------------------------------------------------------

	def _root(self):
		return project.folder.replace('\\', '/')

	def _scene_path(self, slot):
		return self._root() + '/presets/scenes/%02d.json' % int(slot)

	def _layer_path(self, name):
		return self._root() + '/presets/layers/%s.json' % _slug(name)

	def _module_path(self, module_id, name):
		return self._root() + '/presets/modules/%s/%s.json' % (_slug(module_id), _slug(name))

	def _write_json(self, path, data):
		folder = os.path.dirname(path)
		if folder and not os.path.isdir(folder):
			os.makedirs(folder)
		with open(path, 'w', encoding='utf-8') as handle:
			json.dump(data, handle, indent=2)
			handle.write('\n')

	def _read_json(self, path):
		if not os.path.isfile(path):
			return None
		with open(path, 'r', encoding='utf-8') as handle:
			return json.load(handle)

	def _slot(self, slot):
		if slot is None:
			slot = int(self.ownerComp.par.Slot.eval())
		return max(0, min(SCENE_SLOTS - 1, int(slot)))

	def _mods_flag(self):
		return bool(self.ownerComp.par.Includemods.eval())

	def _registry(self):
		try:
			return dict(parent.Rgv.Registry or {})
		except Exception:
			return {}

	def _manifest_id(self, comp):
		if comp is None:
			return ''
		manifest = comp.op('manifest')
		if manifest is None:
			return ''
		try:
			return str(manifest['id', 'value']).strip()
		except Exception:
			return ''

	def _par(self, info):
		mod = op(info.get('modulePath') or '')
		if mod is None:
			return None
		return getattr(mod.par, info.get('parName') or '', None)

	def _selected_layer(self):
		try:
			return int(parent.Rgv.par.Selectedlayer.eval())
		except Exception:
			return 1

	def _selected_module_path(self):
		rg = parent.Rgv
		try:
			layer = int(rg.par.Selectedlayer.eval())
			token = str(rg.par.Selectedmodule.eval())
		except Exception:
			return None
		if hasattr(rg, 'ResolveModulePath'):
			return rg.ResolveModulePath(layer, token)
		return None

	# ---- tables ---------------------------------------------------------

	def _ensure_tables(self):
		scenes = self.ownerComp.op('table_scenes')
		if scenes is not None and scenes.numRows < SCENE_SLOTS + 1:
			scenes.clear()
			scenes.appendRow(['slot', 'name', 'occupied', 'morphing'])
			for i in range(SCENE_SLOTS):
				scenes.appendRow([str(i), '', '0', '0'])
		warnings = self.ownerComp.op('warnings')
		if warnings is not None and (warnings.numRows == 0 or warnings[0, 0].val != 'reason'):
			warnings.clear()
			warnings.appendRow(['reason', 'scope', 'address', 'detail'])

	def _clear_warnings(self):
		self._ensure_tables()
		warnings = self.ownerComp.op('warnings')
		if warnings is None:
			return
		while warnings.numRows > 1:
			warnings.deleteRow(1)

	def _warn(self, reason, scope, address, detail):
		self._ensure_tables()
		warnings = self.ownerComp.op('warnings')
		if warnings is None:
			return
		warnings.appendRow([str(reason), str(scope), str(address), str(detail)])

	def _scene_row(self, slot):
		table = self.ownerComp.op('table_scenes')
		if table is None:
			return None
		for r in range(1, table.numRows):
			try:
				if int(float(table[r, 'slot'].val)) == int(slot):
					return r
			except Exception:
				continue
		return None

	def _set_scene_cell(self, slot, col, value):
		row = self._scene_row(slot)
		table = self.ownerComp.op('table_scenes')
		if row is None or table is None:
			return
		table[row, col] = str(value)

	def _scan_scenes(self):
		for i in range(SCENE_SLOTS):
			path = self._scene_path(i)
			occupied = '0'
			name = ''
			if os.path.isfile(path):
				occupied = '1'
				try:
					name = str(self._read_json(path).get('name') or '')
				except Exception:
					name = ''
			self._set_scene_cell(i, 'name', name)
			self._set_scene_cell(i, 'occupied', occupied)
			self._set_scene_cell(i, 'morphing', '0')

	def _set_morph_column(self, slot):
		for i in range(SCENE_SLOTS):
			self._set_scene_cell(i, 'morphing', '1' if slot is not None and i == int(slot) else '0')

	# ---- capture --------------------------------------------------------

	def _read_param(self, info):
		par = self._par(info)
		if par is None:
			return None
		ptype = (info.get('type') or 'float').lower()
		if ptype == 'pulse':
			return None
		if ptype == 'menu':
			return {'type': 'menu', 'value': str(par.eval())}
		if ptype == 'toggle':
			return {'type': 'toggle', 'value': 1 if par.eval() else 0}
		try:
			value = float(par.eval())
		except Exception:
			value = 0.0
		return {'type': ptype, 'value': max(0.0, min(1.0, value))}

	def _params_with_prefix(self, prefix):
		out = {}
		for address, info in self._registry().items():
			if not address.startswith(prefix):
				continue
			rec = self._read_param(info)
			if rec is not None:
				out[address] = rec
		return out

	def _preferred_address(self, module_path, token):
		cands = []
		for address, info in self._registry().items():
			if info.get('modulePath') != module_path:
				continue
			if address.rsplit('/', 1)[-1] != token:
				continue
			cands.append(address)
		if not cands:
			return None
		for address in cands:
			if 'gen_bank' not in address:
				return address
		return cands[0]

	def _fx_module_id(self, owner, slot):
		if owner is None:
			return ''
		mid = self._manifest_id(owner.op('fx%d' % int(slot)))
		if mid:
			return mid
		table = owner.op('fx_chain')
		if table is None:
			return ''
		for r in range(1, table.numRows):
			try:
				if int(float(table[r, 'slot'].val)) != int(slot):
					continue
			except Exception:
				continue
			return (table[r, 'module_id'].val or '').strip()
		return ''

	def _capture_fx(self, owner, nslots, prefix):
		items = []
		for slot in range(1, nslots + 1):
			mid = self._fx_module_id(owner, slot)
			params = {}
			if mid:
				params = self._params_with_prefix('%s/fx%d@%s/' % (prefix, slot, mid))
			items.append({'slot': slot, 'module_id': mid, 'params': params})
		return items

	def _capture_layer(self, index):
		rg = parent.Rgv
		layer = rg.op('layers/layer%d' % int(index))
		if layer is None:
			return None
		try:
			bank_index = int(round(float(layer.par.Bankindex.eval())))
		except Exception:
			bank_index = 0
		bank_index = max(0, min(BANK_SIZE - 1, bank_index))
		slot = rg.op('layers/layer%d/gen_bank/slot%d' % (int(index), bank_index))
		gen_id = self._manifest_id(slot)
		params = {}
		if gen_id:
			params = self._params_with_prefix('layer%d/gen@%s/' % (int(index), gen_id))
		try:
			blend = str(layer.par.Blend.eval())
		except Exception:
			blend = 'over'
		return {
			'index': int(index),
			'bank_index': bank_index,
			'generator_id': gen_id,
			'blend': blend,
			'params': params,
			'fx': self._capture_fx(layer, LAYER_FX_SLOTS, 'layer%d' % int(index)),
		}

	def _addresses_in_layer(self, layer_obj):
		if not layer_obj:
			return []
		addrs = list((layer_obj.get('params') or {}).keys())
		for item in layer_obj.get('fx') or []:
			addrs.extend((item.get('params') or {}).keys())
		return addrs

	def _capture_mod_rows(self, addresses):
		matrix = parent.Rgv.op('data/mod_matrix')
		wanted = set(addresses)
		rows = []
		if matrix is None:
			return rows
		for r in range(1, matrix.numRows):
			address = matrix[r, 'address'].val.strip()
			if address not in wanted:
				continue
			rows.append({col: matrix[r, col].val for col in MOD_COLS})
		return rows

	def _bpm_hint(self):
		clock = parent.Rgv.op('control/clock')
		if clock is None:
			return None
		try:
			return float(clock.par.Bpm.eval())
		except Exception:
			return None

	def _capture_scene(self):
		layers = []
		addresses = []
		for i in range(1, 5):
			layer_obj = self._capture_layer(i)
			if layer_obj is None:
				continue
			layers.append(layer_obj)
			addresses.extend(self._addresses_in_layer(layer_obj))
		master = parent.Rgv.op('master')
		master_fx = self._capture_fx(master, MASTER_FX_SLOTS, 'master') if master else []
		for item in master_fx:
			addresses.extend((item.get('params') or {}).keys())
		mods = self._mods_flag()
		return {
			'schema': SCHEMA,
			'scope': 'scene',
			'mods': mods,
			'bpm_hint': self._bpm_hint(),
			'palette': {'id': None},
			'layers': layers,
			'master': {'fx': master_fx},
			'mod_rows': self._capture_mod_rows(addresses) if mods else [],
		}

	def _capture_module_params(self, module_path):
		params = {}
		rows_from = []
		seen = set()
		for address, info in self._registry().items():
			if info.get('modulePath') != module_path:
				continue
			token = address.rsplit('/', 1)[-1]
			if token in seen:
				continue
			preferred = self._preferred_address(module_path, token)
			if preferred != address:
				continue
			seen.add(token)
			rec = self._read_param(info)
			if rec is None:
				continue
			params[token] = rec
			rows_from.append(preferred)
		return params, rows_from

	# ---- save -----------------------------------------------------------

	def SaveScene(self, slot=None):
		slot = self._slot(slot)
		data = self._capture_scene()
		self._write_json(self._scene_path(slot), data)
		self.ownerComp.par.Slot = slot
		self._set_scene_cell(slot, 'occupied', '1')
		self._set_scene_cell(slot, 'name', '')
		self._kick_led()
		return True

	def SaveLayer(self, name=None, layerIndex=None):
		name = (name if name is not None else str(self.ownerComp.par.Layername.eval())).strip()
		if not name:
			self._warn('missing_name', 'layer', '', 'set Layer Name')
			return False
		if layerIndex is None:
			layerIndex = self._selected_layer()
		layer_obj = self._capture_layer(layerIndex)
		if layer_obj is None:
			self._warn('missing', 'layer', '', 'layer %s' % layerIndex)
			return False
		mods = self._mods_flag()
		addrs = self._addresses_in_layer(layer_obj)
		data = {
			'schema': SCHEMA,
			'scope': 'layer',
			'name': name,
			'mods': mods,
			'layer': layer_obj,
			'mod_rows': self._capture_mod_rows(addrs) if mods else [],
		}
		self._write_json(self._layer_path(name), data)
		return True

	def SaveModule(self, name=None):
		name = (name if name is not None else str(self.ownerComp.par.Modulename.eval())).strip()
		if not name:
			self._warn('missing_name', 'module', '', 'set Module Name')
			return False
		module_path = self._selected_module_path()
		mod = op(module_path) if module_path else None
		module_id = self._manifest_id(mod)
		if not module_id:
			self._warn('missing', 'module', '', 'selected module has no id')
			return False
		params, addresses = self._capture_module_params(mod.path)
		mods = self._mods_flag()
		data = {
			'schema': SCHEMA,
			'scope': 'module',
			'name': name,
			'mods': mods,
			'module_id': module_id,
			'params': params,
			'mod_rows': self._capture_mod_rows(addresses) if mods else [],
		}
		self._write_json(self._module_path(module_id, name), data)
		return True

	# ---- recall ---------------------------------------------------------

	def RecallScene(self, slot=None):
		slot = self._slot(slot)
		data = self._read_json(self._scene_path(slot))
		if not data:
			self._clear_warnings()
			self._warn('missing', 'scene', '', self._scene_path(slot))
			return False
		if int(data.get('schema', 1)) != SCHEMA:
			self._clear_warnings()
			self._warn('schema', 'scene', '', str(data.get('schema')))
			return False
		data['_slot'] = slot
		self.ownerComp.par.Slot = slot
		return self._arm('scene', data)

	def RecallLayer(self, name=None, layerIndex=None):
		name = (name if name is not None else str(self.ownerComp.par.Layername.eval())).strip()
		if not name:
			self._clear_warnings()
			self._warn('missing_name', 'layer', '', 'set Layer Name')
			return False
		data = self._read_json(self._layer_path(name))
		if not data:
			self._clear_warnings()
			self._warn('missing', 'layer', '', self._layer_path(name))
			return False
		if int(data.get('schema', 1)) != SCHEMA:
			self._clear_warnings()
			self._warn('schema', 'layer', '', str(data.get('schema')))
			return False
		if layerIndex is None:
			layerIndex = self._selected_layer()
		data['_target_layer'] = int(layerIndex)
		return self._arm('layer', data)

	def RecallModule(self, name=None):
		name = (name if name is not None else str(self.ownerComp.par.Modulename.eval())).strip()
		if not name:
			self._clear_warnings()
			self._warn('missing_name', 'module', '', 'set Module Name')
			return False
		module_path = self._selected_module_path()
		mod = op(module_path) if module_path else None
		module_id = self._manifest_id(mod)
		path = self._module_path(module_id or 'missing', name)
		data = self._read_json(path)
		if not data:
			self._clear_warnings()
			self._warn('missing', 'module', '', path)
			return False
		if int(data.get('schema', 1)) != SCHEMA:
			self._clear_warnings()
			self._warn('schema', 'module', '', str(data.get('schema')))
			return False
		return self._arm('module', data)

	def StepScene(self, delta):
		occupied = [i for i in range(SCENE_SLOTS) if os.path.isfile(self._scene_path(i))]
		if not occupied:
			self._clear_warnings()
			self._warn('empty', 'scene', '', 'no scene slots')
			return False
		cur = self._slot(None)
		if cur in occupied:
			idx = occupied.index(cur)
		else:
			idx = -1 if delta > 0 else 0
		step = 1 if delta > 0 else -1
		nxt = occupied[(idx + step) % len(occupied)]
		self.ownerComp.par.Slot = nxt
		return self.RecallScene(nxt)

	def _arm(self, kind, payload):
		self._pending = {'kind': kind, 'payload': payload}
		try:
			quantize = str(self.ownerComp.par.Quantize.eval())
		except Exception:
			quantize = 'now'
		if quantize == 'now':
			self._commit()
		return True

	def OnClockPulse(self, which):
		if not self._pending:
			return False
		try:
			quantize = str(self.ownerComp.par.Quantize.eval())
		except Exception:
			quantize = 'now'
		if quantize != which:
			return False
		self._commit()
		return True

	def _commit(self):
		pending = self._pending
		self._pending = None
		if not pending:
			return False
		self.FinishMorph()
		self._clear_warnings()
		kind = pending['kind']
		payload = pending['payload']
		if kind == 'scene':
			self._undo = None
			floats, applied = self._recall_scene(payload)
			slot = payload.get('_slot')
		elif kind == 'layer':
			floats, applied = self._recall_layer(payload)
			slot = None
		elif kind == 'module':
			floats, applied = self._recall_module(payload)
			slot = None
		else:
			return False
		if payload.get('mods'):
			self._merge_mods(payload.get('mod_rows') or [], applied)
		self._start_morph(floats, slot)
		self._kick_led()
		return True

	def _remap_layer_address(self, address, src, dst):
		if src is None or dst is None or int(src) == int(dst):
			return address
		old = 'layer%d/' % int(src)
		new = 'layer%d/' % int(dst)
		if address.startswith(old):
			return new + address[len(old):]
		return address

	def _resolve_generator(self, layer_index, generator_id, stored_index):
		rg = parent.Rgv
		ids = []
		for i in range(BANK_SIZE):
			slot = rg.op('layers/layer%d/gen_bank/slot%d' % (int(layer_index), i))
			ids.append(self._manifest_id(slot))
		generator_id = generator_id or ''
		if not generator_id:
			if 0 <= int(stored_index) < BANK_SIZE and not ids[int(stored_index)]:
				return int(stored_index)
			return None
		matches = [i for i, mid in enumerate(ids) if mid == generator_id]
		if not matches:
			return None
		if int(stored_index) in matches:
			return int(stored_index)
		return matches[0]

	def _select_generator(self, layer_index, generator_id, stored_index, scope):
		resolved = self._resolve_generator(layer_index, generator_id, stored_index)
		if resolved is None:
			self._warn(
				'module_mismatch', scope, 'layer%d/gen' % int(layer_index),
				'id %s not in bank' % (generator_id or '(empty)'),
			)
			return False
		rg = parent.Rgv
		if hasattr(rg, 'SetBankIndex'):
			rg.SetBankIndex(layer_index, resolved, reset=False)
		else:
			layer = rg.op('layers/layer%d' % int(layer_index))
			if layer is not None:
				layer.par.Bankindex = resolved
		return True

	def _apply_prefixed(self, params, prefix, scope):
		floats = []
		applied = []
		file_by_token = {}
		for address, rec in (params or {}).items():
			if address.startswith(prefix):
				file_by_token[address.rsplit('/', 1)[-1]] = rec
		seen = set()
		for address, info in self._registry().items():
			if not address.startswith(prefix):
				continue
			token = address.rsplit('/', 1)[-1]
			if token in seen:
				continue
			seen.add(token)
			live_type = (info.get('type') or 'float').lower()
			if live_type == 'pulse':
				continue
			loaded_id = self._manifest_id(op(info.get('modulePath') or ''))
			addr_id = _module_id_of(address)
			if loaded_id and addr_id and loaded_id != addr_id:
				self._warn('module_mismatch', scope, address, 'loaded %s' % loaded_id)
				continue
			if token not in file_by_token:
				self._queue_default(info, live_type, address, floats, applied)
				continue
			rec = file_by_token[token]
			file_type = (rec.get('type') or live_type).lower()
			if file_type != live_type:
				self._warn('type_mismatch', scope, address, '%s vs %s' % (file_type, live_type))
				continue
			self._queue_value(info, live_type, address, rec.get('value'), floats, applied, scope)
		return floats, applied

	def _queue_default(self, info, live_type, address, floats, applied):
		par = self._par(info)
		if par is None:
			return
		if live_type in ('float', 'int'):
			try:
				start = float(par.eval())
			except Exception:
				start = 0.0
			try:
				target = float(par.default)
			except Exception:
				target = start
			floats.append((address, start, max(0.0, min(1.0, target)), info))
			applied.append(address)
		elif live_type == 'toggle':
			try:
				par.val = 1 if float(par.default) >= 0.5 else 0
			except Exception:
				par.val = 0
			applied.append(address)
		elif live_type == 'menu':
			try:
				par.val = par.default
			except Exception:
				pass
			applied.append(address)

	def _queue_value(self, info, live_type, address, value, floats, applied, scope):
		par = self._par(info)
		if par is None:
			self._warn('unknown_param', scope, address, 'parameter missing')
			return
		if live_type in ('float', 'int'):
			try:
				start = float(par.eval())
			except Exception:
				start = 0.0
			try:
				target = float(value)
			except Exception:
				target = start
			floats.append((address, start, max(0.0, min(1.0, target)), info))
			applied.append(address)
		elif live_type == 'toggle':
			try:
				flag = float(value) >= 0.5
			except Exception:
				flag = bool(value)
			par.val = 1 if flag else 0
			applied.append(address)
		elif live_type == 'menu':
			try:
				par.val = value
			except Exception:
				self._warn('bad_menu', scope, address, value)
				return
			applied.append(address)

	def _apply_fx_list(self, owner, items, prefix, scope, src_layer, dst_layer):
		floats = []
		applied = []
		for item in items or []:
			try:
				slot = int(item.get('slot', 0))
			except Exception:
				continue
			wanted = (item.get('module_id') or '').strip()
			loaded = self._fx_module_id(owner, slot)
			if loaded != wanted:
				self._warn(
					'module_mismatch', scope, '%s/fx%d' % (prefix, slot),
					'loaded %s preset %s' % (loaded or '(empty)', wanted or '(empty)'),
				)
				continue
			if not wanted:
				continue
			params = {}
			for address, rec in (item.get('params') or {}).items():
				if src_layer is not None:
					address = self._remap_layer_address(address, src_layer, dst_layer)
				params[address] = rec
			part_f, part_a = self._apply_prefixed(
				params, '%s/fx%d@%s/' % (prefix, slot, wanted), scope,
			)
			floats.extend(part_f)
			applied.extend(part_a)
		return floats, applied

	def _apply_blend(self, layer, blend, scope, label):
		blend = (blend or 'over').lower()
		if blend not in BLEND_MODES:
			self._warn('bad_blend', scope, label, blend)
			return
		try:
			layer.par.Blend = blend
		except Exception:
			self._warn('bad_blend', scope, label, blend)

	def _apply_layer_object(self, layer_obj, target_index, scope):
		floats = []
		applied = []
		if not layer_obj:
			return floats, applied
		src = int(layer_obj.get('index', target_index))
		dst = int(target_index)
		layer = parent.Rgv.op('layers/layer%d' % dst)
		if layer is None:
			self._warn('missing', scope, 'layer%d' % dst, 'no layer')
			return floats, applied
		self._apply_blend(layer, layer_obj.get('blend'), scope, 'layer%d' % dst)
		matched = self._select_generator(
			dst, layer_obj.get('generator_id') or '', layer_obj.get('bank_index') or 0, scope,
		)
		if matched:
			gen_id = layer_obj.get('generator_id') or ''
			params = {}
			for address, rec in (layer_obj.get('params') or {}).items():
				params[self._remap_layer_address(address, src, dst)] = rec
			if gen_id:
				part_f, part_a = self._apply_prefixed(
					params, 'layer%d/gen@%s/' % (dst, gen_id), scope,
				)
				floats.extend(part_f)
				applied.extend(part_a)
		part_f, part_a = self._apply_fx_list(
			layer, layer_obj.get('fx'), 'layer%d' % dst, scope, src, dst,
		)
		floats.extend(part_f)
		applied.extend(part_a)
		return floats, applied

	def _recall_scene(self, data):
		floats = []
		applied = []
		for layer_obj in data.get('layers') or []:
			try:
				index = int(layer_obj.get('index'))
			except Exception:
				continue
			part_f, part_a = self._apply_layer_object(layer_obj, index, 'scene')
			floats.extend(part_f)
			applied.extend(part_a)
		master = parent.Rgv.op('master')
		master_fx = (data.get('master') or {}).get('fx')
		part_f, part_a = self._apply_fx_list(master, master_fx, 'master', 'scene', None, None)
		floats.extend(part_f)
		applied.extend(part_a)
		self._apply_bpm_hint(data.get('bpm_hint'))
		# Remap is identity for a scene. Filter rows onto addresses we applied.
		data['mod_rows'] = data.get('mod_rows') or []
		return floats, applied

	def _recall_layer(self, data):
		target = int(data.get('_target_layer') or self._selected_layer())
		floats, applied = self._apply_layer_object(data.get('layer'), target, 'layer')
		src = int((data.get('layer') or {}).get('index', target))
		rows = []
		for row in data.get('mod_rows') or []:
			row = dict(row)
			row['address'] = self._remap_layer_address(row.get('address') or '', src, target)
			rows.append(row)
		data['mod_rows'] = rows
		return floats, applied

	def _recall_module(self, data):
		module_path = self._selected_module_path()
		mod = op(module_path) if module_path else None
		live_id = self._manifest_id(mod)
		file_id = (data.get('module_id') or '').strip()
		if mod is None or not live_id or live_id != file_id:
			self._warn(
				'module_mismatch', 'module', file_id,
				'selected %s' % (live_id or '(none)'),
			)
			data['mod_rows'] = []
			return [], []
		params = {}
		for token, rec in (data.get('params') or {}).items():
			address = self._preferred_address(mod.path, token)
			if not address:
				self._warn('unknown_param', 'module', '%s/%s' % (file_id, token), 'ignored')
				continue
			params[address] = rec
		prefix = ''
		if params:
			prefix = next(iter(params)).rsplit('/', 1)[0] + '/'
		floats, applied = self._apply_prefixed(params, prefix, 'module') if prefix else ([], [])
		# Missing tokens: _apply_prefixed already defaults live addresses under prefix.
		rows = []
		for row in data.get('mod_rows') or []:
			token = (row.get('address') or '').rsplit('/', 1)[-1]
			live = self._preferred_address(mod.path, token)
			if not live:
				continue
			row = dict(row)
			row['address'] = live
			rows.append(row)
		data['mod_rows'] = rows
		return floats, applied

	def _apply_bpm_hint(self, hint):
		if hint is None:
			return
		clock = parent.Rgv.op('control/clock')
		if clock is None:
			return
		try:
			source = str(clock.par.Source.eval())
		except Exception:
			return
		if source != 'internal':
			return
		try:
			clock.par.Bpm = float(hint)
		except Exception:
			self._warn('bpm', 'scene', 'clock/bpm', hint)

	def _merge_mods(self, rows, applied):
		matrix = parent.Rgv.op('data/mod_matrix')
		if matrix is None:
			return
		applied = set(applied)
		row_i = matrix.numRows - 1
		while row_i >= 1:
			if matrix[row_i, 'address'].val.strip() in applied:
				matrix.deleteRow(row_i)
			row_i -= 1
		for row in rows:
			address = (row.get('address') or '').strip()
			if address not in applied:
				continue
			matrix.appendRow([
				'' if row.get(col) is None else str(row.get(col))
				for col in MOD_COLS
			])
		meval = parent.Rgv.op('control/mod_eval')
		if meval is not None and hasattr(meval, 'Rebuild'):
			meval.Rebuild()

	# ---- morph ----------------------------------------------------------

	def _write_const(self, chop, pairs):
		if chop is None:
			return
		if not pairs:
			pairs = [('idle', 0.0)]
		seq = chop.seq.const
		seq.numBlocks = len(pairs)
		for i, (name, val) in enumerate(pairs):
			block = seq[i]
			block.par.name = name
			block.par.value = float(val)

	def _channel_name(self, address, info):
		name = (info or {}).get('channel')
		if name:
			return name
		if hasattr(parent.Rgv, 'SanitizeChannel'):
			return parent.Rgv.SanitizeChannel(address)
		return address.replace('/', '_').replace('@', '_at_')

	def _start_morph(self, floats, scene_slot):
		try:
			beats = float(self.ownerComp.par.Morphbeats.eval())
		except Exception:
			beats = 0.0
		rg = parent.Rgv
		if beats <= 0 or not floats:
			for address, _start, target, _info in floats:
				if hasattr(rg, 'SetBase'):
					rg.SetBase(address, target)
			self._morph_slot = None
			self._set_morph_column(None)
			self.Morphing = 0
			return
		starts = []
		ends = []
		plan = []
		for address, start, target, info in floats:
			name = self._channel_name(address, info)
			starts.append((name, start))
			ends.append((name, target))
			plan.append((address, target, info, name))
		self._write_const(self.ownerComp.op('const_start'), starts)
		self._write_const(self.ownerComp.op('const_end'), ends)
		self._reset_integrator()
		self._morph_slot = scene_slot
		self.Morphing = 1
		self._set_morph_column(scene_slot)
		null_u = self.ownerComp.op('null_u')
		null_m = self.ownerComp.op('null_morph')
		if null_u is not None:
			null_u.cook(force=True)
		if null_m is not None:
			null_m.cook(force=True)
		self._morph = {}
		for address, target, info, name in plan:
			par = self._par(info)
			if par is None or null_m is None:
				continue
			try:
				par.expr = "parent.Rgv.op('data/presets/null_morph')[%r]" % name
				par.mode = ParMode.EXPRESSION
			except Exception as exc:
				self._warn('export', '', address, exc)
				if hasattr(rg, 'SetBase'):
					rg.SetBase(address, target)
				continue
			self._morph[address] = {'target': target, 'par': par, 'channel': name}
		if not self._morph:
			self.Morphing = 0
			self._morph_slot = None
			self._set_morph_column(None)
			return
		self._kick_flash()

	def _reset_integrator(self):
		self._integrator_armed = False
		switch = self.ownerComp.op('switch_beats')
		feedback = self.ownerComp.op('feedback_beats')
		if switch is not None:
			switch.par.index = 0
		if feedback is not None:
			feedback.par.resetpulse.pulse()
		nullb = self.ownerComp.op('null_beats')
		if nullb is not None:
			nullb.cook(force=True)
		run("op(%r).ArmIntegrator()" % self.ownerComp.path, delayFrames=1)

	def ArmIntegrator(self):
		if not int(self.Morphing):
			return
		switch = self.ownerComp.op('switch_beats')
		if switch is not None:
			switch.par.index = 1
		self._integrator_armed = True

	def ReleaseMorph(self, address):
		rec = self._morph.pop(address, None)
		if not rec:
			return False
		par = rec.get('par')
		if par is not None:
			try:
				par.mode = ParMode.CONSTANT
				par.expr = ''
			except Exception:
				pass
		if not self._morph:
			self.Morphing = 0
			self.Sceneflash = 0
			self._morph_slot = None
			self._set_morph_column(None)
		return True

	def FinishMorph(self):
		items = list(self._morph.items())
		self._morph = {}
		self.Morphing = 0
		self.Sceneflash = 0
		switch = self.ownerComp.op('switch_beats')
		if switch is not None:
			switch.par.index = 0
		rg = parent.Rgv
		for address, rec in items:
			par = rec.get('par')
			if par is not None:
				try:
					par.mode = ParMode.CONSTANT
					par.expr = ''
				except Exception:
					pass
			if hasattr(rg, 'SetBase'):
				rg.SetBase(address, rec.get('target', 0))
		self._morph_slot = None
		self._set_morph_column(None)
		return True

	def OnMorphDone(self):
		if not self._integrator_armed:
			return False
		if not int(self.Morphing) or not self._morph:
			return False
		null_u = self.ownerComp.op('null_u')
		try:
			progress = float(null_u['u'])
		except Exception:
			return False
		if progress < 0.999:
			return False
		return self.FinishMorph()

	def _kick_flash(self):
		self.Sceneflash = 1
		self._kick_led()
		if self._flash_armed:
			return
		self._flash_armed = True
		run("op(%r).TickSceneFlash()" % self.ownerComp.path, delayMilliSeconds=250)

	def TickSceneFlash(self):
		self._flash_armed = False
		if not int(self.Morphing):
			self.Sceneflash = 0
			self._kick_led()
			return
		self.Sceneflash = 0 if int(self.Sceneflash) else 1
		self._kick_led()
		self._flash_armed = True
		run("op(%r).TickSceneFlash()" % self.ownerComp.path, delayMilliSeconds=250)

	def _kick_led(self):
		midi = parent.Rgv.op('control/midi')
		if midi is not None and hasattr(midi, 'UpdateLeds'):
			midi.UpdateLeds()

	# ---- mutate ---------------------------------------------------------

	def _layer_float_addresses(self, layer):
		gen_prefix = 'layer%d/gen@' % int(layer)
		fx_prefix = 'layer%d/fx' % int(layer)
		out = []
		for address, info in self._registry().items():
			if info.get('type') not in ('float', 'int'):
				continue
			if address.startswith(gen_prefix) or address.startswith(fx_prefix):
				out.append(address)
		return out

	def Mutate(self):
		rg = parent.Rgv
		layer = self._selected_layer()
		undo = {}
		for address in self._layer_float_addresses(layer):
			if not hasattr(rg, 'GetBase') or not hasattr(rg, 'SetBase'):
				return False
			current = rg.GetBase(address)
			if current is None:
				continue
			undo[address] = float(current)
			rg.SetBase(address, random.random())
		self._undo = undo or None
		return bool(undo)

	def Undo(self):
		if not self._undo:
			return False
		rg = parent.Rgv
		for address, value in self._undo.items():
			if hasattr(rg, 'SetBase'):
				rg.SetBase(address, value)
		self._undo = None
		return True

	def onParPulse(self, par):
		name = par.name
		if name == 'Saveslot':
			self.SaveScene()
		elif name == 'Recallslot':
			self.RecallScene()
		elif name == 'Savemodule':
			self.SaveModule()
		elif name == 'Recallmodule':
			self.RecallModule()
		elif name == 'Savelayer':
			self.SaveLayer()
		elif name == 'Recalllayer':
			self.RecallLayer()
		elif name == 'Mutate':
			self.Mutate()
		elif name == 'Undo':
			self.Undo()
