import importlib


SOURCES = {
    "garmin": "sources.garmin",
    "sqlite_import": "sources.sqlite_import",
}


def source_names():
    return sorted(SOURCES)


def load_source(name):
    try:
        module_path = SOURCES[name]
    except KeyError as e:
        raise ValueError(f"Unknown source: {name}") from e
    return importlib.import_module(module_path)
