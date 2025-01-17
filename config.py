

extension_to_language = {
    ".py": "python",
    ".java": "java", # etc
}

FILE_EXTENSIONS = [".py"]

# configure root folder for each repo
repo_to_top_folder = {
    "django/django": "django",
    "sphinx-doc/sphinx": "sphinx",
    "scikit-learn/scikit-learn": "sklearn",
    "sympy/sympy": "sympy",
    "pytest-dev/pytest": "src/_pytest",
    "matplotlib/matplotlib": "matplotlib",
    "astropy/astropy": "astropy",
    "pydata/xarray": "xarray",
    "mwaskom/seaborn": "seaborn",
    "psf/requests": "requests",
    "pylint-dev/pylint": "pylint",
    "pallets/flask": "flask",
}

MODEL_NAME = "issue-review-local"

# max file candid
MAX_CANDIDATES = 3