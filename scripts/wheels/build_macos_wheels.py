#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Oak Ridge National Laboratory and Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""
Build the ADIOS2 wheels for macOS (prototype):

  adios2      cp312-abi3   serial build: libs, bindings, headers, cmake package,
                           with third-party dylibs (OpenSSL, libsodium, ...)
                           bundled privately by delocate
  adios2-mpi  cp312-abi3   add-on: one self-contained MPI build per MPI ABI
                           (mpich, openmpi), selected at import time by
                           adios2/bindings/__init__.py to match the MPI that
                           mpi4py loaded. Depends on adios2==<same version>.

Layout of the add-on inside the adios2 package:

  adios2/libadios2_mpi_<abi>_*.dylib          the variant's libraries
                                              (ADIOS2_LIBRARY_SUFFIX=_mpi_<abi>)
  adios2/bindings/adios2_bindings_mpi_<abi>.abi3.so
  adios2/mpi/<abi>/include, adios2/mpi/<abi>/cmake/adios2
                                              headers + cmake package of the
                                              variant (ADIOS2_HAVE_MPI on)

Each MPI variant is built with the MPI wheel (mpich / openmpi) installed in
its own venv together with mpi4py, so libmpi is referenced by soname only and
found at runtime through the library mpi4py already loaded.

Usage:
  build_macos_wheels.py --build-dir DIR --version 2.12.1.9001 \
      --python /path/venv/bin/python \
      --mpi mpich=/path/venv-mpich/bin/python \
      --mpi openmpi=/path/venv-openmpi/bin/python
