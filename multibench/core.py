# Copyright (C) 2014 Josh Willis
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 3 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General
# Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.

# The following imports *should* be available to those importing
# the package multibench
import time
from timeit import Timer

try:
    # We *prefer* subprocess32 on linux.  And we only
    # care about linux.
    import subprocess32 as subprocess
except ImportError:
    import subprocess as subprocess

# These are private imports
import os as _os
import argparse as _ap

# Determine which commands for setting affinity are available
with open(_os.devnull,"r+") as _dev_null:
    # We prefer to use numactl if we can, as we can also
    # bind the memory
    if subprocess.call(['numactl','--hardware'],stdout=_dev_null,stderr=_dev_null) == 0:
        HAVE_NUMACTL = True
    else:
        HAVE_NUMACTL = False

    if subprocess.call(['taskset','-V'],stdout=_dev_null,stderr=_dev_null) == 0:
        HAVE_TASKSET = True
    else:
        HAVE_TASKSET = False

    if (not HAVE_NUMACTL) and (not HAVE_TASKSET):
        raise RuntimeError("Need at least one of numactl or taskset on path")

def set_affinity_cmd(command, bindmem=None):
    """
    This command sets (globally) which operating system command
    is used to bind the affinity of the various jobs, and whether
    or not to also bind the memory.

    The 'command' parameter may be one of 'numactl' or 'taskset',
    and whichever command is chosen must be found on the current
    PATH.  That is determined y looking at the global variables
    HAVE_NUMACTL and HAVE_TASKSET, which are set at module load
    time.

    The 'bindmem' option defaults to 'None', in which case it is
    interpreted as binding the memory to the local pool if
    'command' is 'numactl', and not doing so for 'taskset'. It
    may be set to 'True' or 'False' to explicitly enable one
    behavior, except that it is an error to set it to 'True'
    when 'command' is 'taskset', as taskset does not support
    binding the memory.
    """

    global affinity_cmd
    global mem_cmd

    if (command == 'numactl') and not HAVE_NUMACTL:
        raise ValueError("You specified 'numactl' but that is not available on your PATH")
    elif (command == 'taskset') and not HAVE_TASKSET:
        raise ValueError("You specified 'taskset' but that is not available on your PATH")
    elif (command == 'taskset') and bindmem:
        raise ValueError("You specified 'taskset' and 'bindmem=True', which is unsupported")
    elif command not in ['numactl', 'taskset']:
        raise ValueError("command must be one of 'numactl' or 'taskset'")

    if command == 'numactl':
        affinity_cmd = ['numactl', '-C']
        if bindmem in [None, True]:
            mem_cmd = ['-l', '--']
        else:
            mem_cmd = ['--']
    elif command == 'taskset':
        affinity_cmd = ['taskset', '-c']
        mem_cmd = []

# Set the defaults

set_affinity_cmd(command='numactl', bindmem=None)


# Now define the functions our module uses to parse its own command line
# options.  Note that a big responsibility of this module will be correctly
# passing on all other arguments to the subprograms

def insert_option_group(parser):
    """
    This takes an existing argparse instance and adds all of the options
    that the driver scripts themselves may use. These specify the timing
    and dummy programs, the cpu affinity lists, and may read and write from
    files, depending on how the driver script is structured.
    """
    bench_group = parser.add_argument_group("Options for setting the cpu affinity"
                                            " and therefore implicitly the number"
                                            " of dummy jobs, and number of threads"
                                            " assigned to each job (dummy or real)."
                                            " This option group also specifies the"
                                            " executable to use as the dummy and"
                                            " timing programs.")
    bench_group.add_argument("--mbench-cpu-affinity-list", 
                             help="Space separated list of cpu affinities. The"
                             " length of the list is the number of jobs to run,"
                             " and the number of cpus in each list-item is the number"
                             " of threads to assign to each job. Each list-item must"
                             " itself be a comma-separated list of cpu IDs", 
                             nargs='*', default=[])

    bench_group.add_argument("--mbench-gpu-list",
                             help="Space separated list of gpu devices",
                             nargs='*', default=[])

    bench_group.add_argument("--mbench-dummy-program", 
                             help="The program to execute to fill up  unoccupied cores"
                             " on the CPU socket.  It should never terminate on its"
                             " own, and should require minimal setup time. It is an"
                             " error if this argument is not given, but the length of"
                             " '--mbench-cpu-affinity-list' is greater than one. If"
                             " '--mbench-input-file' is given, this program will be"
                             " called successively with the argument '--problem' and"
                             " each line of that input file in turn.",
                             default=None)

    bench_group.add_argument("--mbench-timing-program",
                             help="The main program which should time the execution of"
                             " a single problem, and print whatever output summarizes"
                             " the performance on that problem to stdout. If"
                             " '--mbench-input-file' is given, this program will be"
                             " called successively with the argument '--problem' and"
                             " each line of that input file in turn, and its successive"
                             " outputs will be appended to '--mbench-output-file'.",
                             default=None)

    bench_group.add_argument("--mbench-nthreads-env-name",
                             help="The name of the environment variable to set in"
                             " order to coummunicate to the dummy and timing programs"
                             " the number of threads they should use.",
                             default=None)

    bench_group.add_argument("--mbench-wait-time",
                             help="The amount of time (in seconds)to sleep before"
                             " starting the next job.", type=int, default=10)

    bench_group.add_argument("--mbench-affinity-cmd",
                             help="The command to use to set the CPU affinity of jobs",
                             choices=['numactl', 'taskset'], default='numactl')

    # For the following function, because we do not want simply on/off behavior,
    # we can't specify the type and also can't use 'store_true'
    bench_group.add_argument("--mbench-bind-mem",
                             help="Determine whether or not to bind the jobs to use"
                             " only memory local to that node. This cannot be 'True'"
                             " when the affinity command is 'taskset'. 'None' will"
                             " cause this to behave as 'True' for numactl, 'False' for"
                             " taskset",
                             choices=['None', 'True', 'False'], default='None')

