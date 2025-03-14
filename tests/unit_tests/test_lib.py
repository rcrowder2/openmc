from collections.abc import Mapping
from math import pi
import os

import numpy as np
import pytest
import openmc
import openmc.exceptions as exc
import openmc.lib

from tests import cdtemp


@pytest.fixture(scope='module')
def pincell_model():
    """Set up a model to test with and delete files when done"""
    openmc.reset_auto_ids()
    pincell = openmc.examples.pwr_pin_cell()
    pincell.settings.verbosity = 1

    # Add a tally
    filter1 = openmc.MaterialFilter(pincell.materials)
    filter2 = openmc.EnergyFilter([0.0, 1.0, 1.0e3, 20.0e6])
    mat_tally = openmc.Tally()
    mat_tally.filters = [filter1, filter2]
    mat_tally.nuclides = ['U235', 'U238']
    mat_tally.scores = ['total', 'elastic', '(n,gamma)']
    pincell.tallies.append(mat_tally)

    # Add an expansion tally
    zernike_tally = openmc.Tally()
    filter3 = openmc.ZernikeFilter(5, r=.63)
    cells = pincell.geometry.root_universe.cells
    filter4 = openmc.CellFilter(list(cells.values()))
    zernike_tally.filters = [filter3, filter4]
    zernike_tally.scores = ['fission']
    pincell.tallies.append(zernike_tally)

    # Add an energy function tally
    energyfunc_tally = openmc.Tally()
    energyfunc_filter = openmc.EnergyFunctionFilter(
        [0.0, 20e6], [0.0, 20e6])
    energyfunc_tally.scores = ['fission']
    energyfunc_tally.filters = [energyfunc_filter]
    pincell.tallies.append(energyfunc_tally)

    # Write XML files in tmpdir
    with cdtemp():
        pincell.export_to_xml()
        yield


@pytest.fixture(scope='module')
def uo2_trigger_model():
    """Set up a simple UO2 model with k-eff trigger"""
    model = openmc.model.Model()
    m = openmc.Material(name='UO2')
    m.add_nuclide('U235', 1.0)
    m.add_nuclide('O16', 2.0)
    m.set_density('g/cm3', 10.0)
    model.materials.append(m)

    cyl = openmc.ZCylinder(r=1.0, boundary_type='vacuum')
    c = openmc.Cell(fill=m, region=-cyl)
    model.geometry.root_universe = openmc.Universe(cells=[c])

    model.settings.batches = 10
    model.settings.inactive = 5
    model.settings.particles = 100
    model.settings.source = openmc.IndependentSource(
        space=openmc.stats.Box([-0.5, -0.5, -1], [0.5, 0.5, 1]),
        constraints={'fissionable': True},
    )
    model.settings.verbosity = 1
    model.settings.keff_trigger = {'type': 'std_dev', 'threshold': 0.001}
    model.settings.trigger_active = True
    model.settings.trigger_max_batches = 10
    model.settings.trigger_batch_interval = 1

    # Write XML files in tmpdir
    with cdtemp():
        model.export_to_xml()
        yield


@pytest.fixture(scope='module')
def lib_init(pincell_model, mpi_intracomm):
    openmc.lib.init(intracomm=mpi_intracomm)
    yield
    openmc.lib.finalize()


@pytest.fixture(scope='module')
def lib_simulation_init(lib_init):
    openmc.lib.simulation_init()
    yield


@pytest.fixture(scope='module')
def lib_run(lib_simulation_init):
    openmc.lib.run()


@pytest.fixture(scope='module')
def pincell_model_w_univ():
    """Set up a model to test with and delete files when done"""
    openmc.reset_auto_ids()
    pincell = openmc.examples.pwr_pin_cell()
    clad_univ = openmc.Universe(cells=[openmc.Cell(fill=pincell.materials[1])])
    pincell.geometry.root_universe.cells[2].fill = clad_univ
    pincell.settings.verbosity = 1

    # Write XML files in tmpdir
    with cdtemp():
        pincell.export_to_xml()
        yield


def test_cell_mapping(lib_init):
    cells = openmc.lib.cells
    assert isinstance(cells, Mapping)
    assert len(cells) == 3
    for cell_id, cell in cells.items():
        assert isinstance(cell, openmc.lib.Cell)
        assert cell_id == cell.id


def test_cell(lib_init):
    cell = openmc.lib.cells[1]
    assert isinstance(cell.fill, openmc.lib.Material)
    cell.fill = openmc.lib.materials[1]
    assert str(cell) == '<Cell(id=1)>'
    assert cell.name == "Fuel"
    cell.name = "Not fuel"
    assert cell.name == "Not fuel"
    assert cell.num_instances == 1


def test_cell_temperature(lib_init):
    cell = openmc.lib.cells[1]
    cell.set_temperature(100.0, 0)
    assert cell.get_temperature(0) == pytest.approx(100.0)
    cell.set_temperature(200)
    assert cell.get_temperature() == pytest.approx(200.0)


