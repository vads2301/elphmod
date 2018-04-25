#/usr/bin/env python

from . import bravais

import numpy as np

def read_flfrc(flfrc):
    """Read file 'flfrc' with force constants generated by 'q2r.x'."""

    with open(flfrc) as data:
        # read all words of current line:

        def cells():
            return data.readline().split()

        # read table:

        def table(rows):
            return np.array([map(float, cells()) for row in range(rows)])

        # read crystal structure:

        tmp = cells()
        ntyp, nat, ibrav = list(map(int, tmp[:3]))
        celldim = list(map(float, tmp[3:]))

        if ibrav == 0:
            at = table(3)
        elif ibrav == 4:
            at = np.empty((3, 3))

            at[0] = np.array([ 1.0,        0.0, 0.0]) * celldim[0]
            at[1] = np.array([-1.0, np.sqrt(3), 0.0]) * celldim[0] / 2
            at[2] = np.array([ 0.0,        0.0, 1.0]) * celldim[0] * celldim[2]
        else:
            print('Bravais lattice unknown')
            return

        # read palette of atomic species and masses:

        atm = []
        amass = np.empty(ntyp)

        for nt in range(ntyp):
            tmp = cells()

            atm.append(tmp[1][1:3])
            amass[nt] = float(tmp[-1])

        # read types and positions of individual atoms:

        ityp = np.empty(nat, dtype=int)
        tau = np.empty((nat, 3))

        for na in range(nat):
            tmp = cells()

            ityp[na] = int(tmp[1]) - 1
            tau[na, :] = list(map(float, tmp[2:5]))

        tau *= celldim[0]

        # read macroscopic dielectric function and effective charges:

        lrigid = cells()[0] == 'T'

        if lrigid:
            epsil = table(3)

            zeu = np.empty((nat, 3, 3))

            for na in range(nat):
                zeu[na] = table(3)

        # read interatomic force constants:

        nr1, nr2, nr3 = map(int, cells())

        phid = np.empty((nat, nat, nr1, nr2, nr3, 3, 3))

        for j1 in range(3):
            for j2 in range(3):
                for na1 in range(nat):
                    for na2 in range(nat):
                        cells() # skip line with j1, j2, na2, na2

                        for m3 in range(nr3):
                            for m2 in range(nr2):
                                for m1 in range(nr1):
                                    phid[na1, na2, m1, m2, m3, j1, j2] \
                                        = float(cells()[-1])

    # return force constants, masses, and geometry:

    return [phid, amass[ityp], at, tau]

def asr(phid):
    """Apply simple acoustic sum rule correction to force constants."""

    nat, nr1, nr2, nr3 = phid.shape[1:5]

    for na1 in range(nat):
        phid[na1, na1, 0, 0, 0] = -sum(
        phid[na1, na2, m1, m2, m3]
            for na2 in range(nat)
            for m1 in range(nr1)
            for m2 in range(nr2)
            for m3 in range(nr3)
            if na1 != na2 or m1 or m2 or m3)

def dynamical_matrix(comm, phid, amass, at, tau, eps=1e-7):
    """Set up dynamical matrix for force constants, masses, and geometry."""

    nat, nr1, nr2, nr3 = phid.shape[1:5]

    supercells = [-1, 0, 1] # indices of central and neighboring supercells

    maxdim = nat ** 2 * nr1 * nr2 * nr3 * len(supercells) ** 3 // comm.size

    atoms = np.empty((maxdim, 2), dtype=np.int8) # atom indices
    cells = np.empty((maxdim, 3), dtype=np.int8) # cell indices
    const = np.empty((maxdim, 3, 3)) # force constants divided by masses

    n = 0 # 'spring' counter (per process)
    N = 0 # 'spring' counter (overall)

    for m1 in range(nr1):
        for m2 in range(nr2):
            for m3 in range(nr3):
                N += 1

                if N % comm.size != comm.rank:
                    continue

                # determine equivalent unit cells within considered supercells:

                copies = np.array([[
                        m1 + M1 * nr1,
                        m2 + M2 * nr2,
                        m3 + M3 * nr3,
                        ]
                    for M1 in supercells
                    for M2 in supercells
                    for M3 in supercells
                    ])

                # calculate corresponding translation vectors:

                shifts = [np.dot(copy, at) for copy in copies]

                for na1 in range(nat):
                    for na2 in range(nat):
                        # find equivalent bond(s) within Wigner-Seitz cell:

                        bonds = [r + tau[na1] - tau[na2] for r in shifts]
                        lengths = [np.sqrt(np.dot(r, r)) for r in bonds]
                        length = min(lengths)

                        selected = copies[np.where(abs(lengths - length) < eps)]

                        # undo supercell double counting and divide by masses:

                        C = phid[na1, na2, m1, m2, m3] / (
                            len(selected) * np.sqrt(amass[na1] * amass[na2]))

                        # save data for dynamical matrix calculation:

                        for R in selected:
                            atoms[n] = [na1, na2]
                            cells[n] = R
                            const[n] = C

                            n += 1

    # gather data of all processes:

    dims = np.array(comm.allgather(n))
    dim = dims.sum()

    allatoms = np.empty((dim, 2), dtype=np.int8)
    allcells = np.empty((dim, 3), dtype=np.int8)
    allconst = np.empty((dim, 3, 3))

    comm.Allgatherv(atoms[:n], (allatoms, dims * 2))
    comm.Allgatherv(cells[:n], (allcells, dims * 3))
    comm.Allgatherv(const[:n], (allconst, dims * 9))

    # (see cdef _p_message message_vector in mpi4py/src/mpi4py/MPI/msgbuffer.pxi
    # for possible formats of second argument 'recvbuf')

    # return function to calculate dynamical matrix for arbitrary q points:

    def calculate_dynamical_matrix(q1=0, q2=0, q3=0):
        q = np.array([q1, q2, q3])
        D = np.zeros((3 * nat, 3 * nat), dtype=complex)

        for (na1, na2), R, C in zip(allatoms, allcells, allconst):
            D[na1::nat, na2::nat] += C * np.exp(1j * np.dot(R, q))

        return D

    calculate_dynamical_matrix.size = 3 * nat

    return calculate_dynamical_matrix

def sgnsqrt(w2):
    """Calculate signed square root."""

    return np.sign(w2) * np.sqrt(np.absolute(w2))

def polarization(e, path):
    """Characterize as in-plane longitudinal/transverse or out-of-plane."""

    bands = e.shape[1]

    mode = np.empty((len(path), bands, 3))

    nat = bands // 3

    x = slice(0, nat)
    y = slice(nat, 2 * nat)
    z = slice(2 * nat, 3 * nat)

    for n, q in enumerate(path):
        q = q[0] * bravais.u1 + q[1] * bravais.u2
        Q = np.sqrt(q.dot(q))

        centered = Q < 1e-10

        if centered:
            q = np.array([1, 0])
        else:
            q /= Q

        for band in range(bands):
            L = sum(abs(e[n, x, band] * q[0] + e[n, y, band] * q[1]) ** 2)
            T = sum(abs(e[n, x, band] * q[1] - e[n, y, band] * q[0]) ** 2)
            Z = sum(abs(e[n, z, band]) ** 2)

            if centered:
                L = T = (L + T) / 2

            mode[n, band, :] = [L, T, Z]

    return mode