def from_cli(opt):
    """
    This function acts on the options add by insert_option_group.  Currently
    this means only that the global variables specifying the affinity command
    and whether or not to also bind memory are set.
    """
    bind_mem_dict = { 'None' : None, 'True' : True, 'False' : False}

    set_affinity_cmd(command=opt.mbench_affinity_cmd,
                     bindmem=bind_mem_dict[opt.mbench_bind_mem])
    

                             
def insert_io_option_group(parser):
    """
    This function is called by programs wishing to read each problem successively
    from a specified input file, calling the dummy and timing programs on each problem
    in that file, and writing the output of the timing program to the output file.
    """
    bench_io_group = parser.add_argument_group("Options for setting the input and"
                                               " output files for problems and benchmarking"
                                               " results")

    bench_io_group.add_argument("--mbench-input-file",
                                help="A file from which to read single problem instances,"
                                " one per line, to provide to the dummy and timing"
                                " programs.", default=None)

    bench_io_group.add_argument("--mbench-output-file",
                                help="A file to which to write the stdout of the timing"
                                " program, appending each problem's output to that file.",
                                default=None)

    bench_io_group.add_argument("--mbench-problem-argstring",
                                help="The string used to specify a particular problem to"
                                " both the dummy and timing scripts.  The leading double"
                                " dash should be omitted.", default="problem")

def set_problem_string(probstring):
    """
    This function sets the global variable 'problem_string' to be the argument (including
    leading double dashes) that should be passed to both dummy and timing programs
    to tell them which problem to act on.

    The argument should be that string *without* the leading double dashes, which will
    be added when this function is called.
    """

    global problem_string
    problem_string = "--"
    problem_string += probstring

set_problem_string("problem")

def io_from_cli(opt):
    """
    This function acts on the command line arguments for the I/O option group.

    At present, that means setting the global variable 'problem_string'
    """

    set_problem_string(opt.mbench_problem_argstring)

def insert_timing_option_group(parser):
    """
    This program adds the options for the minimum length of time to use for each
    benchmark run (when calling BenchProblem.needed_n(time)) and how many repeats
    of such a run to perform.

    This option group is added separately as it will usually NOT be called from the
    driving script, but rather the timing program (when that is in python).
    """
    bench_timing_group = parser.add_argument_group("Options to control the benchmarking parameters")
    bench_timing_group.add_argument("--mbench-time",
                                    help="The minimum amount of time per timing loop",
                                    type=float, default=1.0)
    bench_timing_group.add_argument("--mbench-repeats",
                                    help="The number of repeats to make of the timing loop",
                                    type=int, default=8)

def parse_cpu_affinity_list(cpu_affinity_list):
    njobs = len(cpu_affinity_list)
    if njobs == 0:
        raise ValueError("You must give at least one cpu affinity mask!")
    else:
        nthreads = len(cpu_affinity_list[0].split(','))
    for clist in cpu_affinity_list[1:]:
        if len(clist.split(',')) != nthreads:
            raise ValueError("Each item in --mbench-cpu-affinity-list must list the same number of cpus")
    return nthreads

def use_gpus(opt):
    if len(opt.bench_gpu_list) > 0:
        if len(opt.bench_cpu_affinity_list) != 1:
            raise ValueError("When giving non-empty GPU list CPU affinity list must have length one")
        else:
            return True
    else:
        return False

def format_time_strings(times):
    """
    Takes a list of related times, and returns a list of string
    representations with a common time unit derived from the first
    time in the list.
    """
    base_time = times[0] # We choose our units based on this
    retlist = []
    
    if (base_time >= 1.0):
        tstr = " s"
        fact = 1.0
    elif (base_time >= 1.0e-3):
        fact = 1.0e3
        tstr = " ms"
    elif (base_time >= 1.0e-6):
        tstr = " us"
        fact = 1.0e6
    else:
        # Yeah, like we ever get here:
        tstr = " ns"
        fact = 1.0e9
        
    for t in times:
        tcopy = t
        retlist.append("{0:g} {1}".format(tcopy*fact,tstr))

    return retlist

class MultiBenchProblem(object):
    """
    This provides a base class from which specific problems to be benchmarked
    should be derived. Those classes will need to define a nontrivial
    constructor and a method 'execute' that takes no arguments. Any 
    setup that should be done after the constructor and should be timed
    should be done in a method named '_setup()' that again takes no
    arguments.  This base class will then provide a method 'setup()'
    that calls that method and times it, putting the result into
    self.setup_time.
    """
    def __init__(self,*args,**kwargs):
        pass

    def execute(self):
        pass

    def _setup(self):
        # So that we always have this method; in nontrivial cases
        # it should be overridden.
        pass

    def setup(self):
        start_time = time.time()
        self._setup()
        self.setup_time = time.time()-start_time

    def needed_n(self,secs):
        """
        Method to determine the number of calls to
        self.execute() required to take at least 'secs'
        seconds.
        """

        # This just copies what timeit does internally
        # when called from the command line. The
        # use of powers of ten means that things can get
        # out of hand quickly if you are making 'secs'
        # large (i.e., more than a few secs).

        tmp_Timer = Timer(self.execute)

        for i in range(1,10):
            n = 10**i
            x = tmp_Timer.timeit(number=n)
            if x > secs:
                break

        del tmp_Timer

        return n

