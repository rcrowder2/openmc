from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import Iterable
from numbers import Real

import numpy as np

import openmc
import openmc.checkvalue as cv
from .mixin import IDManagerMixin
from .plots import add_plot_params


class UniverseBase(ABC, IDManagerMixin):
    """A collection of cells that can be repeated.

    Attributes
    ----------
    id : int
        Unique identifier of the universe
    name : str
        Name of the universe
    """

    next_id = 1
    used_ids = set()

    def __init__(self, universe_id=None, name=''):
        # Initialize Universe class attributes
        self.id = universe_id
        self.name = name
        self._volume = None
        self._atoms = {}

        # Keys   - Cell IDs
        # Values - Cells
        self._cells = {}

    def __repr__(self):
        string = 'Universe\n'
        string += '{: <16}=\t{}\n'.format('\tID', self._id)
        string += '{: <16}=\t{}\n'.format('\tName', self._name)
        return string

    @property
    def name(self):
        return self._name

    @property
    def cells(self):
        return self._cells

    @name.setter
    def name(self, name):
        if name is not None:
            cv.check_type('universe name', name, str)
            self._name = name
        else:
            self._name = ''

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, volume):
        if volume is not None:
            cv.check_type('universe volume', volume, Real)
        self._volume = volume

    def add_volume_information(self, volume_calc):
        """Add volume information to a universe.

        Parameters
        ----------
        volume_calc : openmc.VolumeCalculation
            Results from a stochastic volume calculation

        """
        if volume_calc.domain_type == 'universe':
            if self.id in volume_calc.volumes:
                self._volume = volume_calc.volumes[self.id].n
                self._atoms = volume_calc.atoms[self.id]
            else:
                raise ValueError(
                    'No volume information found for this universe.')
        else:
            raise ValueError('No volume information found for this universe.')

    def get_all_universes(self, memo=None):
        """Return all universes that are contained within this one.

        Returns
        -------
        universes : dict
            Dictionary whose keys are universe IDs and values are
            :class:`Universe` instances

        """
        if memo is None:
            memo = set()
        elif self in memo:
            return {}
        memo.add(self)

        # Append all Universes within each Cell to the dictionary
        universes = {}
        for cell in self.get_all_cells().values():
            universes.update(cell.get_all_universes(memo))

        return universes

    @abstractmethod
    def create_xml_subelement(self, xml_element, memo=None):
        """Add the universe xml representation to an incoming xml element

        Parameters
        ----------
        xml_element : lxml.etree._Element
            XML element to be added to

        memo : set or None
            A set of object id's representing geometry entities already
            written to the xml_element. This parameter is used internally
            and should not be specified by users.

        Returns
        -------
        None

        """

    def _determine_paths(self, path='', instances_only=False):
        """Count the number of instances for each cell in the universe, and
        record the count in the :attr:`Cell.num_instances` properties."""

        univ_path = path + f'u{self.id}'

        for cell in self.cells.values():
            cell_path = f'{univ_path}->c{cell.id}'
            fill = cell._fill
            fill_type = cell.fill_type

            # If universe-filled, recursively count cells in filling universe
            if fill_type == 'universe':
                fill._determine_paths(cell_path + '->', instances_only)
            # If lattice-filled, recursively call for all universes in lattice
            elif fill_type == 'lattice':
                latt = fill

                # Count instances in each universe in the lattice
                for index in latt._natural_indices:
                    latt_path = '{}->l{}({})->'.format(
                        cell_path, latt.id, ",".join(str(x) for x in index))
                    univ = latt.get_universe(index)
                    univ._determine_paths(latt_path, instances_only)

            else:
                if fill_type == 'material':
                    mat = fill
                elif fill_type == 'distribmat':
                    mat = fill[cell._num_instances]
                else:
                    mat = None

                if mat is not None:
                    mat._num_instances += 1
                    if not instances_only:
                        mat._paths.append(f'{cell_path}->m{mat.id}')

            # Append current path
            cell._num_instances += 1
            if not instances_only:
                cell._paths.append(cell_path)

    def add_cells(self, cells):
        """Add multiple cells to the universe.

        Parameters
        ----------
        cells : Iterable of openmc.Cell
            Cells to add

        """

        if not isinstance(cells, Iterable):
            msg = f'Unable to add Cells to Universe ID="{self._id}" since ' \
                  f'"{cells}" is not iterable'
            raise TypeError(msg)

        for cell in cells:
            self.add_cell(cell)

    @abstractmethod
    def add_cell(self, cell):
        pass

    @abstractmethod
    def remove_cell(self, cell):
        pass

    def clear_cells(self):
        """Remove all cells from the universe."""

        self._cells.clear()

    def get_all_cells(self, memo=None):
        """Return all cells that are contained within the universe

        Returns
        -------
        cells : dict
            Dictionary whose keys are cell IDs and values are :class:`Cell`
            instances

        """

        if memo is None:
            memo = set()
        elif self in memo:
            return {}
        memo.add(self)

        # Add this Universe's cells to the dictionary
        cells = {}
        cells.update(self._cells)

        # Append all Cells in each Cell in the Universe to the dictionary
        for cell in self._cells.values():
            cells.update(cell.get_all_cells(memo))

        return cells

    def get_all_materials(self, memo=None):
        """Return all materials that are contained within the universe

        Returns
        -------
        materials : dict
            Dictionary whose keys are material IDs and values are
            :class:`Material` instances

        """

        if memo is None:
            memo = set()

        materials = {}

        # Append all Cells in each Cell in the Universe to the dictionary
        cells = self.get_all_cells(memo)
        for cell in cells.values():
            materials.update(cell.get_all_materials(memo))

        return materials

    @abstractmethod
    def _partial_deepcopy(self):
        """Deepcopy all parameters of an openmc.UniverseBase object except its cells.
        This should only be used from the openmc.UniverseBase.clone() context.

        """

    def clone(self, clone_materials=True, clone_regions=True, memo=None):
        """Create a copy of this universe with a new unique ID, and clones
        all cells within this universe.

        Parameters
        ----------
        clone_materials : bool
            Whether to create separates copies of the materials filling cells
            contained in this universe.
        clone_regions : bool
            Whether to create separates copies of the regions bounding cells
            contained in this universe.
        memo : dict or None
            A nested dictionary of previously cloned objects. This parameter
            is used internally and should not be specified by the user.

        Returns
        -------
        clone : openmc.Universe
            The clone of this universe

        """
        if memo is None:
            memo = {}

        # If no memoize'd clone exists, instantiate one
        if self not in memo:
            clone = self._partial_deepcopy()

            # Clone all cells for the universe clone
            clone._cells = {}
            for cell in self._cells.values():
                clone.add_cell(cell.clone(clone_materials, clone_regions,
                                          memo))

            # Memoize the clone
            memo[self] = clone

        return memo[self]

    def find(self, point):
        """Find cells/universes/lattices which contain a given point

        Parameters
        ----------
        point : 3-tuple of float
            Cartesian coordinates of the point

        Returns
        -------
        list
            Sequence of universes, cells, and lattices which are traversed to
            find the given point

        """
        p = np.asarray(point)
        for cell in self._cells.values():
            if p in cell:
                if cell.fill_type in ('material', 'distribmat', 'void'):
                    return [self, cell]
                elif cell.fill_type == 'universe':
                    if cell.translation is not None:
                        p -= cell.translation
                    if cell.rotation is not None:
                        p[:] = cell.rotation_matrix.dot(p)
                    return [self, cell] + cell.fill.find(p)
                else:
                    return [self, cell] + cell.fill.find(p)
        return []

    @add_plot_params
    def plot(self, *args, **kwargs):
        """Display a slice plot of the universe.
        """
        model = openmc.Model()
        model.geometry = openmc.Geometry(self)

        # Determine whether any materials contains macroscopic data and if
        # so, set energy mode accordingly
        for mat in self.get_all_materials().values():
            if mat._macroscopic is not None:
                model.settings.energy_mode = 'multi-group'
                break

        return model.plot(*args, **kwargs)

    def get_nuclides(self):
        """Returns all nuclides in the universe

        Returns
        -------
        nuclides : list of str
            List of nuclide names

        """

        nuclides = []

        # Append all Nuclides in each Cell in the Universe to the dictionary
        for cell in self.cells.values():
            for nuclide in cell.get_nuclides():
                if nuclide not in nuclides:
                    nuclides.append(nuclide)

        return nuclides

    def get_nuclide_densities(self):
        """Return all nuclides contained in the universe

        Returns
        -------
        nuclides : dict
            Dictionary whose keys are nuclide names and values are 2-tuples of
            (nuclide, density)

        """
        nuclides = {}

        if self._atoms:
            volume = self.volume
            for name, atoms in self._atoms.items():
                density = 1.0e-24 * atoms.n/volume  # density in atoms/b-cm
                nuclides[name] = (name, density)
        else:
            raise RuntimeError(
                'Volume information is needed to calculate microscopic cross '
                f'sections for universe {self.id}. This can be done by running '
                'a stochastic volume calculation via the '
                'openmc.VolumeCalculation object')

        return nuclides



