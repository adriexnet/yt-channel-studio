"""Cell 1 of the launcher notebook, run for real in a subprocess against a local git repository:
first clone, update after a new commit, a fixed version tag, and the Drive copy when GitHub is unreachable."""
import json, os, shutil, subprocess, sys
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FAKE_COLAB = "import sys, types\ncolab = types.ModuleType('google.colab')\ncolab.drive = types.SimpleNamespace(mount=lambda path: None)\n" \
             "sys.modules['google'] = types.ModuleType('google')\nsys.modules['google.colab'] = colab\n"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                            GIT_COMMITTER_EMAIL="t@t"))


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    git("init", "-q", "-b", "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "v1", cwd=repo)
    git("tag", "v1.0.0", cwd=repo)
    return repo


def run_cell_1(tmp_path, repo_url, version="main"):
    nb = json.load(open(f"{ROOT}/notebook/YT-Channel-Studio.ipynb"))
    code = "".join(nb["cells"][1]["source"])
    code = code.replace('REPO = "https://github.com/adriexnet/yt-channel-studio.git"', f'REPO = "{repo_url}"')
    code = code.replace('VERSION = "main"', f'VERSION = "{version}"')
    code = code.replace('CODE = "/content/yt-channel-studio"', f'CODE = "{tmp_path}/code"')
    code = code.replace('DRIVE_COPY = "/content/temp_drive/MyDrive/YT-Channel-Studio/code"', f'DRIVE_COPY = "{tmp_path}/drive_copy"')
    code = code.replace("setup.base()", "print('LOADED_FROM', studio.__file__, studio.__version__)")
    code = code.replace('subprocess.run([sys.executable, "-m", "pip", "install", "-q", "toml"], check=True)', "")
    result = subprocess.run([sys.executable, "-c", FAKE_COLAB + code], capture_output=True, text=True,
                            cwd=tmp_path, env=dict(os.environ, PYTHONPATH=""))
    assert result.returncode == 0, result.stderr[-1500:]
    return result.stdout


def test_first_clone_then_update_then_fixed_version(tmp_path, origin):
    out = run_cell_1(tmp_path, origin)
    assert f"LOADED_FROM {tmp_path}/code/studio/__init__.py 2.0.0" in out
    init = origin / "studio" / "__init__.py"
    init.write_text(init.read_text().replace('__version__ = "2.0.0"', '__version__ = "2.0.1"'))
    git("commit", "-q", "-am", "v2", cwd=origin)
    assert "2.0.1" in run_cell_1(tmp_path, origin)                     # re-running Cell 1 brings the update
    assert "2.0.0" in run_cell_1(tmp_path, origin, version="v1.0.0")   # and a tag pins an older version


def test_drive_copy_when_github_is_unreachable(tmp_path, origin):
    shutil.copytree(origin, tmp_path / "drive_copy", ignore=shutil.ignore_patterns(".git"))
    out = run_cell_1(tmp_path, tmp_path / "does-not-exist")
    assert "GitHub did not answer" in out and f"LOADED_FROM {tmp_path}/code/studio/__init__.py" in out
