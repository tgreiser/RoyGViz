from TDStoreTools import StorageManager
import TDFunctions as TDF


def _sanitize_channel(address):
	"""Turn an address into a legal CHOP channel name."""
	return (
		address.replace('/', '_')
		.replace('@', '_at_')
		.replace('-', '_')
	)


def _par_name_from_token(token):
	"""Custom par name: Capitalized, letters+digits only."""
	clean = ''.join(c for c in token if c.isalnum())
	if not clean:
		clean = 'Param'
	return clean[0].upper() + clean[1:]


class ParamEngineExt:
	"""Create module custom parameters from params tables. Structure only."""

	def __init__(self, ownerComp):
		self.ownerComp = ownerComp
		# address -> {module, parName, channel, row meta}
		TDF.createProperty(self, 'Registry', value={}, dependable=True, readOnly=False)

	def onInitTD(self):
		# Rebuild registry after load / re-init (custom pars persist; registry does not)
		gen = self.ownerComp.op('layers/layer1/gen')
		if gen is not None and gen.op('params') is not None:
			self.LoadModule(gen.path)

	def SanitizeChannel(self, address):
		return _sanitize_channel(address)

	def LoadModule(self, modulePath, addressPrefix=None):
		"""
		Read modulePath/params, create custom pars on the module COMP,
		register addresses. Returns list of registered address strings.
		"""
		mod = op(modulePath)
		if mod is None:
			raise ValueError(f'ParamEngine: module not found: {modulePath}')

		params = mod.op('params')
		if params is None:
			raise ValueError(f'ParamEngine: no params DAT in {modulePath}')

		manifest = mod.op('manifest')
		module_id = 'module'
		if manifest is not None:
			try:
				module_id = str(manifest['id', 'value'])
			except Exception:
				module_id = 'module'

		if addressPrefix is None:
			# relative to rgv: layers/layer1/gen
			rel = mod.path
			if rel.startswith(self.ownerComp.path + '/'):
				rel = rel[len(self.ownerComp.path) + 1:]
			addressPrefix = rel

		# header row expected
		headers = [params[0, c].val for c in range(params.numCols)]

		def col(name, default=None):
			if name in headers:
				return headers.index(name)
			return default

		idx_name = col('name')
		idx_label = col('label', idx_name)
		idx_min = col('min')
		idx_max = col('max')
		idx_default = col('default')
		idx_curve = col('curve')
		idx_type = col('type')
		idx_page = col('page')
		idx_role = col('role')

		if idx_name is None:
			raise ValueError('ParamEngine: params table missing name column')

		registered = []
		registry = dict(self.Registry)

		for r in range(1, params.numRows):
			token = params[r, idx_name].val.strip()
			if not token:
				continue

			label = params[r, idx_label].val if idx_label is not None else token
			ptype = (params[r, idx_type].val if idx_type is not None else 'float').lower()
			page_name = params[r, idx_page].val if idx_page is not None else 'Parameters'
			role = params[r, idx_role].val if idx_role is not None else ''
			curve = params[r, idx_curve].val if idx_curve is not None else 'lin'

			def _float_cell(ci, fallback):
				if ci is None:
					return fallback
				try:
					return float(params[r, ci].val)
				except Exception:
					return fallback

			pmin = _float_cell(idx_min, 0.0)
			pmax = _float_cell(idx_max, 1.0)
			pdef = _float_cell(idx_default, 0.5)

			par_name = _par_name_from_token(token)
			page = None
			for existing in mod.customPages:
				if existing.name == page_name:
					page = existing
					break
			if page is None:
				page = mod.appendCustomPage(page_name)

			if ptype in ('float', 'int'):
				if ptype == 'int':
					pg = page.appendInt(par_name, label=label, replace=True)
				else:
					pg = page.appendFloat(par_name, label=label, replace=True)
				par = pg[0]
				# Stored base is always 0-1 for float/int contract
				par.min = 0.0
				par.max = 1.0
				par.clampMin = True
				par.clampMax = True
				par.normMin = 0.0
				par.normMax = 1.0
				par.default = max(0.0, min(1.0, pdef))
				par.val = par.default
			elif ptype == 'toggle':
				pg = page.appendToggle(par_name, label=label, replace=True)
				par = pg[0]
				par.default = 1 if pdef >= 0.5 else 0
				par.val = par.default
			elif ptype == 'menu':
				pg = page.appendMenu(par_name, label=label, replace=True)
				par = pg[0]
			elif ptype == 'pulse':
				pg = page.appendPulse(par_name, label=label, replace=True)
				par = pg[0]
			else:
				pg = page.appendFloat(par_name, label=label, replace=True)
				par = pg[0]
				par.min = 0.0
				par.max = 1.0
				par.clampMin = True
				par.clampMax = True
				par.default = max(0.0, min(1.0, pdef))
				par.val = par.default

			address = f'{addressPrefix}@{module_id}/{token.lower()}'
			# Prefer layers/... style: layer path without trailing gen duplication
			# Plan address: layer1/gen@stub/scale
			# addressPrefix for gen is layers/layer1/gen — strip layers/ for public address
			if addressPrefix.startswith('layers/'):
				short = addressPrefix[len('layers/'):]
				address = f'{short}@{module_id}/{token.lower()}'

			channel = _sanitize_channel(address)
			registry[address] = {
				'modulePath': mod.path,
				'parName': par_name,
				'channel': channel,
				'type': ptype,
				'curve': curve,
				'role': role,
				'tableMin': pmin,
				'tableMax': pmax,
			}
			registered.append(address)

		self.Registry = registry
		return registered

	def GetBase(self, address):
		info = self.Registry.get(address)
		if not info:
			return None
		mod = op(info['modulePath'])
		if mod is None:
			return None
		par = getattr(mod.par, info['parName'], None)
		if par is None:
			return None
		return float(par.eval())

	def Hello(self):
		return 'ParamEngine'
