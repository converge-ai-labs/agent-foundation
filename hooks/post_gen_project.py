"""Keep only the files that belong to the selected App surface."""

import shutil

if {{ cookiecutter.surface | tojson }} == "backend-only":
    shutil.rmtree("frontend")
