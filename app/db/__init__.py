"""Persistence layer (Postgres via SQLAlchemy).

Kept deliberately optional: if ``DATABASE_URL`` is not set the whole layer
becomes a no-op so local runs, the eval harness, and unit tests work without a
database. When it *is* set (docker-compose wires it to the ``postgres`` service)
every finished investigation is written to the ``investigations`` table.
"""
