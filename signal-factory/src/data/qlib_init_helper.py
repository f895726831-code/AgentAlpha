"""Qlib init wrapper — handles missing mlflow dependency"""
import sys, types, os

# Full mlflow mock for environments where mlflow cannot be installed
_m = types.ModuleType('mlflow')
_m.exceptions = types.ModuleType('mlflow.exceptions')
_m.exceptions.MlflowException = type('MlflowException', (Exception,), {})
_m.exceptions.ExecutionException = _m.exceptions.MlflowException
_m.exceptions.RestException = _m.exceptions.MlflowException
_m.exceptions.RESOURCE_ALREADY_EXISTS = 'RESOURCE_ALREADY_EXISTS'
_m.exceptions.ErrorCode = type('ErrorCode', (), {'Name': lambda n: n})()
_m.exceptions.INVALID_PARAMETER_VALUE = 'INVALID_PARAMETER_VALUE'
_m.exceptions.INTERNAL_ERROR = 'INTERNAL_ERROR'
_m.entities = types.ModuleType('mlflow.entities')
_m.entities.RunStatus = types.ModuleType('mlflow.entities.RunStatus')
_m.entities.RunStatus.FINISHED = 'FINISHED'
_m.entities.RunStatus.to_string = lambda x: str(x)
_m.entities.RunStatus.from_string = lambda x: 'FINISHED'
_m.entities.RunInfo = type('RunInfo', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.Run = type('Run', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.RunData = type('RunData', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.Metric = type('Metric', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.Param = type('Param', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.Experiment = type('Experiment', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.FileInfo = type('FileInfo', (), {'__init__': lambda s, *a, **kw: None})
_m.entities.ViewType = types.ModuleType('mlflow.entities.ViewType')
_m.entities.ViewType.ALL = 1
_m.entities.LifecycleStage = types.ModuleType('mlflow.entities.LifecycleStage')
_m.entities.LifecycleStage.ACTIVE = 'active'

_m.tracking = types.ModuleType('mlflow.tracking')
_m.tracking.MlflowClient = type('MlflowClient', (), {
    'set_tracking_uri': staticmethod(lambda *a: None),
    'get_experiment_by_name': staticmethod(lambda *a: None),
    'create_experiment': staticmethod(lambda *a: '0'),
    'set_experiment': staticmethod(lambda *a: None),
    'search_runs': staticmethod(lambda *a: []),
    'create_run': staticmethod(lambda *a: type('R', (), {'info': type('I', (), {'run_id': '0', 'experiment_id': '0'})})()),
    'log_metric': staticmethod(lambda *a: None),
    'log_param': staticmethod(lambda *a: None),
    'log_params': staticmethod(lambda *a: None),
    'set_tag': staticmethod(lambda *a: None),
    'set_terminated': staticmethod(lambda *a: None),
    'log_artifact': staticmethod(lambda *a: None),
    'log_artifacts': staticmethod(lambda *a: None),
    'list_artifacts': staticmethod(lambda *a: []),
    'download_artifacts': staticmethod(lambda *a: '/tmp'),
    'get_run': staticmethod(lambda *a: None),
    'delete_run': staticmethod(lambda *a: None),
})

_m.set_tracking_uri = lambda *a: None
_m.set_experiment = lambda *a: None
_m.create_experiment = lambda *a: '0'
_m.start_run = lambda *a: type('Run', (), {'__enter__': lambda s: s, '__exit__': lambda *a: None})()
_m.log_metric = lambda *a: None
_m.log_param = lambda *a: None
_m.log_params = lambda *a: None
_m.set_tag = lambda *a: None
_m.log_artifact = lambda *a: None
_m.log_artifacts = lambda *a: None
_m.active_run = lambda: None
_m.end_run = lambda: None
_m.get_artifact_uri = lambda: ''
_m.MlflowClient = _m.tracking.MlflowClient

_m.tracking.fluent = types.ModuleType('mlflow.tracking.fluent')
_m.tracking.fluent.active_run = _m.active_run
_m.tracking.fluent.start_run = _m.start_run
_m.tracking.fluent.end_run = _m.end_run
_m.tracking.fluent.log_metric = _m.log_metric
_m.tracking.fluent.log_param = _m.log_param
_m.tracking.fluent.log_params = _m.log_params
_m.tracking.fluent.set_tag = _m.set_tag
_m.tracking.fluent.log_artifact = _m.log_artifact
_m.tracking.fluent.log_artifacts = _m.log_artifacts

_m.tracking._model_registry = types.ModuleType('mlflow.tracking._model_registry')
_m.tracking._model_registry.fluent = types.ModuleType('mlflow.tracking._model_registry.fluent')
_m.tracking._tracking_service = types.ModuleType('mlflow.tracking._tracking_service')
_m.tracking._tracking_service.client = types.ModuleType('mlflow.tracking._tracking_service.client')
_m.tracking._tracking_service.client.TrackingServiceClient = _m.tracking.MlflowClient

_m.store = types.ModuleType('mlflow.store')
_m.store.artifact = types.ModuleType('mlflow.store.artifact')
_m.store.artifact.artifact_repo = types.ModuleType('mlflow.store.artifact.artifact_repo')
_m.store.artifact.artifact_repo.ArtifactRepository = type('ArtifactRepository', (), {})
_m.store.artifact.runs_artifact_repo = types.ModuleType('mlflow.store.artifact.runs_artifact_repo')
_m.store.artifact.runs_artifact_repo.RunsArtifactRepository = _m.store.artifact.artifact_repo.ArtifactRepository
_m.store.artifact.local_artifact_repo = types.ModuleType('mlflow.store.artifact.local_artifact_repo')
_m.store.artifact.local_artifact_repo.LocalArtifactRepository = _m.store.artifact.artifact_repo.ArtifactRepository
_m.store.artifact.artifact_repository_registry = types.ModuleType('mlflow.store.artifact.artifact_repository_registry')
_m.store.artifact.artifact_repository_registry.get_artifact_repository = lambda *a: _m.store.artifact.local_artifact_repo.LocalArtifactRepository()

_m.store.tracking = types.ModuleType('mlflow.store.tracking')
_m.store.tracking.file_store = types.ModuleType('mlflow.store.tracking.file_store')
_m.store.tracking.file_store.FileStore = type('FileStore', (), {})
_m.store.tracking.rest_store = types.ModuleType('mlflow.store.tracking.rest_store')
_m.store.tracking.rest_store.RestStore = type('RestStore', (), {})
_m.store.tracking.sqlalchemy_store = types.ModuleType('mlflow.store.tracking.sqlalchemy_store')
_m.store.tracking.sqlalchemy_store.SqlAlchemyStore = type('SqlAlchemyStore', (), {})
_m.store.tracking.abstract_store = types.ModuleType('mlflow.store.tracking.abstract_store')
_m.store.tracking.abstract_store.AbstractStore = type('AbstractStore', (), {})

_m.projects = types.ModuleType('mlflow.projects')
_m.projects._project_spec = types.ModuleType('mlflow.projects._project_spec')
_m.pyfunc = types.ModuleType('mlflow.pyfunc')
_m.pyfunc.model = types.ModuleType('mlflow.pyfunc.model')
_m.models = types.ModuleType('mlflow.models')
_m.models.model = types.ModuleType('mlflow.models.model')
_m.models.model.Model = type('Model', (), {})
_m.models.model.get_model_info = lambda *a: None
_m.models.model.set_model = lambda *a: None

_m.environment_variables = types.ModuleType('mlflow.environment_variables')
_m.environment_variables.MLFLOW_TRACKING_URI = types.ModuleType('mlflow.environment_variables.MLFLOW_TRACKING_URI')
_m.environment_variables.MLFLOW_TRACKING_URI.get = lambda *a: ''

_m.utils = types.ModuleType('mlflow.utils')
_m.utils.mlflow_tags = types.ModuleType('mlflow.utils.mlflow_tags')
for tag in ['MLFLOW_RUN_NAME', 'MLFLOW_PARENT_RUN_ID', 'MLFLOW_USER', 'MLFLOW_SOURCE_TYPE',
            'MLFLOW_SOURCE_NAME', 'MLFLOW_GIT_COMMIT', 'MLFLOW_GIT_BRANCH',
            'MLFLOW_PROJECT_ENTRY_POINT', 'MLFLOW_PROJECT_ENV']:
    setattr(_m.utils.mlflow_tags, tag, f'mlflow.{tag.lower()}')

_m.utils.uri = types.ModuleType('mlflow.utils.uri')
_m.utils.uri.is_databricks_uri = lambda *a: False
_m.utils.uri.is_http_uri = lambda *a: False
_m.utils.uri.get_uri_scheme = lambda *a: 'file'
_m.utils.databricks_utils = types.ModuleType('mlflow.utils.databricks_utils')
_m.utils.databricks_utils.get_databricks_host_creds = lambda *a: None
_m.utils.databricks_utils.is_in_databricks_runtime = lambda: False
_m.utils.rest_utils = types.ModuleType('mlflow.utils.rest_utils')
_m.utils.rest_utils.http_request = lambda *a, **kw: type('Resp', (), {'status_code': 200, 'text': '{}', 'json': lambda: {}})()
_m.utils.rest_utils.MlflowHostCreds = type('MlflowHostCreds', (), {'__init__': lambda s, *a, **kw: None})
_m.utils.file_utils = types.ModuleType('mlflow.utils.file_utils')
_m.utils.file_utils.get_local_path_or_none = lambda x: x
_m.utils.file_utils.yield_file_in_dir = lambda *a: []
_m.utils.file_utils.TempDir = type('TempDir', (), {'__enter__': lambda s: '/tmp', '__exit__': lambda *a: None})
_m.utils.file_utils.path_to_local_file_uri = lambda x: x
_m.utils.file_utils.path_to_local_sqlite_uri = lambda x: x
_m.utils.file_utils.mkdir = lambda *a: None
_m.utils.file_utils.list_all = lambda *a: []
_m.utils.file_utils.get_file_info = lambda *a: _m.entities.FileInfo()
_m.utils.file_utils.relative_path_to_artifact_path = lambda x: x
_m.utils.file_utils.build_path = lambda *a: '/'.join(a)
_m.utils.environment = types.ModuleType('mlflow.utils.environment')
_m.utils.environment._mlflow_conda_env = lambda *a, **kw: {}
_m.utils.requirements_utils = types.ModuleType('mlflow.utils.requirements_utils')

_m.gateway = types.ModuleType('mlflow.gateway')
_m.gateway.config = types.ModuleType('mlflow.gateway.config')
_m.gateway.utils = types.ModuleType('mlflow.gateway.utils')
_m.gateway.utils.get_gateway_uri = lambda: ''
_m.gateway.utils.is_gateway_endpoint = lambda *a: False
_m.gateway.utils.set_gateway_uri = lambda *a: None

_m.deployments = types.ModuleType('mlflow.deployments')
_m.deployments.server = types.ModuleType('mlflow.deployments.server')
_m.deployments.server.runner = types.ModuleType('mlflow.deployments.server.runner')
_m.deployments.server.config = types.ModuleType('mlflow.deployments.server.config')
_m.deployments.server.config.EndpointsConfig = type('EndpointsConfig', (), {})
_m.deployments.utils = types.ModuleType('mlflow.deployments.utils')
_m.deployments.utils.get_deployments_target = lambda: ''

_m.protos = types.ModuleType('mlflow.protos')
_m.protos.databricks_pb2 = types.ModuleType('mlflow.protos.databricks_pb2')
_m.protos.databricks_pb2.RESOURCE_ALREADY_EXISTS = 'RESOURCE_ALREADY_EXISTS'
_m.protos.databricks_pb2.INVALID_PARAMETER_VALUE = 'INVALID_PARAMETER_VALUE'
_m.protos.databricks_pb2.ErrorCode = type('ErrorCode', (), {'Name': lambda n: n})()
_m.protos.databricks_pb2.INTERNAL_ERROR = 'INTERNAL_ERROR'

# Register all submodules in sys.modules
_all_modules = {}
for full_name in dir(_m):
    obj = getattr(_m, full_name)
    if isinstance(obj, types.ModuleType):
        _all_modules[f'mlflow.{full_name}'] = obj

# Recursively find submodules
def _find_submodules(obj, prefix):
    for attr_name in dir(obj):
        if attr_name.startswith('_'):
            continue
        try:
            attr = getattr(obj, attr_name)
        except:
            continue
        if isinstance(attr, types.ModuleType):
            full = f'{prefix}.{attr_name}'
            _all_modules[full] = attr
            _find_submodules(attr, full)

_find_submodules(_m, 'mlflow')

for name, mod in _all_modules.items():
    if name not in sys.modules:
        sys.modules[name] = mod

sys.modules['mlflow'] = _m

# Auto-create missing mlflow submodules on demand
class _MlflowLoader:
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith('mlflow.') and fullname not in sys.modules:
            mod = _make_mlflow_module(fullname)
            sys.modules[fullname] = mod
            from importlib.machinery import ModuleSpec
            return ModuleSpec(fullname, None, is_package=True)
        return None

def _make_mlflow_module(name):
    """Create a module that auto-creates submodules on attribute access"""
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__getattr__ = lambda n: _auto_submodule(name, n)
    return mod

def _auto_submodule(parent, child):
    """Auto-create submodule on attribute access"""
    full = f'{parent}.{child}'
    if full not in sys.modules:
        mod = _make_mlflow_module(full)
        sys.modules[full] = mod
        setattr(sys.modules.get(parent), child, mod)
    return sys.modules[full]

sys.meta_path.insert(0, _MlflowLoader())

# Ensure all existing mlflow modules are package-like with auto-submodule
for name, mod in list(sys.modules.items()):
    if name.startswith('mlflow.') and not hasattr(mod, '__path__'):
        mod.__path__ = []
    if name.startswith('mlflow.') and not hasattr(mod, '__getattr__'):
        mod.__getattr__ = lambda n, p=name: _auto_submodule(p, n)

print("mlflow mock installed, Qlib can now import")

# Also mock gym if not available (Qlib's RL module imports it)
try:
    import gym
except ImportError:
    _gym = types.ModuleType('gym')
    _gym.spaces = types.ModuleType('gym.spaces')
    _gym.spaces.Box = type('Box', (), {'__init__': lambda s, *a, **kw: None})
    _gym.spaces.Discrete = type('Discrete', (), {'__init__': lambda s, *a, **kw: None})
    _gym.Env = type('Env', (), {})
    _gym.Wrapper = type('Wrapper', (), {'__init__': lambda s, *a, **kw: None})
    sys.modules['gym'] = _gym
    sys.modules['gym.spaces'] = _gym.spaces

# Recursively find submodules
def _find_submodules(obj, prefix):
    for attr_name in dir(obj):
        if attr_name.startswith('_'):
            continue
        try:
            attr = getattr(obj, attr_name)
        except:
            continue
        if isinstance(attr, types.ModuleType):
            full = f'{prefix}.{attr_name}'
            _all_modules[full] = attr
            _find_submodules(attr, full)

_find_submodules(_m, 'mlflow')

for name, mod in _all_modules.items():
    if name not in sys.modules:
        sys.modules[name] = mod

sys.modules['mlflow'] = _m

# Now Qlib can be imported safely
print("mlflow mock installed, Qlib can now import")