def test_properties_temperature(lib_init):
    # Cell temperature should be 200 from above test
    cell = openmc.lib.cells[1]
    assert cell.get_temperature() == pytest.approx(200.0)

    # Export properties and change temperature
    openmc.lib.export_properties('properties.h5')
    cell.set_temperature(300.0)
    assert cell.get_temperature() == pytest.approx(300.0)

    # Import properties and check that temperature is restored
    openmc.lib.import_properties('properties.h5')
    assert cell.get_temperature() == pytest.approx(200.0)


def test_new_cell(lib_init):
    with pytest.raises(exc.AllocationError):
        openmc.lib.Cell(1)
    new_cell = openmc.lib.Cell()
    new_cell_with_id = openmc.lib.Cell(10)
    assert len(openmc.lib.cells) == 5


def test_properties_fail_cell(lib_init):
    # The number of cells was changed in the previous test, so the properties
    # file is no longer valid
    with pytest.raises(exc.GeometryError, match="Number of cells"):
        openmc.lib.import_properties("properties.h5")


def test_material_mapping(lib_init):
    mats = openmc.lib.materials
    assert isinstance(mats, Mapping)
    assert len(mats) == 3
    for mat_id, mat in mats.items():
        assert isinstance(mat, openmc.lib.Material)
        assert mat_id == mat.id


def test_material(lib_init):
    m = openmc.lib.materials[3]
    assert m.nuclides == ['H1', 'O16', 'B10', 'B11']

    old_dens = m.densities
    test_dens = [1.0e-1, 2.0e-1, 2.5e-1, 1.0e-3]
    m.set_densities(m.nuclides, test_dens)
    assert m.densities == pytest.approx(test_dens)

    assert m.volume is None
    m.volume = 10.0
    assert m.volume == 10.0

    with pytest.raises(exc.OpenMCError):
        m.set_density(1.0, 'goblins')

    rho = 2.25e-2
    m.set_density(rho)
    assert sum(m.densities) == pytest.approx(rho)

    m.set_density(0.1, 'g/cm3')
    assert m.get_density('g/cm3') == pytest.approx(0.1)
    assert m.name == "Hot borated water"
    m.name = "Not hot borated water"
    assert m.name == "Not hot borated water"

    assert m.depletable == False
    m.depletable = True
    assert m.depletable == True


def test_properties_density(lib_init):
    m = openmc.lib.materials[1]
    orig_density = m.get_density('atom/b-cm')
    orig_density_gpcc = m.get_density('g/cm3')

    # Export properties and change density
    openmc.lib.export_properties('properties.h5')
    m.set_density(orig_density_gpcc*2, 'g/cm3')
    assert m.get_density() == pytest.approx(orig_density*2)

    # Import properties and check that density was restored
    openmc.lib.import_properties('properties.h5')
    assert m.get_density() == pytest.approx(orig_density)

    with pytest.raises(ValueError):
        m.get_density('🥏')


def test_material_add_nuclide(lib_init):
    m = openmc.lib.materials[3]
    m.add_nuclide('Xe135', 1e-12)
    assert m.nuclides[-1] == 'Xe135'
    assert m.densities[-1] == 1e-12


def test_new_material(lib_init):
    with pytest.raises(exc.AllocationError):
        openmc.lib.Material(1)
    new_mat = openmc.lib.Material()
    new_mat_with_id = openmc.lib.Material(10)
    assert len(openmc.lib.materials) == 5


def test_properties_fail_material(lib_init):
    # The number of materials was changed in the previous test, so the properties
    # file is no longer valid
    with pytest.raises(exc.GeometryError, match="Number of materials"):
        openmc.lib.import_properties("properties.h5")


def test_nuclide_mapping(lib_init):
    nucs = openmc.lib.nuclides
    assert isinstance(nucs, Mapping)
    assert len(nucs) == 13
    for name, nuc in nucs.items():
        assert isinstance(nuc, openmc.lib.Nuclide)
        assert name == nuc.name


def test_settings(lib_init):
    settings = openmc.lib.settings
    assert settings.inactive == 5
    assert settings.generations_per_batch == 1
    assert settings.particles == 100
    assert settings.seed == 1
    assert settings.event_based is False
    settings.seed = 11


def test_tally_mapping(lib_init):
    tallies = openmc.lib.tallies
    assert isinstance(tallies, Mapping)
    assert len(tallies) == 3
    for tally_id, tally in tallies.items():
        assert isinstance(tally, openmc.lib.Tally)
        assert tally_id == tally.id


def test_energy_function_filter(lib_init):
    """Test special __new__ and __init__ for EnergyFunctionFilter"""
    efunc = openmc.lib.EnergyFunctionFilter([0.0, 1.0], [0.0, 2.0])
    assert len(efunc.energy) == 2
    assert (efunc.energy == [0.0, 1.0]).all()
    assert len(efunc.y) == 2
    assert (efunc.y == [0.0, 2.0]).all()

    # Default should be lin-lin
    assert efunc.interpolation == 'linear-linear'
    efunc.interpolation = 'histogram'
    assert efunc.interpolation == 'histogram'


