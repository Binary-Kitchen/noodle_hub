import importlib
import logging
import sys
import glob


from types import ModuleType


SYS_MOD_PATHS = glob.glob("/usr/lib/python3*/dist-packages")
SYS_MOD_PATHS += glob.glob("/usr/lib/python3*/site-packages")

def load_system_module(name: str) -> ModuleType:
    for module_path in SYS_MOD_PATHS:
        sys.path.insert(0, module_path)
        try:
            module = importlib.import_module(name)
        except ImportError as e:
            if not isinstance(e, ModuleNotFoundError):
                logging.exception(f"Failed to load {name} module")
            sys.path.pop(0)
        else:
            sys.path.pop(0)
            break
    else:
        raise Error(f"Unable to import module {name}")
    return module