"""Fixtures for the store and route tests.

The pure modules — units, aisles, shopping, importer, prompts — need none of
this and deliberately do not use it. Only the two layers that touch something
outside themselves come through here.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as recipesapp  # noqa: E402
import schema  # noqa: E402
import store  # noqa: E402


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = str(tmp_path / "recipes.db")
    monkeypatch.setattr(recipesapp, "DB_PATH", path)
    store.init_db(path)
    return path


@pytest.fixture
def conn(db_path):
    connection = schema.connect(db_path)
    yield connection
    connection.close()


@pytest.fixture
def options(tmp_path, monkeypatch):
    """Write add-on options the way Supervisor does, and point config at them."""
    import config
    path = tmp_path / "options.json"
    monkeypatch.setattr(config, "OPTIONS_PATH", str(path))

    def _write(**values):
        import json
        path.write_text(json.dumps(values))
        return values

    _write()
    return _write


@pytest.fixture
def client(db_path, options):
    """A browser arriving through Home Assistant's ingress proxy."""
    recipesapp.app.config.update(TESTING=True)
    with recipesapp.app.test_client() as test_client:
        test_client.environ_base["HTTP_X_REMOTE_USER_ID"] = "test-user"
        yield test_client


def a_recipe(name="Test dish", category="Family", servings=4, ingredients=None):
    return {
        "name": name, "category": category, "servings": servings,
        "method": "1. Cook it.",
        "ingredients": ingredients if ingredients is not None else [
            {"name": "rice", "shop_name": "ris", "amount": 300, "unit": "g"},
        ],
    }
