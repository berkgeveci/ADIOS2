"""Two-rank ADIOS2 MPI round trip; run under mpiexec -n 2."""
import sys

from mpi4py import MPI

comm = MPI.COMM_WORLD
rank, size = comm.Get_rank(), comm.Get_size()


def log(*a):
    print(f"[{rank}]", *a, flush=True)


import numpy as np
import adios2

log("adios2", adios2.__version__, "mpi build:", adios2.is_built_with_mpi,
    "bindings:", adios2.bindings.__name__ if hasattr(adios2.bindings, "__name__") else "?")
assert adios2.is_built_with_mpi

ad = adios2.Adios(comm=comm)
io = ad.declare_io("w")
data = np.full(4, rank, dtype=np.float64)
v = io.define_variable("x", data, shape=[4 * size], start=[4 * rank], count=[4])
e = io.open("mpi_test.bp", adios2.bindings.Mode.Write)
e.begin_step()
e.put(v, data)
e.end_step()
e.close()
comm.Barrier()

# collective read on the same communicator
with adios2.FileReader("mpi_test.bp", comm) as f:
    out = f.read("x")
expected = np.repeat(np.arange(size, dtype=np.float64), 4)
assert np.array_equal(out, expected), (out, expected)
if rank == 0:
    log("round trip ok:", out.tolist())
