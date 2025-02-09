from collections import namedtuple
from io import StringIO
import os
import shutil
from subprocess import Popen, PIPE, STDOUT, CalledProcessError
import tempfile
from pathlib import Path
import warnings

from . import endf
import openmc.data


# For a given material, give a name for the ACE table and a list of ZAID
# identifiers.
ThermalTuple = namedtuple('ThermalTuple', ['name', 'zaids', 'nmix'])
_THERMAL_DATA = {
    'c_Al27': ThermalTuple('al27', [13027], 1),
    'c_Al_in_Al2O3': ThermalTuple('asap00', [13027], 1),
    'c_Be': ThermalTuple('be', [4009], 1),
    'c_Be_in_BeO': ThermalTuple('bebeo', [4009], 1),
    'c_Be_in_Be2C': ThermalTuple('bebe2c', [4009], 1),
    'c_Be_in_FLiBe': ThermalTuple('beflib', [4009], 1),
    'c_C6H6': ThermalTuple('benz', [1001, 6000, 6012], 2),
    'c_C_in_SiC': ThermalTuple('csic', [6000, 6012, 6013], 1),
    'c_Ca_in_CaH2': ThermalTuple('cacah2', [20040, 20042, 20043, 20044, 20046, 20048], 1),
    'c_D_in_D2O': ThermalTuple('dd2o', [1002], 1),
    'c_D_in_D2O_solid': ThermalTuple('dice', [1002], 1),
    'c_F_in_FLiBe': ThermalTuple('fflibe', [9019], 1),
    'c_Fe56': ThermalTuple('fe56', [26056], 1),
    'c_Graphite': ThermalTuple('graph', [6000, 6012, 6013], 1),
    'c_Graphite_10p': ThermalTuple('grph10', [6000, 6012, 6013], 1),
    'c_Graphite_30p': ThermalTuple('grph30', [6000, 6012, 6013], 1),
    'c_H_in_C5O2H8': ThermalTuple('lucite', [1001], 1),
    'c_H_in_CaH2': ThermalTuple('hcah2', [1001], 1),
    'c_H_in_CH2': ThermalTuple('hch2', [1001], 1),
    'c_H_in_CH4_liquid': ThermalTuple('lch4', [1001], 1),
    'c_H_in_CH4_solid': ThermalTuple('sch4', [1001], 1),
    'c_H_in_CH4_solid_phase_II': ThermalTuple('sch4p2', [1001], 1),
    'c_H_in_H2O': ThermalTuple('hh2o', [1001], 1),
    'c_H_in_H2O_solid': ThermalTuple('hice', [1001], 1),
    'c_H_in_HF': ThermalTuple('hhf', [1001], 1),
    'c_H_in_Mesitylene': ThermalTuple('mesi00', [1001], 1),
    'c_H_in_ParaffinicOil': ThermalTuple('hparaf', [1001], 1),
    'c_H_in_Toluene': ThermalTuple('tol00', [1001], 1),
    'c_H_in_UH3': ThermalTuple('huh3', [1001], 1),
    'c_H_in_YH2': ThermalTuple('hyh2', [1001], 1),
    'c_H_in_ZrH': ThermalTuple('hzrh', [1001], 1),
    'c_H_in_ZrH2': ThermalTuple('hzrh2', [1001], 1),
    'c_H_in_ZrHx': ThermalTuple('hzrhx', [1001], 1),
    'c_Li_in_FLiBe': ThermalTuple('liflib', [3006, 3007], 1),
    'c_Mg24': ThermalTuple('mg24', [12024], 1),
    'c_N_in_UN': ThermalTuple('n-un', [7014, 7015], 1),
    'c_O_in_Al2O3': ThermalTuple('osap00', [92238], 1),
    'c_O_in_BeO': ThermalTuple('obeo', [8016, 8017, 8018], 1),
    'c_O_in_D2O': ThermalTuple('od2o', [8016, 8017, 8018], 1),
    'c_O_in_H2O_solid': ThermalTuple('oice', [8016, 8017, 8018], 1),
    'c_O_in_UO2': ThermalTuple('ouo2', [8016, 8017, 8018], 1),
    'c_ortho_D': ThermalTuple('orthod', [1002], 1),
    'c_ortho_H': ThermalTuple('orthoh', [1001], 1),
    'c_para_D': ThermalTuple('parad', [1002], 1),
    'c_para_H': ThermalTuple('parah', [1001], 1),
    'c_Si28': ThermalTuple('si00', [14028], 1),
    'c_Si_in_SiC': ThermalTuple('sisic', [14028, 14029, 14030], 1),
    'c_SiO2_alpha': ThermalTuple('sio2-a', [8016, 8017, 8018, 14028, 14029, 14030], 3),
    'c_SiO2_beta': ThermalTuple('sio2-b', [8016, 8017, 8018, 14028, 14029, 14030], 3),
    'c_U_in_UN': ThermalTuple('u-un', [92238], 1),
    'c_U_in_UO2': ThermalTuple('uuo2', [8016, 8017, 8018], 1),
    'c_Y_in_YH2': ThermalTuple('yyh2', [39089], 1),
    'c_Zr_in_ZrH': ThermalTuple('zrzrh', [40000, 40090, 40091, 40092, 40094, 40096], 1),
    'c_Zr_in_ZrH2': ThermalTuple('zrzrh2', [40000, 40090, 40091, 40092, 40094, 40096], 1),
    'c_Zr_in_ZrHx': ThermalTuple('zrzrhx', [40000, 40090, 40091, 40092, 40094, 40096], 1),
}


