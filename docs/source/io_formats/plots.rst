.. _io_plots:

============================================
Geometry Plotting Specification -- plots.xml
============================================

Basic plotting capabilities are available in OpenMC by creating a plots.xml file
and subsequently running with the ``--plot`` command-line flag. The root element
of the plots.xml is simply ``<plots>`` and any number output plots can be
defined with ``<plot>`` sub-elements.  Four plot types are currently implemented
in openMC:

* ``slice``  2D pixel plot along one of the major axes. Produces a PNG image
  file.
* ``voxel``  3D voxel data dump. Produces an HDF5 file containing voxel xyz
  position and cell or material id.
* ``wireframe_raytrace``  2D pixel plot of a three-dimensional view of a
  geometry using wireframes around cells or materials and coloring by depth
  through each material.
* ``solid_raytrace``  2D pixel plot of a three-dimensional view of a geometry
  with solid colored surfaces of a set of cells or materials.


------------------
``<plot>`` Element
------------------

Each plot is specified by a combination of the following attributes or
sub-elements:

  :id:
    The unique ``id`` of the plot.

    *Default*: None - Required entry

  :filename:
    Filename for the output plot file.

    *Default*: "plot"

  :color_by:
    Keyword for plot coloring.  This can be either "cell" or "material", which
    colors regions by cells and materials, respectively. For voxel plots, this
    determines which id (cell or material) is associated with each position.

    *Default*: "cell"

  :level:
    Universe depth to plot at (optional).  This parameter controls how many
    universe levels deep to pull cell and material ids from when setting plot
    colors.  If a given location does not have as many levels as specified,
    colors will be taken from the lowest level at that location. For example, if
    ``level`` is set to zero colors will be taken from top-level (universe zero)
    cells only.  However, if ``level`` is set to 1 colors will be taken from
    cells in universes that fill top-level fill-cells, and from top-level cells
    that contain materials.

    *Default*: Whatever the deepest universe is in the model

  :origin:
    Specifies the (x,y,z) coordinate of the center of the plot.  Should be three
    floats separated by spaces.

    *Default*: None - Required entry

  :width:
    Specifies the width of the plot along each of the basis directions.  Should
    be two or three floats separated by spaces for 2D plots and 3D plots,
    respectively.

    *Default*: None - Required entry

  :type:
    Keyword for type of plot to be produced. Currently "slice", "voxel",
    "wireframe_raytrace", and "solid_raytrace" plots are implemented. The
    "slice" plot type creates 2D pixel maps saved in the PNG file format. The
    "voxel" plot type produces a binary datafile containing voxel grid
    positioning and the cell or material (specified by the ``color`` tag) at the
    center of each voxel. Voxel plot files can be processed into VTK files using
    the :func:`openmc.voxel_to_vtk` function and subsequently viewed with a 3D
    viewer such as VISIT or Paraview. See :ref:`io_voxel` for information about
    the datafile structure.

    .. note:: High-resolution voxel files produced by OpenMC can be quite large,
              but the equivalent VTK files will be significantly smaller.

    *Default*: "slice"

All ``<plot>`` elements must contain the ``pixels``
attribute or sub-element:

  :pixels:
    Specifies the number of pixels or voxels to be used along each of the basis
    directions for "slice" and "voxel" plots, respectively. Should be two or
    three integers separated by spaces.

    .. warning:: If the aspect ratio defined in ``pixels`` does not match the
                 aspect ratio defined in ``width`` the plot may appear stretched
                 or squeezed.

    .. warning:: Geometry features along a basis direction smaller than
                 ``width``/``pixels`` along that basis direction may not appear
                 in the plot.

    *Default*: None - Required entry for all plots

