"""Shared setup for the property-based tests (Hypothesis) in test/.

Importing it puts the repository root on sys.path, stubs mysql.connector
when it isn't installed (the tests never connect), and loads a Hypothesis
settings profile: "default" (1,000 examples per property) or "thorough"
(10,000), chosen with the HYPOTHESIS_PROFILE environment variable.
"""
import os
import sys
import types

parent_dir = os.path.normpath(os.path.join(os.path.dirname(os.path.realpath(__file__)), '../'))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    import mysql.connector  # noqa: F401
except ImportError:
    mysql_module = types.ModuleType('mysql')
    connector_module = types.ModuleType('mysql.connector')
    connector_module.errors = types.ModuleType('mysql.connector.errors')
    mysql_module.connector = connector_module
    native_module = types.ModuleType('_mysql_connector')
    native_module.MySQLInterfaceError = type('MySQLInterfaceError', (Exception,), {})
    sys.modules.update({'mysql': mysql_module, 'mysql.connector': connector_module,
                        'mysql.connector.errors': connector_module.errors,
                        '_mysql_connector': native_module})

from hypothesis import settings  # noqa: E402

settings.register_profile('default', max_examples=1000, deadline=None)
settings.register_profile('thorough', max_examples=10000, deadline=None)
settings.load_profile(os.environ.get('HYPOTHESIS_PROFILE', 'default'))