_TEMPLATE_RECONR = """
reconr / %%%%%%%%%%%%%%%%%%% Reconstruct XS for neutrons %%%%%%%%%%%%%%%%%%%%%%%
{nendf} {npendf}
'{library} PENDF for {zsymam}'/
{mat} 2/
{error}/ err
'{library}: {zsymam}'/
'Processed by NJOY'/
0/
"""

_TEMPLATE_BROADR = """
broadr / %%%%%%%%%%%%%%%%%%%%%%% Doppler broaden XS %%%%%%%%%%%%%%%%%%%%%%%%%%%%
{nendf} {npendf} {nbroadr}
{mat} {num_temp} 0 0 0. /
{error}/ errthn
{temps}
0/
"""

_TEMPLATE_HEATR = """
heatr / %%%%%%%%%%%%%%%%%%%%%%%%% Add heating kerma %%%%%%%%%%%%%%%%%%%%%%%%%%%%
{nendf} {nheatr_in} {nheatr} /
{mat} 4 0 0 0 /
302 318 402 444 /
"""

_TEMPLATE_HEATR_LOCAL = """
heatr / %%%%%%%%%%%%%%%%% Add heating kerma (local photons) %%%%%%%%%%%%%%%%%%%%
{nendf} {nheatr_in} {nheatr_local} /
{mat} 4 0 0 1 /
302 318 402 444 /
"""

_TEMPLATE_GASPR = """
gaspr / %%%%%%%%%%%%%%%%%%%%%%%%% Add gas production %%%%%%%%%%%%%%%%%%%%%%%%%%%
{nendf} {ngaspr_in} {ngaspr} /
"""

_TEMPLATE_PURR = """
purr / %%%%%%%%%%%%%%%%%%%%%%%% Add probability tables %%%%%%%%%%%%%%%%%%%%%%%%%
{nendf} {npurr_in} {npurr} /
{mat} {num_temp} 1 20 64 /
{temps}
1.e10
0/
"""

_TEMPLATE_ACER = """
acer / %%%%%%%%%%%%%%%%%%%%%%%% Write out in ACE format %%%%%%%%%%%%%%%%%%%%%%%%
{nendf} {nacer_in} 0 {nace} {ndir}
1 0 1 .{ext} /
'{library}: {zsymam} at {temperature}'/
{mat} {temperature}
1 1 {ismooth}/
/
"""

_THERMAL_TEMPLATE_THERMR = """
thermr / %%%%%%%%%%%%%%%% Add thermal scattering data (free gas) %%%%%%%%%%%%%%%
0 {nthermr1_in} {nthermr1}
0 {mat} 12 {num_temp} 1 0 {iform} 1 221 1/
{temps}
{error} {energy_max}
thermr / %%%%%%%%%%%%%%%% Add thermal scattering data (bound) %%%%%%%%%%%%%%%%%%
{nthermal_endf} {nthermr2_in} {nthermr2}
{mat_thermal} {mat} 16 {num_temp} {inelastic} {elastic} {iform} {natom} 222 1/
{temps}
{error} {energy_max}
"""