"""

import argparse
import base64
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# features common to all variants; things that would link Homebrew libraries
# that delocate cannot reasonably bundle are off
COMMON_DEFINES = {
    "ADIOS2_USE_PythonStableABI": "ON",
    "ADIOS2_USE_OpenSSL": "ON",
    "ADIOS2_USE_CURL": "ON",
    "ADIOS2_USE_Sodium": "ON",
    "ADIOS2_USE_Campaign": "ON",
    "ADIOS2_USE_SealKeygen": "OFF",
    "ADIOS2_USE_XRootD": "OFF",
    "ADIOS2_USE_Catalyst": "OFF",
    "ADIOS2_USE_PNG": "OFF",
    "ADIOS2_USE_UCX": "OFF",
    "CMAKE_DISABLE_FIND_PACKAGE_LIBFABRIC": "ON",
    "ADIOS2_USE_BigWhoop": "OFF",
    "ADIOS2_USE_SZ3": "OFF",
    "ADIOS2_USE_LIBPRESSIO": "OFF",
    "ADIOS2_USE_MGARD": "OFF",
    "ADIOS2_USE_PRODM": "OFF",
    "ADIOS2_USE_Caliper": "OFF",
    "ADIOS2_USE_DAOS": "OFF",
    "ADIOS2_USE_AWSSDK": "OFF",
    "ADIOS2_USE_Kokkos": "OFF",
}


def tpl_defines(tpl_prefix):
    """
    OpenSSL and libsodium built from source with the wheel's deployment
    target (Homebrew bottles are built for the host OS and would make
    delocate retag the wheel to that OS version).
    """
    if not tpl_prefix:
        return {}
    p = Path(tpl_prefix)
    return {
        "CMAKE_PREFIX_PATH": f"{p / 'openssl'};{p / 'libsodium'}",
        "OPENSSL_ROOT_DIR": str(p / "openssl"),
    }
# never bundle the MPI runtime: it comes from the mpich / openmpi wheel
DELOCATE_EXCLUDE = ["libmpi", "libpmpi", "libopen-pal", "libopen-rte", "libpmix"]


def log(msg):
    print(f"[adios2 wheels] {msg}", flush=True)


def run(cmd, **kw):
    cmd = [str(c) for c in cmd]
    print("+ " + " ".join(cmd), flush=True)
    subprocess.check_call(cmd, **kw)


def output(cmd):
    return subprocess.check_output([str(c) for c in cmd], text=True).strip()


def pip_wheel(python, src, out_dir, build_dir, defines, env, isolation=True):
    args = [python, "-m", "pip", "wheel", src, "--no-deps", "-w", out_dir,
            "-C", f"build-dir={build_dir}",
            "-C", "cmake.build-type=Release",
            "-C", "wheel.py-api=cp312"]
    if not isolation:
        # the MPI variants need mpi4py at configure time, which an isolated
        # build environment does not have; the venv must then provide
        # scikit-build-core, setuptools_scm, ninja and numpy itself
        args.append("--no-build-isolation")
    for k, v in defines.items():
        args += ["-C", f"cmake.define.{k}={v}"]
    run(args, env=env)
    wheels = sorted(Path(out_dir).glob("adios2-*.whl"))
    if not wheels:
        raise RuntimeError(f"no wheel produced in {out_dir}")
    return wheels[-1]


def delocate(python, whl, out_dir, ignore_missing=False):
    args = [python, "-m", "delocate.cmd.delocate_wheel", "-w", out_dir,
            "--require-archs", platform.machine()]
    for e in DELOCATE_EXCLUDE:
        args += ["--exclude", e]
    if ignore_missing:
        # the MPI variants reference libmpi by soname only; it is provided at
        # runtime by the MPI library mpi4py loaded, so it must stay unresolved
        args.append("--ignore-missing-dependencies")
    args.append(whl)
    run(args)
    return Path(out_dir) / whl.name


###############################################################################
# packing of the add-on wheel
###############################################################################
def urlsafe_b64(d):
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def pack_addon(out_dir, version, tag, staging, requires, license_file):
    dist_info = f"adios2_mpi-{version}.dist-info"
    whl = out_dir / f"adios2_mpi-{version}-{tag}.whl"
    out_dir.mkdir(parents=True, exist_ok=True)
    if whl.exists():
        whl.unlink()
    metadata = ["Metadata-Version: 2.1", "Name: adios2-mpi", f"Version: {version}",
                "Summary: MPI builds of ADIOS2 for the adios2 wheel (mpich, openmpi)",
                "Requires-Python: >=3.12"] + [f"Requires-Dist: {r}" for r in requires]
    wheel_meta = ["Wheel-Version: 1.0", "Generator: adios2 build_macos_wheels.py",
                  "Root-Is-Purelib: false", f"Tag: {tag}"]
    records = []
    with zipfile.ZipFile(whl, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(staging.rglob("*")):
            if p.is_dir():
                continue
            data = p.resolve().read_bytes() if p.is_symlink() else p.read_bytes()
            arc = p.relative_to(staging).as_posix()
            info = zipfile.ZipInfo(arc, date_time=(2016, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if os.access(p, os.X_OK) else 0o644) << 16
            zf.writestr(info, data)
            records.append(f"{arc},sha256={urlsafe_b64(hashlib.sha256(data).digest())},{len(data)}")
        for name, text in (("METADATA", metadata), ("WHEEL", wheel_meta)):
            data = ("\n".join(text) + "\n").encode()
            zf.writestr(f"{dist_info}/{name}", data)
            records.append(f"{dist_info}/{name},sha256={urlsafe_b64(hashlib.sha256(data).digest())},{len(data)}")
        if license_file.exists():
            data = license_file.read_bytes()
            zf.writestr(f"{dist_info}/LICENSE", data)
            records.append(f"{dist_info}/LICENSE,sha256={urlsafe_b64(hashlib.sha256(data).digest())},{len(data)}")
        records.append(f"{dist_info}/RECORD,,")
        zf.writestr(f"{dist_info}/RECORD", "\n".join(records) + "\n")
    log(f"wrote {whl} ({whl.stat().st_size / 1e6:.1f} MB)")
    return whl


def extract_variant(whl, abi, staging):
    """copy the variant-specific files of an MPI build into the add-on staging"""
    suffix = f"_mpi_{abi}"
    keep = re.compile(
        rf"^adios2/(libadios2{suffix}_[^/]+|bindings/adios2_bindings{suffix}\.[^/]+|mpi/{abi}/.*)$")
    n = 0
    with zipfile.ZipFile(whl) as zf:
        for info in zf.infolist():
            if info.is_dir() or not keep.match(info.filename):
                continue
            dst = staging / info.filename
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(zf.read(info))
            if (info.external_attr >> 16) & 0o111:
                dst.chmod(0o755)
            n += 1
    if n == 0:
        raise RuntimeError(f"no {abi} variant files found in {whl}")
    log(f"{abi}: extracted {n} files")


def strip_absolute_rpaths(staging):
    """
    The MPI builds keep CMAKE_INSTALL_RPATH_USE_LINK_PATH, which leaves an
    absolute rpath to the build venv's lib dir (where libmpi was found).
    Remove those; libmpi is resolved through the copy mpi4py loaded.
    """
    n = 0
    for f in staging.rglob("*"):
        if f.is_dir() or f.suffix not in (".dylib", ".so"):
            continue
        out = output(["otool", "-l", f]).splitlines()
        rpaths = [ln.strip().split()[1] for ln in out
                  if ln.strip().startswith("path ")]
        changed = False
        for rp in rpaths:
            if rp.startswith("/"):
                run(["install_name_tool", "-delete_rpath", rp, f])
                changed = True
                n += 1
        if changed:
            run(["codesign", "--force", "--sign", "-", f],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log(f"stripped {n} absolute rpath entries")


def check_deps(staging):
    """every non-system dependency must be @rpath/@loader_path based"""
    bad = []
    for f in staging.rglob("*"):
        if f.is_dir() or f.suffix not in (".dylib", ".so"):
            continue
        for line in output(["otool", "-L", f]).splitlines()[1:]:
            dep = line.strip().split(" (compatibility")[0]
            if dep.startswith(("/usr/lib/", "/System/Library/", "@rpath/", "@loader_path/")):
                continue
            bad.append(f"{f.relative_to(staging)}: {dep}")
    if bad:
        raise RuntimeError("absolute dependencies left:\n  " + "\n  ".join(bad))


###############################################################################
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=None, help="ADIOS2 source dir (default: repo root)")
    ap.add_argument("--build-dir", required=True)
    ap.add_argument("--version", required=True, help="package version (written to VERSION.TXT)")
    ap.add_argument("--python", required=True,
                    help="python for the serial build (numpy, pip, delocate installed)")
    ap.add_argument("--mpi", action="append", default=[], metavar="ABI=PYTHON",
                    help="MPI variant to build: ABI name and a python whose venv has "
                         "the matching MPI wheel, mpi4py, numpy, pip and delocate")
    ap.add_argument("--tpl-prefix", default=None,
                    help="prefix holding openssl/ and libsodium/ installs built with the "
                         "deployment target (see tpl_defines)")
    ap.add_argument("--deployment-target", default="11.0")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--repack", action="store_true",
                    help="skip the builds; re-extract the MPI variants from the "
                         "existing delocated wheels and repack the add-on")
    ap.add_argument("--skip-serial", action="store_true",
                    help="reuse the serial wheel already in <build-dir>/dist")
    args = ap.parse_args()

    src = Path(args.src).resolve() if args.src else Path(__file__).resolve().parents[2]
    build = Path(args.build_dir).resolve()
    dist = build / "dist"
    tpls = tpl_defines(args.tpl_prefix)
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=args.deployment_target,
               CMAKE_BUILD_PARALLEL_LEVEL=str(args.jobs))
    if args.tpl_prefix:
        env["PKG_CONFIG_PATH"] = str(Path(args.tpl_prefix) / "libsodium" / "lib" / "pkgconfig")
    (src / "VERSION.TXT").write_text(args.version)
    try:
        # serial ------------------------------------------------------------
        if args.repack or args.skip_serial:
            serial_whl = sorted(dist.glob(f"adios2-{args.version}-*.whl"))[-1]
        else:
            log("serial build")
            raw = pip_wheel(args.python, src, build / "serial" / "raw", build / "serial" / "build",
                            {**COMMON_DEFINES, **tpls}, env)
            serial_whl = delocate(args.python, raw, dist)

        # mpi variants -------------------------------------------------------
        staging = build / "mpi" / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        for spec in args.mpi:
            abi, python = spec.split("=", 1)
            # the venv prefix (not the symlink target of bin/python)
            venv = Path(output([python, "-c", "import sys; print(sys.prefix)"]))
            vdir = build / "mpi" / abi
            if args.repack:
                extract_variant(sorted((vdir / "fixed").glob("adios2-*.whl"))[-1], abi, staging)
                continue
            log(f"MPI variant {abi} with {venv}")
            mpi4py_path = output([python, "-c", "import mpi4py; print(mpi4py.__path__[0])"])
            defines = {
                **COMMON_DEFINES, **tpls,
                "ADIOS2_USE_MPI": "ON",
                "MPI_C_COMPILER": venv / "bin" / "mpicc",
                "MPI_CXX_COMPILER": venv / "bin" / "mpicxx",
                # FindPythonModule's probe fails under scikit-build-core; the
                # finder honors a preset path
                "PythonModule_mpi4py_PATH": mpi4py_path,
                "ADIOS2_LIBRARY_SUFFIX": f"_mpi_{abi}",
                "CMAKE_INSTALL_CMAKEDIR": f"adios2/mpi/{abi}/cmake/adios2",
                "ADIOS2_PIP_INCLUDEDIR": f"adios2/mpi/{abi}/include",
            }
            raw = pip_wheel(python, src, vdir / "raw", vdir / "build", defines, env,
                            isolation=False)
            fixed = delocate(python, raw, vdir / "fixed", ignore_missing=True)
            extract_variant(fixed, abi, staging)
        if args.mpi:
            strip_absolute_rpaths(staging)
            check_deps(staging)
            tag = serial_whl.name.split("-", 2)[2].removesuffix(".whl")
            pack_addon(dist, args.version, tag, staging,
                       [f"adios2=={args.version}", "mpi4py>=4.0"], src / "LICENSE")
    finally:
        (src / "VERSION.TXT").unlink(missing_ok=True)
    log("done")
    for w in sorted(dist.glob("*.whl")):
        log(f"  {w}")


if __name__ == "__main__":
    main()
