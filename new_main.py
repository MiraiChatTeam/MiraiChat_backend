"""Compatibility shim for the migration backend entrypoint.

The migration entrypoint now lives in chat_backend/migration_app.py.
This file remains so existing service definitions that import `new_main:app`
continue to work unchanged.
"""

from chat_backend.migration_app import *  # noqa: F401,F403