def test_tally(lib_init):
    t = openmc.lib.tallies[1]
    assert t.type == 'volume'
    assert len(t.filters) == 2
    assert isinstance(t.filters[0], openmc.lib.MaterialFilter)
    assert isinstance(t.filters[1], openmc.lib.EnergyFilter)

    # Create new filter and replace existing
    with pytest.raises(exc.AllocationError):
        openmc.lib.MaterialFilter(uid=1)
    mats = openmc.lib.materials
    f = openmc.lib.MaterialFilter([mats[2], mats[1]])
    assert f.bins[0] == mats[2]
    assert f.bins[1] == mats[1]
    t.filters = [f]
    assert t.filters == [f]

    assert t.nuclides == ['U235', 'U238']
    with pytest.raises(exc.DataError):
        t.nuclides = ['Zr2']
    t.nuclides = ['U234', 'Zr90']
    assert t.nuclides == ['U234', 'Zr90']

    assert t.scores == ['total', '(n,elastic)', '(n,gamma)']
    new_scores = ['scatter', 'fission', 'nu-fission', '(n,2n)']
    t.scores = new_scores
    assert t.scores == new_scores

    t2 = openmc.lib.tallies[2]
    assert len(t2.filters) == 2
    assert isinstance(t2.filters[0], openmc.lib.ZernikeFilter)
    assert isinstance(t2.filters[1], openmc.lib.CellFilter)
    assert len(t2.filters[1].bins) == 3
    assert t2.filters[0].order == 5

    t3 = openmc.lib.tallies[3]
    assert len(t3.filters) == 1
    t3_f = t3.filters[0]
    assert isinstance(t3_f, openmc.lib.EnergyFunctionFilter)
    assert len(t3_f.energy) == 2
    assert len(t3_f.y) == 2
    t3_f.set_data([0.0, 1.0, 2.0], [0.0, 1.0, 4.0])
    assert len(t3_f.energy) == 3
    assert len(t3_f.y) == 3


def test_new_tally(lib_init):
    with pytest.raises(exc.AllocationError):
        openmc.lib.Material(1)
    new_tally = openmc.lib.Tally()
    new_tally.scores = ['flux']
    new_tally_with_id = openmc.lib.Tally(10)
    new_tally_with_id.scores = ['flux']
    assert len(openmc.lib.tallies) == 5


def test_delete_tally(lib_init):
    # delete tally 10 which was added in the above test
    # check length is one less than before
    del openmc.lib.tallies[10]
    assert len(openmc.lib.tallies) == 4


def test_invalid_tally_id(lib_init):
    # attempt to access a tally that is guaranteed not to have a valid index
    max_id = max(openmc.lib.tallies.keys())
    with pytest.raises(KeyError):
        openmc.lib.tallies[max_id+1]


def test_tally_activate(lib_simulation_init):
    t = openmc.lib.tallies[1]
    assert not t.active
    t.active = True
    assert t.active


def test_tally_multiply_density(lib_simulation_init):
    # multiply_density is True by default
    t = openmc.lib.tallies[1]
    assert t.multiply_density

    # Make sure setting multiply_density works
    t.multiply_density = False
    assert not t.multiply_density

    # Reset to True
    t.multiply_density = True


def test_tally_writable(lib_simulation_init):
    t = openmc.lib.tallies[1]
    assert t.writable
    t.writable = False
    assert not t.writable
    # Revert tally to writable state for lib_run fixtures
    t.writable = True


def test_tally_results(lib_run):
    t = openmc.lib.tallies[1]
    assert t.num_realizations == 10  # t was made active in test_tally_active
    assert np.all(t.mean >= 0)
    nonzero = (t.mean > 0.0)
    assert np.all(t.std_dev[nonzero] >= 0)
    assert np.all(t.ci_width()[nonzero] >= 1.95*t.std_dev[nonzero])

    t2 = openmc.lib.tallies[2]
    n = 5
    assert t2.mean.size == (n + 1) * (n + 2) // 2 * 3 # Number of Zernike coeffs * 3 cells


def test_global_tallies(lib_run):
    assert openmc.lib.num_realizations() == 5
    gt = openmc.lib.global_tallies()
    for mean, std_dev in gt:
        assert mean >= 0


def test_statepoint(lib_run):
    openmc.lib.statepoint_write('test_sp.h5')
    assert os.path.exists('test_sp.h5')


def test_source_bank(lib_run):
    source = openmc.lib.source_bank()
    assert np.all(source['E'] > 0.0)
    assert np.all(source['wgt'] == 1.0)
    assert np.allclose(np.linalg.norm(source['u'], axis=1), 1.0)


