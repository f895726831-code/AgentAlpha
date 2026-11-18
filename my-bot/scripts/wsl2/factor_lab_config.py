"""
Redirect module: re-exports from factor-lab-config.py
Python can't import files with hyphens, so this bridge handles the import.
"""
import importlib.util
import os
import sys

_target = os.path.join(os.path.dirname(__file__), 'factor-lab-config.py')
spec = importlib.util.spec_from_file_location('_factor_lab_config', _target)
_mod = importlib.util.module_from_spec(spec)
sys.modules['_factor_lab_config'] = _mod
spec.loader.exec_module(_mod)

get_qlib_bin_dir = _mod.get_qlib_bin_dir
get_qlib_csv_dir = _mod.get_qlib_csv_dir
get_qlib_region = _mod.get_qlib_region
get_qlib_freq = _mod.get_qlib_freq
get_qlib_calendar_path = _mod.get_qlib_calendar_path
get_qlib_instruments_dir = _mod.get_qlib_instruments_dir
get_qlib_features_dir = _mod.get_qlib_features_dir
get_qlib_local_path = _mod.get_qlib_local_path
get_pit_enabled = _mod.get_pit_enabled
get_pit_release_delays = _mod.get_pit_release_delays
get_pit_forbidden_operations = _mod.get_pit_forbidden_operations