_THERMAL_TEMPLATE_ACER = """
acer / %%%%%%%%%%%%%%%%%%%%%%%% Write out in ACE format %%%%%%%%%%%%%%%%%%%%%%%%
{nendf} {nthermal_acer_in} 0 {nace} {ndir}
2 0 1 .{ext}/
'{library}: {zsymam_thermal} processed by NJOY'/
{mat} {temperature} '{data.name}' {nza} /
{zaids} /
222 64 {mt_elastic} {elastic_type} {data.nmix} {energy_max} {iwt}/
"""


def run(commands, tapein, tapeout, input_filename=None, stdout=False,
        njoy_exec='njoy'):
    """Run NJOY with given commands

    Parameters
    ----------
    commands : str
        Input commands for NJOY
    tapein : dict
        Dictionary mapping tape numbers to paths for any input files
    tapeout : dict
        Dictionary mapping tape numbers to paths for any output files
    input_filename : str, optional
        File name to write out NJOY input commands
    stdout : bool, optional
        Whether to display output when running NJOY
    njoy_exec : str, optional
        Path to NJOY executable

    Raises
    ------
    subprocess.CalledProcessError
        If the NJOY process returns with a non-zero status

    """

    if input_filename is not None:
        with open(str(input_filename), 'w') as f:
            f.write(commands)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Copy evaluations to appropriates 'tapes'
        for tape_num, filename in tapein.items():
            tmpfilename = os.path.join(tmpdir, 'tape{}'.format(tape_num))
            shutil.copy(str(filename), tmpfilename)

        # Start up NJOY process
        njoy = Popen([njoy_exec], cwd=tmpdir, stdin=PIPE, stdout=PIPE,
                     stderr=STDOUT, universal_newlines=True)

        njoy.stdin.write(commands)
        njoy.stdin.flush()
        lines = []
        while True:
            # If process is finished, break loop
            line = njoy.stdout.readline()
            if not line and njoy.poll() is not None:
                break

            lines.append(line)
            if stdout:
                # If user requested output, print to screen
                print(line, end='')

        # Check for error
        if njoy.returncode != 0:
            raise CalledProcessError(njoy.returncode, njoy_exec,
                                     ''.join(lines))

        # Copy output files back to original directory
        for tape_num, filename in tapeout.items():
            tmpfilename = os.path.join(tmpdir, 'tape{}'.format(tape_num))
            if os.path.isfile(tmpfilename):
                shutil.move(tmpfilename, str(filename))


def make_pendf(filename, pendf='pendf', error=0.001, stdout=False):
    """Generate pointwise ENDF file from an ENDF file

    Parameters
    ----------
    filename : str
        Path to ENDF file
    pendf : str, optional
        Path of pointwise ENDF file to write
    error : float, optional
        Fractional error tolerance for NJOY processing
    stdout : bool
        Whether to display NJOY standard output

    Raises
    ------
    subprocess.CalledProcessError
        If the NJOY process returns with a non-zero status

    """

    make_ace(filename, pendf=pendf, error=error, broadr=False,
             heatr=False, purr=False, acer=False, stdout=stdout)