def test_by_batch(lib_run):
    openmc.lib.hard_reset()

    # Running next batch before simulation is initialized should raise an
    # exception
    with pytest.raises(exc.AllocationError):
        openmc.lib.next_batch()

    openmc.lib.simulation_init()
    try:
        for _ in openmc.lib.iter_batches():
            # Make sure we can get k-effective during inactive/active batches
            mean, std_dev = openmc.lib.keff()
            assert 0.0 < mean < 2.5
            assert std_dev > 0.0
        assert openmc.lib.num_realizations() == 5

        for i in range(3):
            openmc.lib.next_batch()
        assert openmc.lib.num_realizations() == 8

    finally:
        openmc.lib.simulation_finalize()


def test_set_n_batches(lib_run):
    # Run simulation_init so that current_batch reset to 0
    openmc.lib.hard_reset()
    openmc.lib.simulation_init()

    settings = openmc.lib.settings
    assert settings.get_batches() == 10

    # Setting n_batches less than n_inactive should raise error
    with pytest.raises(exc.InvalidArgumentError):
        settings.set_batches(3)
    # n_batches should stay the same
    assert settings.get_batches() == 10

    for i in range(7):
        openmc.lib.next_batch()
    # Setting n_batches less than current_batch should raise error
    with pytest.raises(exc.InvalidArgumentError):
        settings.set_batches(6)
    # n_batches should stay the same
    assert settings.get_batches() == 10

    # Change n_batches from 10 to 20
    settings.set_batches(20)
    for _ in openmc.lib.iter_batches():
        pass
    openmc.lib.simulation_finalize()

    # n_active should have been overwritten from 5 to 15
    assert openmc.lib.num_realizations() == 15

    # Ensure statepoint created at new value of n_batches
    assert os.path.exists('statepoint.20.h5')


def test_reset(lib_run):
    # Init and run 10 batches.
    openmc.lib.hard_reset()
    openmc.lib.simulation_init()
    try:
        for i in range(20):
            openmc.lib.next_batch()

        # Make sure there are 15 realizations for the 15 active batches.
        assert openmc.lib.num_realizations() == 15
        assert openmc.lib.tallies[2].num_realizations == 15
        _, keff_sd1 = openmc.lib.keff()
        tally_sd1 = openmc.lib.tallies[2].std_dev[0]

        # Reset and run 3 more batches.  Check the number of realizations.
        openmc.lib.reset()
        for i in range(3):
            openmc.lib.next_batch()
        assert openmc.lib.num_realizations() == 3
        assert openmc.lib.tallies[2].num_realizations == 3

        # Check the tally std devs to make sure results were cleared.
        _, keff_sd2 = openmc.lib.keff()
        tally_sd2 = openmc.lib.tallies[2].std_dev[0]
        assert keff_sd2 > keff_sd1
        assert tally_sd2 > tally_sd1

    finally:
        openmc.lib.simulation_finalize()


def test_reproduce_keff(lib_init):
    # Get k-effective after run
    openmc.lib.hard_reset()
    openmc.lib.run()
    keff0 = openmc.lib.keff()

    # Reset, run again, and get k-effective again. they should match
    openmc.lib.hard_reset()
    openmc.lib.run()
    keff1 = openmc.lib.keff()
    assert keff0 == pytest.approx(keff1)


def test_find_cell(lib_init):
    cell, instance = openmc.lib.find_cell((0., 0., 0.))
    assert cell is openmc.lib.cells[1]
    cell, instance = openmc.lib.find_cell((0.4, 0., 0.))
    assert cell is openmc.lib.cells[2]
    with pytest.raises(exc.GeometryError):
        openmc.lib.find_cell((100., 100., 100.))


def test_find_material(lib_init):
    mat = openmc.lib.find_material((0., 0., 0.))
    assert mat is openmc.lib.materials[1]
    mat = openmc.lib.find_material((0.4, 0., 0.))
    assert mat is openmc.lib.materials[2]


