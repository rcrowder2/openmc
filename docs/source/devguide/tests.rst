.. _devguide_tests:

==========
Test Suite
==========

The OpenMC test suite consists of two parts, a regression test suite and a unit
test suite. The regression test suite is based on regression or integrated
testing where different types of input files are configured and the full OpenMC
code is executed. Results from simulations are compared with expected
results. The unit tests are primarily intended to test individual
functions/classes in the OpenMC Python API.

Prerequisites
-------------

- The test suite relies on the third-party `pytest <https://docs.pytest.org>`_
  package. To run either or both the regression and unit test suites, it is
  assumed that you have OpenMC fully installed, i.e., the :ref:`scripts_openmc`
  executable is available on your :envvar:`PATH` and the :mod:`openmc` Python
  module is importable. In development where it would be onerous to continually
  install OpenMC every time a small change is made, it is recommended to install
  OpenMC in development/editable mode. With setuptools, this is accomplished by
  running::

      python -m pip install -e .[test]

- The test suite requires a specific set of cross section data in order for
  tests to pass. A download URL for the data that OpenMC expects can be found
  within ``tools/ci/download-xs.sh``. Once the tarball is downloaded and
  unpacked, set the :envvar:`OPENMC_CROSS_SECTIONS` environment variable to the
  path of the ``cross_sections.xml`` file within the unpacked data.
- In addition to the HDF5 data, some tests rely on ENDF files. A download URL
  for those can also be found in ``tools/ci/download-xs.sh``. Once the tarball
  is downloaded and unpacked, set the :envvar:`OPENMC_ENDF_DATA` environment
  variable to the top-level directory of the unpacked tarball.
- Some tests require `NJOY <https://www.njoy21.io/NJOY2016>`_ to preprocess
  cross section data. The test suite assumes that you have an ``njoy``
  executable available on your :envvar:`PATH`.

Running Tests
-------------

To execute the Python test suite, go to the ``tests/`` directory and run::

    pytest

If you want to collect information about source line coverage in the Python API,
you must have the `pytest-cov <https://pypi.org/project/pytest-cov>`_ plugin
installed and run::

    pytest --cov=../openmc --cov-report=html

To execute the C++ test suite, go to your build directory and run::

    ctest

If you want to view testing output on failure run::

    ctest --output-on-failure

Possible Reasons for Test Failures
----------------------------------

You may find that when you run the test suite, not everything passes. First,
make sure you have satisfied all the prerequisites above. After you have done
that, consider the following:

- When building OpenMC, make sure you run CMake with
  ``-DCMAKE_BUILD_TYPE=Debug``. Building with a release build will result in
  some test failures due to differences in which compiler optimizations are
  used.
- Because tallies involve the sum of many floating point numbers, the
  non-associativity of floating point numbers can result in different answers
  especially when the number of threads is high (different order of operations).
  Thus, if you are running on a CPU with many cores, you may need to limit the
  number of OpenMP threads used. It is recommended to set the
  :envvar:`OMP_NUM_THREADS` environment variable to 2.
- Recent versions of NumPy use instruction dispatch that may generate different
  results depending the particular ISA that you are running on. To avoid issues,
  you may need to disable AVX512 instructions. This can be done by setting the
  :envvar:`NPY_DISABLE_CPU_FEATURES` environment variable to "AVX512F
  AVX512_SKX". When NumPy/SciPy are built against OpenBLAS, you may also need to
  limit the number of threads that OpenBLAS uses internally; this can be done by
  setting the :envvar:`OPENBLAS_NUM_THREADS` environment variable to 1.

Debugging Tests in CI
---------------------

.. _tmate: <https://github.com/mxschmitt/action-tmate?tab=readme-ov-file#debug-your-github-actions-by-using-tmate>`_

Tests can be debugged in CI using a feature called `tmate`_. CI debugging can be
enabled by including "[gha-debug]" in the commit message. When the test fails, a
link similar to the one shown below will be provided in the GitHub Actions
output after failure occurs. Logging into the provided link will allow you to
debug the test in the CI environment. The following is an example of the output
shown in the CI log that provides the link to the tmate session:

.. code-block:: text
   :linenos:

   Created new session successfully
   ssh 2VcykjU7vNdvAzEjQcc839GM2@nyc1.tmate.io
   https://tmate.io/t/2VcykjU7vNdvAzEjQcc839GM2
   Entering main loop
   Web shell: https://tmate.io/t/2VcykjU7vNdvAzEjQcc839GM2
   SSH: ssh 2VcykjU7vNdvAzEjQcc839GM2@nyc1.tmate.io
   ...


Generating XML Inputs
---------------------

Many of the regression tests rely on the Python API to build an appropriate
model. However, it can sometimes be desirable to work directly with the XML
input files rather than having to run a script in order to run the problem/test.
To build the input files for a test without actually running the test, you can
run::

    pytest --build-inputs <name-of-test>

Adding C++ Unit Tests
---------------------

The C++ test suite uses Catch2 integrated with CTest. Each header file should
have a corresponding test file in ``tests/cpp_unit_tests/``. If the test file
does not exist run::

    touch test_<name-of-header-file>.cpp

The file must be added to the CMake build system in
``tests/cpp_unit_tests/CMakeLists.txt``. ``test_<name-of-header-file>`` should
be added to ``TEST_NAMES``.

To add a test case to ``test_<name-of-header-file>.cpp`` ensure
``catch2/catch_test_macros.hpp`` is included. A unit test can then be added
using the ``TEST_CASE`` macro and the ``REQUIRE`` assertion from Catch2.

Adding Tests to the Regression Suite
------------------------------------

To add a new test to the regression test suite, create a sub-directory in the
``tests/regression_tests/`` directory. To configure a test you need to add the
following files to your new test directory:

    * OpenMC input XML files, if they are not generated through the Python API
    * **test.py** - Python test driver script; please refer to other tests to
      see how to construct. Any output files that are generated during testing
      must be removed at the end of this script.
    * **inputs_true.dat** - ASCII file that contains Python API-generated XML
      files concatenated together. When the test is run, inputs that are
      generated are compared to this file.
    * **results_true.dat** - ASCII file that contains the expected results from
      the test. The file *results_test.dat* is compared to this file during the
      execution of the python test driver script. When the above files have been
      created, generate a *results_test.dat* file and copy it to this name and
      commit. It should be noted that this file should be generated with basic
      compiler options during openmc configuration and build (e.g., no MPI, no
      debug/optimization).

For tests using the Python API, both the **inputs_true.dat** and
**results_true.dat** files can be generated automatically in the correct format
via::

    pytest --update <name-of-test>

In addition to this description, please see the various types of tests that are
already included in the test suite to see how to create them. If all is
implemented correctly, the new test will automatically be discovered by pytest.
