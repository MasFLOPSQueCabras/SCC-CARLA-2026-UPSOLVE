#!/usr/bin/env bash
# Copy into each measured run; tuning overrides placement in its own settings file.
HPL_BINARY=/shared/environment/view/bin/xhpl
MPI_LAUNCHER=/shared/environment/view/bin/mpirun
MPI_LIBRARY_PATH=/shared/environment/view/lib:/shared/environment/view/lib64
MPI_HOSTFILE=/shared/hpl/hosts
HPL_RANKS=3
OMP_NUM_THREADS=1
MPI_MAP_BY=ppr:1:node:PE=1
UCX_NET_DEVICES=mlx5_0:1
HPL_TIMEOUT_SECONDS=300