def test_regular_mesh(lib_init):
    mesh = openmc.lib.RegularMesh()
    mesh.dimension = (2, 3, 4)
    assert mesh.dimension == (2, 3, 4)
    with pytest.raises(exc.AllocationError):
        mesh2 = openmc.lib.RegularMesh(mesh.id)

    # Make sure each combination of parameters works
    ll = (0., 0., 0.)
    ur = (10., 10., 10.)
    width = (1., 1., 1.)
    mesh.set_parameters(lower_left=ll, upper_right=ur)
    assert mesh.lower_left == pytest.approx(ll)
    assert mesh.upper_right == pytest.approx(ur)
    mesh.set_parameters(lower_left=ll, width=width)
    assert mesh.lower_left == pytest.approx(ll)
    assert mesh.width == pytest.approx(width)
    mesh.set_parameters(upper_right=ur, width=width)
    assert mesh.upper_right == pytest.approx(ur)
    assert mesh.width == pytest.approx(width)

    np.testing.assert_allclose(mesh.volumes, 1.0)

    # bounding box
    mesh.set_parameters(lower_left=ll, upper_right=ur)
    bbox = mesh.bounding_box
    np.testing.assert_allclose(bbox.lower_left, ll)
    np.testing.assert_allclose(bbox.upper_right, ur)

    meshes = openmc.lib.meshes
    assert isinstance(meshes, Mapping)
    assert len(meshes) == 1
    for mesh_id, mesh in meshes.items():
        assert isinstance(mesh, openmc.lib.RegularMesh)
        assert mesh_id == mesh.id

    translation = (1.0, 2.0, 3.0)

    mf = openmc.lib.MeshFilter(mesh)
    assert mf.mesh == mesh
    mf.translation = translation
    assert mf.translation == translation

    msf = openmc.lib.MeshSurfaceFilter(mesh)
    assert msf.mesh == mesh
    msf.translation = translation
    assert msf.translation == translation

    # Test material volumes
    mesh = openmc.lib.RegularMesh()
    mesh.dimension = (2, 2, 1)
    mesh.set_parameters(lower_left=(-0.63, -0.63, -0.5),
                        upper_right=(0.63, 0.63, 0.5))
    vols = mesh.material_volumes()
    assert vols.num_elements == 4
    for i in range(vols.num_elements):
        elem_vols = vols.by_element(i)
        assert sum(f[1] for f in elem_vols) == pytest.approx(1.26 * 1.26 / 4)

    # If the mesh extends beyond the boundaries of the model, we should get a
    # GeometryError
    mesh.dimension = (1, 1, 1)
    mesh.set_parameters(lower_left=(-1.0, -1.0, -0.5),
                        upper_right=(1.0, 1.0, 0.5))
    with pytest.raises(exc.GeometryError, match="not fully contained"):
        vols = mesh.material_volumes()


def test_regular_mesh_get_plot_bins(lib_init):
    mesh: openmc.lib.RegularMesh = openmc.lib.meshes[2]
    mesh.dimension = (2, 2, 1)
    mesh.set_parameters(lower_left=(-1.0, -1.0, -0.5),
                        upper_right=(1.0, 1.0, 0.5))

    # Get bins for a plot view covering only a single mesh bin
    mesh_bins = mesh.get_plot_bins((-0.5, -0.5, 0.), (0.1, 0.1), 'xy', (20, 20))
    assert (mesh_bins == 0).all()
    mesh_bins = mesh.get_plot_bins((0.5, 0.5, 0.), (0.1, 0.1), 'xy', (20, 20))
    assert (mesh_bins == 3).all()

    # Get bins for a plot view covering all mesh bins. Note that the y direction
    # (first dimension) is flipped for plotting purposes
    mesh_bins = mesh.get_plot_bins((0., 0., 0.), (2., 2.), 'xy', (20, 20))
    assert (mesh_bins[:10, :10] == 2).all()
    assert (mesh_bins[:10, 10:] == 3).all()
    assert (mesh_bins[10:, :10] == 0).all()
    assert (mesh_bins[10:, 10:] == 1).all()

    # Get bins for a plot view outside of the mesh
    mesh_bins = mesh.get_plot_bins((100., 100., 0.), (2., 2.), 'xy', (20, 20))
    assert (mesh_bins == -1).all()


def test_rectilinear_mesh(lib_init):
    mesh = openmc.lib.RectilinearMesh()
    x_grid = [-10., 0., 10.]
    y_grid = [0., 10., 20.]
    z_grid = [10., 20., 30.]
    mesh.set_grid(x_grid, y_grid, z_grid)
    assert np.all(mesh.lower_left == (-10., 0., 10.))
    assert np.all(mesh.upper_right == (10., 20., 30.))
    assert np.all(mesh.dimension == (2, 2, 2))
    for i, diff_x in enumerate(np.diff(x_grid)):
        for j, diff_y in enumerate(np.diff(y_grid)):
            for k, diff_z in enumerate(np.diff(z_grid)):
                assert np.all(mesh.width[i, j, k, :] == (10, 10, 10))

    np.testing.assert_allclose(mesh.volumes, 1000.0)

    # bounding box
    bbox = mesh.bounding_box
    np.testing.assert_allclose(bbox.lower_left, (-10., 0., 10.))
    np.testing.assert_allclose(bbox.upper_right, (10., 20., 30.))

    with pytest.raises(exc.AllocationError):
        mesh2 = openmc.lib.RectilinearMesh(mesh.id)

    meshes = openmc.lib.meshes
    assert isinstance(meshes, Mapping)
    assert len(meshes) == 3

    mesh = meshes[mesh.id]
    assert isinstance(mesh, openmc.lib.RectilinearMesh)

    mf = openmc.lib.MeshFilter(mesh)
    assert mf.mesh == mesh

    msf = openmc.lib.MeshSurfaceFilter(mesh)
    assert msf.mesh == mesh

    # Test material volumes
    mesh = openmc.lib.RectilinearMesh()
    w = 1.26
    mesh.set_grid([-w/2, -w/4, w/2], [-w/2, -w/4, w/2], [-0.5, 0.5])

    vols = mesh.material_volumes()
    assert vols.num_elements == 4
    assert sum(f[1] for f in vols.by_element(0)) == pytest.approx(w/4 * w/4)
    assert sum(f[1] for f in vols.by_element(1)) == pytest.approx(w/4 * 3*w/4)
    assert sum(f[1] for f in vols.by_element(2)) == pytest.approx(3*w/4 * w/4)
    assert sum(f[1] for f in vols.by_element(3)) == pytest.approx(3*w/4 * 3*w/4)