def make_ace(filename, temperatures=None, acer=True, xsdir=None,
             output_dir=None, pendf=False, error=0.001, broadr=True,
             heatr=True, gaspr=True, purr=True, evaluation=None,
             smoothing=True, **kwargs):
    """Generate incident neutron ACE file from an ENDF file

    File names can be passed to
    ``[acer, xsdir, pendf, broadr, heatr, gaspr, purr]``
    to specify the exact output for the given module.
    Otherwise, the files will be writen to the current directory
    or directory specified by ``output_dir``. Default file
    names mirror the variable names, e.g. ``heatr`` output
    will be written to a file named ``heatr`` unless otherwise
    specified.

    Parameters
    ----------
    filename : str
        Path to ENDF file
    temperatures : iterable of float, optional
        Temperatures in Kelvin to produce ACE files at. If omitted, data is
        produced at room temperature (293.6 K).
    acer : bool or str, optional
        Flag indicating if acer should be run. If a string is give, write the
        resulting ``ace`` file to this location. Path of ACE file to write.
        Defaults to ``"ace"``
    xsdir : str, optional
        Path of xsdir file to write. Defaults to ``"xsdir"`` in the same
        directory as ``acer``
    output_dir : str, optional
        Directory to write output for requested modules. If not provided
        and at least one of ``[pendf, broadr, heatr, gaspr, purr, acer]``
        is ``True``, then write output files to current directory. If given,
        must be a path to a directory.
    pendf : str, optional
        Path of pendf file to write. If omitted, the pendf file is not saved.
    error : float, optional
        Fractional error tolerance for NJOY processing
    broadr : bool or str, optional
        Indicating whether to Doppler broaden XS when running NJOY. If string,
        write the output tape to this file.
    heatr : bool or str, optional
        Indicating whether to add heating kerma when running NJOY. If string,
        write the output tape to this file.
    gaspr : bool or str, optional
        Indicating whether to add gas production data when running NJOY.
        If string, write the output tape to this file.
    purr : bool or str, optional
        Indicating whether to add probability table when running NJOY.
        If string, write the output tape to this file.
    evaluation : openmc.data.endf.Evaluation, optional
        If the ENDF file contains multiple material evaluations, this argument
        indicates which evaluation should be used.
    smoothing : bool, optional
        If the smoothing option (ACER card 6) is on (True) or off (False).
    **kwargs
        Keyword arguments passed to :func:`openmc.data.njoy.run`

    Raises
    ------
    subprocess.CalledProcessError
        If the NJOY process returns with a non-zero status
    IOError
        If ``output_dir`` does not point to a directory

    """
    if output_dir is None:
        output_dir = Path()
    else:
        output_dir = Path(output_dir)
        if not output_dir.is_dir():
            raise IOError("{} is not a directory".format(output_dir))

    ev = evaluation if evaluation is not None else endf.Evaluation(filename)
    mat = ev.material
    zsymam = ev.target['zsymam']

    # Determine name of library
    library = '{}-{}.{}'.format(*ev.info['library'])

    if temperatures is None:
        temperatures = [293.6]
    num_temp = len(temperatures)
    temps = ' '.join(str(i) for i in temperatures)

    # Create njoy commands by modules
    commands = ""

    nendf, npendf = 20, 21
    tapein = {nendf: filename}
    tapeout = {}
    if pendf:
        tapeout[npendf] = (output_dir / "pendf") if pendf is True else pendf

    # reconr
    commands += _TEMPLATE_RECONR
    nlast = npendf

    # broadr
    if broadr:
        nbroadr = nlast + 1
        tapeout[nbroadr] = (output_dir / "broadr") if broadr is True else broadr
        commands += _TEMPLATE_BROADR
        nlast = nbroadr

    # heatr
    if heatr:
        nheatr_in = nlast
        nheatr_local = nheatr_in + 1
        tapeout[nheatr_local] = (output_dir / "heatr_local") if heatr is True \
            else heatr + '_local'
        commands += _TEMPLATE_HEATR_LOCAL
        nheatr = nheatr_local + 1
        tapeout[nheatr] = (output_dir / "heatr") if heatr is True else heatr
        commands += _TEMPLATE_HEATR
        nlast = nheatr

    # gaspr
    if gaspr:
        ngaspr_in = nlast
        ngaspr = ngaspr_in + 1
        tapeout[ngaspr] = (output_dir / "gaspr") if gaspr is True else gaspr
        commands += _TEMPLATE_GASPR
        nlast = ngaspr

    # purr
    if purr:
        npurr_in = nlast
        npurr = npurr_in + 1
        tapeout[npurr] = (output_dir / "purr") if purr is True else purr
        commands += _TEMPLATE_PURR
        nlast = npurr

    commands = commands.format(**locals())

    # acer
    if acer:
        ismooth = int(smoothing)
        nacer_in = nlast
        for i, temperature in enumerate(temperatures):
            # Extend input with an ACER run for each temperature
            nace = nacer_in + 1 + 2*i
            ndir = nace + 1
            ext = '{:02}'.format(i + 1)
            commands += _TEMPLATE_ACER.format(**locals())

            # Indicate tapes to save for each ACER run
            tapeout[nace] = output_dir / "ace_{:.1f}".format(temperature)
            tapeout[ndir] = output_dir / "xsdir_{:.1f}".format(temperature)
    commands += 'stop\n'
    run(commands, tapein, tapeout, **kwargs)

    if acer:
        ace = (output_dir / "ace") if acer is True else Path(acer)
        xsdir = (ace.parent / "xsdir") if xsdir is None else xsdir
        with ace.open('w') as ace_file, xsdir.open('w') as xsdir_file:
            for temperature in temperatures:
                # Get contents of ACE file
                text = (output_dir / "ace_{:.1f}".format(temperature)).read_text()

                # If the target is metastable, make sure that ZAID in the ACE
                # file reflects this by adding 400
                if ev.target['isomeric_state'] > 0:
                    mass_first_digit = int(text[3])
                    if mass_first_digit <= 2:
                        text = text[:3] + str(mass_first_digit + 4) + text[4:]

                # Concatenate into destination ACE file
                ace_file.write(text)

                # Concatenate into destination xsdir file
                xsdir_in = output_dir / "xsdir_{:.1f}".format(temperature)
                xsdir_file.write(xsdir_in.read_text())

        # Remove ACE/xsdir files for each temperature
        for temperature in temperatures:
            (output_dir / "ace_{:.1f}".format(temperature)).unlink()
            (output_dir / "xsdir_{:.1f}".format(temperature)).unlink()