``<plot>`` elements of ``type`` "slice" can also contain the following
attributes or sub-elements.  These are not used in "voxel" plots:

  :basis:
    Keyword specifying the plane of the plot for "slice" type plots.  Can be
    one of: "xy", "xz", "yz".

    *Default*: "xy"

  :background:
    Specifies the RGB color of the regions where no OpenMC cell can be found.
    Should be three integers separated by spaces.

    *Default*: 0 0 0 (black)

  :color:
    Any number of this optional tag may be included in each ``<plot>`` element,
    which can override the default random colors for cells or materials. Each
    ``color`` element must contain ``id`` and ``rgb`` sub-elements.

    :id:
      Specifies the cell or material unique id for the color specification.

    :rgb:
      Specifies the custom color for the cell or material. Should be 3 integers
      separated by spaces.

    :xs:
      The attenuation coefficient for volume rendering of color in units of
      inverse centimeters. Zero corresponds to transparency. Only for plot type
      "wireframe_raytrace".

    As an example, if your plot is colored by material and you want material 23
    to be blue, the corresponding ``color`` element would look like:

    .. code-block:: xml

        <color id="23" rgb="0 0 255" />

    *Default*: None

  :mask:
    The special ``mask`` sub-element allows for the selective plotting of *only*
    user-specified cells or materials. Only one ``mask`` element is allowed per
    ``plot`` element, and it must contain as attributes or sub-elements a
    background masking color and a list of cells or materials to plot:

    :components:
      List of unique ``id`` numbers of the cells or materials to plot. Should be
      any number of integers separated by spaces.

    :background:
      Color to apply to all cells or materials not in the ``components`` list of
      cells or materials to plot. This overrides any ``color`` color
      specifications.

    *Default*: 255 255 255 (white)

  :show_overlaps:
    Indicates whether overlapping regions of different cells are shown.

    *Default*: None

  :overlap_color:
    Specifies the RGB color of overlapping regions of different cells. Does not
    do anything if ``show_overlaps`` is "false" or not specified. Should be 3
    integers separated by spaces.

    *Default*: 255 0 0 (red)

  :meshlines:
    The ``meshlines`` sub-element allows for plotting the boundaries of a
    regular mesh on top of a plot. Only one ``meshlines`` element is allowed per
    ``plot`` element, and it must contain as attributes or sub-elements a mesh
    type and a linewidth.  Optionally, a color may be specified for the overlay:

    :meshtype:
      The type of the mesh to be plotted. Valid options are "tally", "entropy",
      "ufs", and "cmfd".  If plotting "tally" meshes, the id of the mesh to plot
      must be specified with the ``id`` sub-element.

    :id:
      A single integer id number for the mesh specified on ``tallies.xml`` that
      should be plotted. This element is only required for ``meshtype="tally"``.

    :linewidth:
      A single integer number of pixels of linewidth to specify for the mesh
      boundaries. Specifying this as 0 indicates that lines will be 1 pixel
      thick, specifying 1 indicates 3 pixels thick, specifying 2 indicates
      5 pixels thick, etc.

    :color:
      Specifies the custom color for the meshlines boundaries. Should be 3
      integers separated by whitespace.  This element is optional.

      *Default*: 0 0 0 (black)

    *Default*: None

``<plot>`` elements of ``type`` "wireframe_raytrace" or "solid_raytrace" can contain the
following attributes or sub-elements.

  :camera_position:
    Location in 3D Cartesian space the camera is at.


    *Default*: None - Required for all ``wireframe_raytrace`` or
    ``solid_raytrace`` plots

  :look_at:
    Location in 3D Cartesian space the camera is looking at.


    *Default*: None - Required for all ``wireframe_raytrace`` or
    ``solid_raytrace`` plots

  :field_of_view:
    The horizontal field of view in degrees. Defaults to roughly the same value
    as for the human eye.

    *Default*: 70

  :orthographic_width:
    If set to a nonzero value, an orthographic rather than perspective
    projection for the camera is employed. An orthographic projection puts out
    parallel rays from the camera of a width prescribed here in the horizontal
    direction, with the width in the vertical direction decided by the pixel
    aspect ratio.

    *Default*: 0

``<plot>`` elements of ``type`` "solid_raytrace" can contain the following attributes or
sub-elements.

  :opaque_ids:
    List of integer IDs of cells or materials to be treated as visible in the
    plot. Whether the integers are interpreted as cell or material IDs depends
    on ``color_by``.

    *Default*: None - Required for all phong plots

  :light_position:
    Location in 3D Cartesian space of the light.


    *Default*: Same location as ``camera_position``

  :diffuse_fraction:
    Fraction of light originating from non-directional sources. If set to one,
    the coloring is not influenced by surface curvature, and no shadows appear.
    If set to zero, only regions illuminated by the light are not black.


    *Default*: 0.1

``<plot>`` elements of ``type`` "wireframe_raytrace" can contain the following
attributes or sub-elements.

  :wireframe_color:
    RGB value of the wireframe's color

    *Default*: 0, 0, 0 (black)

  :wireframe_thickness:
    Integer number of pixels that the wireframe takes up. The value is a radius
    of the wireframe. Setting to zero removes any wireframing.

    *Default*: 0

  :wireframe_ids:
    Integer IDs of cells or materials of regions to draw wireframes around.
    Whether the integers are interpreted as cell or material IDs depends on
    ``color_by``.

    *Default*: None