def test_cylindrical_mesh(lib_init):
    deg2rad = lambda deg: deg*pi/180
    mesh = openmc.lib.CylindricalMesh()
    r_grid = [0., 5., 10.]
    phi_grid = np.radians([0., 10., 20.])
    z_grid = [10., 20., 30.]
    mesh.set_grid(r_grid, phi_grid, z_grid)
    assert np.all(mesh.lower_left == (0., 0., 10.))
    assert np.all(mesh.upper_right == (10., deg2rad(20.), 30.))
    assert np.all(mesh.dimension == (2, 2, 2))
    for i, _ in enumerate(np.diff(r_grid)):
        for j, _ in enumerate(np.diff(phi_grid)):
            for k, _ in enumerate(np.diff(z_grid)):
                assert np.allclose(mesh.width[i, j, k, :], (5, deg2rad(10), 10))

    np.testing.assert_allclose(mesh.volumes[::2], 10/360 * pi * 5**2 * 10)
    np.testing.assert_allclose(mesh.volumes[1::2], 10/360 * pi * (10**2 - 5**2) * 10)

    # bounding box
    bbox = mesh.bounding_box
    np.testing.assert_allclose(bbox.lower_left, (-10., -10., 10.))
    np.testing.assert_allclose(bbox.upper_right, (10., 10., 30.))

    with pytest.raises(exc.AllocationError):
        mesh2 = openmc.lib.CylindricalMesh(mesh.id)

    meshes = openmc.lib.meshes
    assert isinstance(meshes, Mapping)
    assert len(meshes) == 5

    mesh = meshes[mesh.id]
    assert isinstance(mesh, openmc.lib.CylindricalMesh)

    mf = openmc.lib.MeshFilter(mesh)
    assert mf.mesh == mesh

    msf = openmc.lib.MeshSurfaceFilter(mesh)
    assert msf.mesh == mesh

    # Test material volumes
    mesh = openmc.lib.CylindricalMesh()
    r_grid = (0., 0.25, 0.5)
    phi_grid = np.linspace(0., 2.0*pi, 4)
    z_grid = (-0.5, 0.5)
    mesh.set_grid(r_grid, phi_grid, z_grid)

    vols = mesh.material_volumes()
    assert vols.num_elements == 6
    for i in range(0, 6, 2):
        assert sum(f[1] for f in vols.by_element(i)) == pytest.approx(pi * 0.25**2 / 3)
    for i in range(1, 6, 2):
        assert sum(f[1] for f in vols.by_element(i)) == pytest.approx(pi * (0.5**2 - 0.25**2) / 3)


