#!/bin/bash

# Copyright (C) 2017-2022 elphmod Developers
# This program is free software under the terms of the GNU GPLv3 or later.

set -e

for example in *.py
do
    mpirun python3 $example
done