class Universe(UniverseBase):
    """A collection of cells that can be repeated.

    Parameters
    ----------
    universe_id : int, optional
        Unique identifier of the universe. If not specified, an identifier will
        automatically be assigned
    name : str, optional
        Name of the universe. If not specified, the name is the empty string.
    cells : Iterable of openmc.Cell, optional
        Cells to add to the universe. By default no cells are added.

    Attributes
    ----------
    id : int
        Unique identifier of the universe
    name : str
        Name of the universe
    cells : dict
        Dictionary whose keys are cell IDs and values are :class:`Cell`
        instances
    volume : float
        Volume of the universe in cm^3. This can either be set manually or
        calculated in a stochastic volume calculation and added via the
        :meth:`Universe.add_volume_information` method.
    bounding_box : openmc.BoundingBox
        Lower-left and upper-right coordinates of an axis-aligned bounding box
        of the universe.

    """

    def __init__(self, universe_id=None, name='', cells=None):
        super().__init__(universe_id, name)

        if cells is not None:
            self.add_cells(cells)

    def __repr__(self):
        string = super().__repr__()
        string += '{: <16}=\t{}\n'.format('\tGeom', 'CSG')
        string += '{: <16}=\t{}\n'.format('\tCells', list(self._cells.keys()))
        return string

    @property
    def bounding_box(self) -> openmc.BoundingBox:
        regions = [c.region for c in self.cells.values()
                   if c.region is not None]
        if regions:
            return openmc.Union(regions).bounding_box
        else:
            return openmc.BoundingBox.infinite()

    @classmethod
    def from_hdf5(cls, group, cells):
        """Create universe from HDF5 group

        Parameters
        ----------
        group : h5py.Group
            Group in HDF5 file
        cells : dict
            Dictionary mapping cell IDs to instances of :class:`openmc.Cell`.

        Returns
        -------
        openmc.Universe
            Universe instance

        """
        universe_id = int(group.name.split('/')[-1].lstrip('universe '))
        cell_ids = group['cells'][()]

        # Create this Universe
        universe = cls(universe_id)

        # Add each Cell to the Universe
        for cell_id in cell_ids:
            universe.add_cell(cells[cell_id])

        return universe


    def add_cell(self, cell):
        """Add a cell to the universe.

        Parameters
        ----------
        cell : openmc.Cell
            Cell to add

        """

        if not isinstance(cell, openmc.Cell):
            msg = f'Unable to add a Cell to Universe ID="{self._id}" since ' \
                  f'"{cell}" is not a Cell'
            raise TypeError(msg)

        cell_id = cell.id

        if cell_id not in self._cells:
            self._cells[cell_id] = cell

    def remove_cell(self, cell):
        """Remove a cell from the universe.

        Parameters
        ----------
        cell : openmc.Cell
            Cell to remove

        """

        if not isinstance(cell, openmc.Cell):
            msg = f'Unable to remove a Cell from Universe ID="{self._id}" ' \
                  f'since "{cell}" is not a Cell'
            raise TypeError(msg)

        # If the Cell is in the Universe's list of Cells, delete it
        self._cells.pop(cell.id, None)

    def create_xml_subelement(self, xml_element, memo=None):
        if memo is None:
            memo = set()

        # Iterate over all Cells
        for cell in self._cells.values():

            # If the cell was already written, move on
            if cell in memo:
                continue

            memo.add(cell)

            # Create XML subelement for this Cell
            cell_element = cell.create_xml_subelement(xml_element, memo)

            # Append the Universe ID to the subelement and add to Element
            cell_element.set("universe", str(self._id))
            xml_element.append(cell_element)

    def _partial_deepcopy(self):
        """Clone all of the openmc.Universe object's attributes except for its cells,
        as they are copied within the clone function. This should only to be
        used within the openmc.UniverseBase.clone() context.
        """
        clone = openmc.Universe(name=self.name)
        clone.volume = self.volume
        return clone
