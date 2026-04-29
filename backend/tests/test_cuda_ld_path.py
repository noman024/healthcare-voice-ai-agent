from __future__ import annotations

from app.hardware import cuda_ld_path


def test_discover_cuda_lib_dirs_includes_explicit_path(tmp_path, monkeypatch) -> None:
    d = tmp_path / "cuda-explicit"
    d.mkdir()
    monkeypatch.setenv("CUDA_LIBRARY_PATH", str(d))
    monkeypatch.delenv("CUDA_HOME", raising=False)
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    found = cuda_ld_path._discover_cuda_lib_dirs()
    assert str(d.resolve()) in found


def test_discover_nvidia_pip_respects_site_packages(tmp_path, monkeypatch) -> None:
    base = tmp_path / "site"
    nlib = base / "nvidia" / "cublas" / "lib"
    nlib.mkdir(parents=True)
    monkeypatch.setattr(cuda_ld_path.site, "getsitepackages", lambda: [str(base)])
    monkeypatch.setattr(cuda_ld_path.site, "getusersitepackages", lambda: "")
    dirs = cuda_ld_path._discover_nvidia_pip_lib_dirs()
    assert str(nlib.resolve()) in dirs