def test_spherical_mesh(lib_init):
    deg2rad = lambda deg: deg*np.pi/180
    mesh = openmc.lib.SphericalMesh()
    r_grid = [0., 5., 10.]
    theta_grid = np.radians([0., 10., 20.])
    phi_grid = np.radians([10., 20., 30.])
    mesh.set_grid(r_grid, theta_grid, phi_grid)
    assert np.all(mesh.lower_left == (0., 0., deg2rad(10.)))
    assert np.all(mesh.upper_right == (10., deg2rad(20.), deg2rad(30.)))
    assert np.all(mesh.dimension == (2, 2, 2))
    for i, _ in enumerate(np.diff(r_grid)):
        for j, _ in enumerate(np.diff(theta_grid)):
            for k, _ in enumerate(np.diff(phi_grid)):
                assert np.allclose(mesh.width[i, j, k, :], (5, deg2rad(10), deg2rad(10)))

    dtheta = lambda d1, d2: np.cos(deg2rad(d1)) - np.cos(deg2rad(d2))
    f = 1/3 * deg2rad(10.)
    np.testing.assert_allclose(mesh.volumes[::4],  f * 5**3 * dtheta(0., 10.))
    np.testing.assert_allclose(mesh.volumes[1::4], f * (10**3 - 5**3) * dtheta(0., 10.))
    np.testing.assert_allclose(mesh.volumes[2::4], f * 5**3 * dtheta(10., 20.))
    np.testing.assert_allclose(mesh.volumes[3::4], f * (10**3 - 5**3) * dtheta(10., 20.))

    # bounding box
    bbox = mesh.bounding_box
    np.testing.assert_allclose(bbox.lower_left, (-10., -10., -10.))
    np.testing.assert_allclose(bbox.upper_right, (10., 10., 10.))

    with pytest.raises(exc.AllocationError):
        mesh2 = openmc.lib.SphericalMesh(mesh.id)

    meshes = openmc.lib.meshes
    assert isinstance(meshes, Mapping)
    assert len(meshes) == 7

    mesh = meshes[mesh.id]
    assert isinstance(mesh, openmc.lib.SphericalMesh)

    mf = openmc.lib.MeshFilter(mesh)
    assert mf.mesh == mesh

    msf = openmc.lib.MeshSurfaceFilter(mesh)
    assert msf.mesh == mesh

    # Test material volumes
    mesh = openmc.lib.SphericalMesh()
    r_grid = (0., 0.25, 0.5)
    theta_grid = np.linspace(0., pi, 3)
    phi_grid = np.linspace(0., 2.0*pi, 4)
    mesh.set_grid(r_grid, theta_grid, phi_grid)

    vols = mesh.material_volumes()
    assert vols.num_elements == 12
    d_theta = theta_grid[1] - theta_grid[0]
    d_phi = phi_grid[1] - phi_grid[0]
    for i in range(0, 12, 2):
        assert sum(f[1] for f in vols.by_element(i)) == pytest.approx(
            0.25**3 / 3 * d_theta * d_phi * 2/pi)
    for i in range(1, 12, 2):
        assert sum(f[1] for f in vols.by_element(i)) == pytest.approx(
            (0.5**3 - 0.25**3) / 3 * d_theta * d_phi * 2/pi)


def test_restart(lib_init, mpi_intracomm):
    # Finalize and re-init to make internal state consistent with XML.
    openmc.lib.hard_reset()
    openmc.lib.finalize()
    openmc.lib.init(intracomm=mpi_intracomm)
    openmc.lib.simulation_init()

    # Run for 7 batches then write a statepoint.
    for i in range(7):
        openmc.lib.next_batch()
    openmc.lib.statepoint_write('restart_test.h5', True)

    # Run 3 more batches and copy the keff.
    for i in range(3):
        openmc.lib.next_batch()
    keff0 = openmc.lib.keff()

    # Restart the simulation from the statepoint and the 3 remaining active batches.
    openmc.lib.simulation_finalize()
    openmc.lib.hard_reset()
    openmc.lib.finalize()
    openmc.lib.init(args=('-r', 'restart_test.h5'))
    openmc.lib.simulation_init()
    for i in range(3):
        openmc.lib.next_batch()
    keff1 = openmc.lib.keff()
    openmc.lib.simulation_finalize()

    # Compare the keff values.
    assert keff0 == pytest.approx(keff1)


def test_load_nuclide(lib_init):
    # load multiple nuclides
    openmc.lib.load_nuclide('H3')
    assert 'H3' in openmc.lib.nuclides
    openmc.lib.load_nuclide('Pu239')
    assert 'Pu239' in openmc.lib.nuclides
    # load non-existent nuclide
    with pytest.raises(exc.DataError):
        openmc.lib.load_nuclide('Pu3')


def test_id_map(lib_init):
    expected_ids = np.array([[(3, 0, 3), (2, 0, 2), (3, 0, 3)],
                             [(2, 0, 2), (1, 0, 1), (2, 0, 2)],
                             [(3, 0, 3), (2, 0, 2), (3, 0, 3)]], dtype='int32')

    # create a plot object
    s = openmc.lib.plot._PlotBase()
    s.width = 1.26
    s.height = 1.26
    s.v_res = 3
    s.h_res = 3
    s.origin = (0.0, 0.0, 0.0)
    s.basis = 'xy'
    s.level = -1

    ids = openmc.lib.plot.id_map(s)
    assert np.array_equal(expected_ids, ids)


def test_property_map(lib_init):
    expected_properties = np.array(
        [[(293.6, 0.740582), (293.6, 6.55), (293.6, 0.740582)],
         [ (293.6, 6.55), (293.6, 10.29769),  (293.6, 6.55)],
         [(293.6, 0.740582), (293.6, 6.55), (293.6, 0.740582)]], dtype='float')

    # create a plot object
    s = openmc.lib.plot._PlotBase()
    s.width = 1.26
    s.height = 1.26
    s.v_res = 3
    s.h_res = 3
    s.origin = (0.0, 0.0, 0.0)
    s.basis = 'xy'
    s.level = -1

    properties = openmc.lib.plot.property_map(s)
    assert np.allclose(expected_properties, properties, atol=1e-04)


def test_position(lib_init):

    pos = openmc.lib.plot._Position(1.0, 2.0, 3.0)

    assert tuple(pos) == (1.0, 2.0, 3.0)

    pos[0] = 1.3
    pos[1] = 2.3
    pos[2] = 3.3

    assert tuple(pos) == (1.3, 2.3, 3.3)


