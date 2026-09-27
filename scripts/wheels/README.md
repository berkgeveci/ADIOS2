# ADIOS2 macOS wheels (prototype)

`build_macos_wheels.py` builds two wheels:

| wheel        | tag          | contents                                                                 |
|--------------|--------------|--------------------------------------------------------------------------|
| `adios2`     | `cp312-abi3` | serial build: libraries, bindings, headers, CMake package; OpenSSL and libsodium bundled privately under `adios2/.dylibs` |
| `adios2-mpi` | `cp312-abi3` | add-on: one self-contained MPI build per MPI ABI (`mpich`, `openmpi`), selected at import time |

Both are stable-ABI builds (`ADIOS2_USE_PythonStableABI`), so one wheel per
platform serves Python 3.12 and newer.

## Layout in site-packages

```
adios2/
  bindings/adios2_bindings.abi3.so             serial bindings
  bindings/adios2_bindings_mpi_mpich.abi3.so   MPI bindings (adios2-mpi)
  bindings/adios2_bindings_mpi_openmpi.abi3.so
  bindings/__init__.py                         picks the build at import time
  libadios2_*.dylib                            serial libraries
  libadios2_mpi_mpich_*.dylib                  MPI build, MPICH ABI (adios2-mpi)
  libadios2_mpi_openmpi_*.dylib                MPI build, Open MPI ABI (adios2-mpi)
  .dylibs/                                     bundled third-party libraries
  include/, cmake/adios2/                      serial headers + CMake package
  mpi/<abi>/include, mpi/<abi>/cmake/adios2    per-variant headers + CMake package
```

## Choosing the MPI build

`adios2/bindings/__init__.py` imports the MPI bindings when `mpi4py.MPI`
was imported before `adios2`, or when `ADIOS2_MPI=1` is set; `ADIOS2_MPI=0`
forces the serial build. The MPI ABI is whatever mpi4py loaded (it dlopens
`libmpi` and probes it), so the ADIOS2 MPI libraries, which reference
`libmpi` by soname only, bind to the same library. With the `mpich` or
`openmpi` wheels from PyPI this needs no system MPI at all:

```bash
pip install adios2 adios2-mpi mpi4py mpich
mpiexec -n 4 python -c "from mpi4py import MPI; import adios2; assert adios2.is_built_with_mpi"
```

C++ consumers of an MPI build point `ADIOS2_DIR` at
`adios2/mpi/<abi>/cmake/adios2` and use `find_package(ADIOS2 COMPONENTS MPI)`.

## How the variants are built

Each MPI variant is a complete ADIOS2 build with `ADIOS2_USE_MPI=ON`,
configured against the MPI wheel installed in its own venv (its `mpicc`
wrapper is relocatable), with `ADIOS2_LIBRARY_SUFFIX=_mpi_<abi>` so its
libraries and bindings module get distinct names, `ADIOS2_PIP_INCLUDEDIR`
and `CMAKE_INSTALL_CMAKEDIR` pointing under `adios2/mpi/<abi>/`, and mpi4py
from the venv. The variant wheel is delocated with the MPI runtime excluded,
and only the variant-specific files are copied into the add-on wheel, which
pins `adios2==<same version>`.

Prerequisites per MPI venv: the MPI wheel (`mpich` or `openmpi`), `mpi4py`,
`numpy`, `pip`, `delocate`, `scikit-build-core`, `setuptools_scm`, `ninja`.
The variants build without pip's build isolation so that mpi4py is visible
at configure time.

## Private third-party libraries

`delocate` copies every non-system dependency into `adios2/.dylibs` and
rewrites the references to `@loader_path/.dylibs/<name>`. On macOS no
further mangling is needed: two-level namespaces bind symbols per library
and the loader identifies libraries by path, so another package's copy of
e.g. libcrypto cannot be substituted for ours.

The bundled libraries must be built for the wheel's deployment target.
Homebrew bottles target the host OS, and delocate would retag the wheel to
that version, so build them from source, e.g.

```bash
./configure CFLAGS=-mmacosx-version-min=11.0 LDFLAGS=-mmacosx-version-min=11.0 --prefix=$TPLS/libsodium --disable-static   # libsodium
./Configure darwin64-arm64-cc shared no-tests no-docs -mmacosx-version-min=11.0 --prefix=$TPLS/openssl --libdir=lib          # openssl
```

and pass `--tpl-prefix $TPLS`.

## Usage

```bash
python scripts/wheels/build_macos_wheels.py \
    --build-dir /path/to/build --version 2.12.1.9002 \
    --python /path/venv/bin/python \
    --mpi mpich=/path/venv-mpich/bin/python \
    --mpi openmpi=/path/venv-openmpi/bin/python \
    --tpl-prefix $TPLS
```

Wheels land in `<build-dir>/dist/`.

## Notes

* `FindPythonModule`'s mpi4py probe does not work under scikit-build-core;
  the script passes `PythonModule_mpi4py_PATH` explicitly.
* The MPI builds keep `CMAKE_INSTALL_RPATH_USE_LINK_PATH`, so an absolute
  rpath to the build venv is left in the variant libraries; delocate's rpath
  sanitizing removes it, and the script strips any that survive.
* The variant wheels are delocated with `--ignore-missing-dependencies`,
  since their `@rpath/libmpi` references are meant to stay unresolved.
* pip's build isolation is off for the variants, so their venvs must
  provide `scikit-build-core`, `setuptools_scm`, `ninja` and `numpy`.
* Linux would use the same layout with auditwheel and `$ORIGIN`.