def make_ace_thermal(filename, filename_thermal, temperatures=None,
                     ace='ace', xsdir=None, output_dir=None, error=0.001,
                     iwt=2, evaluation=None, evaluation_thermal=None,
                     table_name=None, zaids=None, nmix=None, **kwargs):
    """Generate thermal scattering ACE file from ENDF files

    Parameters
    ----------
    filename : str
        Path to ENDF neutron sublibrary file
    filename_thermal : str
        Path to ENDF thermal scattering sublibrary file
    temperatures : iterable of float, optional
        Temperatures in Kelvin to produce data at. If omitted, data is produced
        at all temperatures given in the ENDF thermal scattering sublibrary.
    ace : str, optional
        Path of ACE file to write
    xsdir : str, optional
        Path of xsdir file to write. Defaults to ``"xsdir"`` in the same
        directory as ``ace``
    output_dir : str, optional
        Directory to write ace and xsdir files. If not provided, then write
        output files to current directory. If given, must be a path to a
        directory.
    error : float, optional
        Fractional error tolerance for NJOY processing
    iwt : int
        `iwt` parameter used in NJOR/ACER card 9
    evaluation : openmc.data.endf.Evaluation, optional
        If the ENDF neutron sublibrary file contains multiple material
        evaluations, this argument indicates which evaluation to use.
    evaluation_thermal : openmc.data.endf.Evaluation, optional
        If the ENDF thermal scattering sublibrary file contains multiple
        material evaluations, this argument indicates which evaluation to use.
    table_name : str, optional
        Name to assign to ACE table
    zaids : list of int, optional
        ZAIDs that the thermal scattering data applies to
    nmix : int, optional
        Number of atom types in mixed moderator
    **kwargs
        Keyword arguments passed to :func:`openmc.data.njoy.run`

    Raises
    ------
    subprocess.CalledProcessError
        If the NJOY process returns with a non-zero status

    """
    if output_dir is None:
        output_dir = Path()
    else:
        output_dir = Path(output_dir)
        if not output_dir.is_dir():
            raise IOError("{} is not a directory".format(output_dir))

    ev = evaluation if evaluation is not None else endf.Evaluation(filename)
    mat = ev.material
    zsymam = ev.target['zsymam']

    ev_thermal = (evaluation_thermal if evaluation_thermal is not None
                  else endf.Evaluation(filename_thermal))
    mat_thermal = ev_thermal.material
    zsymam_thermal = ev_thermal.target['zsymam'].strip()

    # Determine name, isotopes, and number of atom types
    if table_name and zaids and nmix:
        data = ThermalTuple(table_name, zaids, nmix)
    else:
        with warnings.catch_warnings(record=True) as w:
            proper_name = openmc.data.get_thermal_name(zsymam_thermal)
            if w:
                raise RuntimeError(
                    f"Thermal scattering material {zsymam_thermal} not "
                    "recognized. Please contact OpenMC developers at "
                    "https://openmc.discourse.group.")
        data = _THERMAL_DATA[proper_name]

    zaids = ' '.join(str(zaid) for zaid in data.zaids)
    nza = len(data.zaids)

    # Determine name of library
    library = '{}-{}.{}'.format(*ev_thermal.info['library'])

    # Determine if thermal elastic is present
    if (7, 2) in ev_thermal.section:
        elastic = 1
        mt_elastic = 223

        # Determine whether elastic is incoherent (0) or coherent (1)
        file_obj = StringIO(ev_thermal.section[7, 2])
        elastic_type = endf.get_head_record(file_obj)[2] - 1
    else:
        elastic = 0
        mt_elastic = 0
        elastic_type = 0

    # Determine number of principal atoms
    file_obj = StringIO(ev_thermal.section[7, 4])
    items = endf.get_head_record(file_obj)
    items, values = endf.get_list_record(file_obj)
    energy_max = values[3]
    natom = int(values[5])

    # Note that the 'iform' parameter is omitted in NJOY 99. We assume that the
    # user is using NJOY 2012 or later.
    iform = 0
    inelastic = 2

    # Determine temperatures from MF=7, MT=4 if none were specified
    if temperatures is None:
        file_obj = StringIO(ev_thermal.section[7, 4])
        endf.get_head_record(file_obj)
        endf.get_list_record(file_obj)
        endf.get_tab2_record(file_obj)
        params = endf.get_tab1_record(file_obj)[0]
        temperatures = [params[0]]
        for i in range(params[2]):
            temperatures.append(endf.get_list_record(file_obj)[0][0])

    num_temp = len(temperatures)
    temps = ' '.join(str(i) for i in temperatures)

    # Create njoy commands by modules
    commands = ""

    nendf, nthermal_endf, npendf = 20, 21, 22
    tapein = {nendf: filename, nthermal_endf: filename_thermal}
    tapeout = {}

    # reconr
    commands += _TEMPLATE_RECONR
    nlast = npendf

    # broadr
    nbroadr = nlast + 1
    commands += _TEMPLATE_BROADR
    nlast = nbroadr

    # thermr
    nthermr1_in = nlast
    nthermr1 = nthermr1_in + 1
    nthermr2_in = nthermr1
    nthermr2 = nthermr2_in + 1
    commands += _THERMAL_TEMPLATE_THERMR
    nlast = nthermr2

    commands = commands.format(**locals())

    # acer
    nthermal_acer_in = nlast
    for i, temperature in enumerate(temperatures):
        # Extend input with an ACER run for each temperature
        nace = nthermal_acer_in + 1 + 2*i
        ndir = nace + 1
        ext = '{:02}'.format(i + 1)
        commands += _THERMAL_TEMPLATE_ACER.format(**locals())

        # Indicate tapes to save for each ACER run
        tapeout[nace] = output_dir / "ace_{:.1f}".format(temperature)
        tapeout[ndir] = output_dir / "xsdir_{:.1f}".format(temperature)
    commands += 'stop\n'
    run(commands, tapein, tapeout, **kwargs)

    ace = output_dir / ace
    xsdir = (ace.parent / "xsdir") if xsdir is None else Path(xsdir)
    with ace.open('w') as ace_file, xsdir.open('w') as xsdir_file:
        # Concatenate ACE and xsdir files together
        for temperature in temperatures:
            ace_in = output_dir / "ace_{:.1f}".format(temperature)
            ace_file.write(ace_in.read_text())

            xsdir_in = output_dir / "xsdir_{:.1f}".format(temperature)
            xsdir_file.write(xsdir_in.read_text())

    # Remove ACE/xsdir files for each temperature
    for temperature in temperatures:
        (output_dir / "ace_{:.1f}".format(temperature)).unlink()
        (output_dir / "xsdir_{:.1f}".format(temperature)).unlink()