def test_global_bounding_box(lib_init):
    expected_llc = (-0.63, -0.63, -np.inf)
    expected_urc = (0.63, 0.63, np.inf)

    llc, urc = openmc.lib.global_bounding_box()

    assert tuple(llc) == expected_llc
    assert tuple(urc) == expected_urc


def test_trigger_set_n_batches(uo2_trigger_model, mpi_intracomm):
    openmc.lib.finalize()
    openmc.lib.init(intracomm=mpi_intracomm)
    openmc.lib.simulation_init()

    settings = openmc.lib.settings
    # Change n_batches to 12 and n_max_batches to 20
    settings.set_batches(12, set_max_batches=False, add_sp_batch=False)
    settings.set_batches(20, set_max_batches=True, add_sp_batch=True)

    assert settings.get_batches(get_max_batches=False) == 12
    assert settings.get_batches(get_max_batches=True) == 20

    for _ in openmc.lib.iter_batches():
        pass
    openmc.lib.simulation_finalize()

    # n_active should have been overwritten from 5 to 15
    assert openmc.lib.num_realizations() == 15

    # Ensure statepoint was created only at batch 20 when calling set_batches
    assert not os.path.exists('statepoint.12.h5')
    assert os.path.exists('statepoint.20.h5')


def test_cell_translation(pincell_model_w_univ, mpi_intracomm):
    openmc.lib.finalize()
    openmc.lib.init(intracomm=mpi_intracomm)
    # Cell 1 is filled with a material so it has a translation, but we can't
    # set it.
    cell = openmc.lib.cells[1]
    assert cell.translation == pytest.approx([0., 0., 0.])
    with pytest.raises(exc.GeometryError, match='not filled with'):
        cell.translation = (1., 0., -1.)

    # Cell 2 was given a universe, so we can assign it a translation vector
    cell = openmc.lib.cells[2]
    assert cell.translation == pytest.approx([0., 0., 0.])
    # This time we *can* set it
    cell.translation = (1., 0., -1.)
    assert cell.translation == pytest.approx([1., 0., -1.])
    openmc.lib.finalize()


def test_cell_rotation(pincell_model_w_univ, mpi_intracomm):
    openmc.lib.finalize()
    openmc.lib.init(intracomm=mpi_intracomm)
    # Cell 1 is filled with a material so we cannot rotate it, but we can get
    # its rotation matrix (which will be the identity matrix)
    cell = openmc.lib.cells[1]
    assert cell.rotation == pytest.approx([0., 0., 0.])
    with pytest.raises(exc.GeometryError, match='not filled with'):
        cell.rotation = (180., 0., 0.)

    # Now repeat with Cell 2 and we will be allowed to do it
    cell = openmc.lib.cells[2]
    assert cell.rotation == pytest.approx([0., 0., 0.])
    cell.rotation = (180., 0., 0.)
    assert cell.rotation == pytest.approx([180., 0., 0.])
    openmc.lib.finalize()


def test_sample_external_source(run_in_tmpdir, mpi_intracomm):
    # Define a simple model and export
    mat = openmc.Material()
    mat.add_nuclide('U235', 1.0e-2)
    sph = openmc.Sphere(r=100.0, boundary_type='vacuum')
    cell = openmc.Cell(fill=mat, region=-sph)
    model = openmc.Model()
    model.geometry = openmc.Geometry([cell])
    model.settings.source = openmc.IndependentSource(
        space=openmc.stats.Box([-5., -5., -5.], [5., 5., 5.]),
        angle=openmc.stats.Monodirectional((0., 0., 1.)),
        energy=openmc.stats.Discrete([1.0e5], [1.0]),
        constraints={'fissionable': True}
    )
    model.settings.particles = 1000
    model.settings.batches = 10
    model.export_to_xml()

    # Sample some particles and make sure they match specified source
    openmc.lib.init()
    particles = openmc.lib.sample_external_source(10, prn_seed=3)
    assert len(particles) == 10
    for p in particles:
        assert -5. < p.r[0] < 5.
        assert -5. < p.r[1] < 5.
        assert -5. < p.r[2] < 5.
        assert p.u[0] == 0.0
        assert p.u[1] == 0.0
        assert p.u[2] == 1.0
        assert p.E == 1.0e5

    # Using the same seed should produce the same particles
    other_particles = openmc.lib.sample_external_source(10, prn_seed=3)
    assert len(other_particles) == 10
    for p1, p2 in zip(particles, other_particles):
        assert p1.r == p2.r
        assert p1.u == p2.u
        assert p1.E == p2.E
        assert p1.time == p2.time
        assert p1.wgt == p2.wgt

    openmc.lib.finalize()

    # Make sure sampling works in volume calculation mode
    openmc.lib.init(["-c"])
    openmc.lib.sample_external_source(100)
    openmc.lib.finalize()
