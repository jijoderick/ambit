#!/usr/bin/env python3

# Copyright (c) 2019-2023, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import sys
import numpy as np

from petsc4py import PETSc
import timeintegration
import utilities
import solver_nonlin

from base import problem_base, solver_base

# framework of 0D flow models, relating pressure p (and its derivative) to fluxes q

class Flow0DProblem(problem_base):

    def __init__(self, io_params, time_params, model_params, time_curves, coupling_params={}, comm=None):
        problem_base.__init__(self, io_params, time_params, comm)

        self.problem_physics = 'flow0d'

        self.simname = io_params['simname']

        self.time_params = time_params

        # only relevant to syspul* models
        try:
            self.chamber_models = model_params['chamber_models']
            if 'ao' not in self.chamber_models.keys(): self.chamber_models['ao'] = {'type' : '0D_rigid'} # add aortic root model
        except: self.chamber_models = {}

        try: self.coronary_model = model_params['coronary_model']
        except: self.coronary_model = None

        try: self.vad_model = model_params['vad_model']
        except: self.vad_model = None

        try: self.excitation_curve = model_params['excitation_curve']
        except: self.excitation_curve = None

        try: initial_file = time_params['initial_file']
        except: initial_file = ''

        # could use extra write frequency setting for 0D model (i.e. for coupled problem)
        try: self.write_results_every_0D = io_params['write_results_every_0D']
        except: self.write_results_every_0D = io_params['write_results_every']

        # for restart
        try: self.write_restart_every = io_params['write_restart_every']
        except: self.write_restart_every = -1

        # could use extra output path setting for 0D model (i.e. for coupled problem)
        try: self.output_path_0D = io_params['output_path_0D']
        except: self.output_path_0D = io_params['output_path']

        # whether to output midpoint (t_{n+theta}) of state variables or endpoint (t_{n+1}) - for post-processing
        try: self.output_midpoint = io_params['output_midpoint_0D']
        except: self.output_midpoint = True

        try: valvelaws = model_params['valvelaws']
        except: valvelaws = {'av' : ['pwlin_pres',0], 'mv' : ['pwlin_pres',0], 'pv' : ['pwlin_pres',0], 'tv' : ['pwlin_pres',0]}

        try: self.cq = coupling_params['coupling_quantity']
        except: self.cq = ['volume']

        try: self.vq = coupling_params['variable_quantity']
        except: self.vq = ['pressure']

        try: self.eps_periodic = time_params['eps_periodic']
        except: self.eps_periodic = 1.0e-20

        try: self.periodic_checktype = time_params['periodic_checktype']
        except: self.periodic_checktype = ['allvar']

        try: self.prescribed_variables = model_params['prescribed_variables']
        except: self.prescribed_variables = {}

        try: self.perturb_type = model_params['perturb_type'][0]
        except: self.perturb_type = None

        try: self.perturb_factor = model_params['perturb_type'][1]
        except: self.perturb_factor = 1.

        try: self.perturb_id = model_params['perturb_type'][2]
        except: self.perturb_id = -1

        try: self.initial_backwardeuler = time_params['initial_backwardeuler']
        except: self.initial_backwardeuler = False

        try: self.perturb_after_cylce = model_params['perturb_after_cylce']
        except: self.perturb_after_cylce = -1
        # definitely set to -1 if we don't have a perturb type
        if self.perturb_type is None: self.perturb_after_cylce = -1

        self.have_induced_pert = False

        # initialize 0D model class - currently, we always init with True since restart will generate new output file names (so no need to append to old ones)
        if model_params['modeltype'] == '2elwindkessel':
            from cardiovascular0D_2elwindkessel import cardiovascular0D2elwindkessel
            self.cardvasc0D = cardiovascular0D2elwindkessel(model_params['parameters'], self.cq, self.vq, init=True, comm=self.comm)
        elif model_params['modeltype'] == '4elwindkesselLsZ':
            from cardiovascular0D_4elwindkesselLsZ import cardiovascular0D4elwindkesselLsZ
            self.cardvasc0D = cardiovascular0D4elwindkesselLsZ(model_params['parameters'], self.cq, self.vq, init=True, comm=self.comm)
        elif model_params['modeltype'] == '4elwindkesselLpZ':
            from cardiovascular0D_4elwindkesselLpZ import cardiovascular0D4elwindkesselLpZ
            self.cardvasc0D = cardiovascular0D4elwindkesselLpZ(model_params['parameters'], self.cq, self.vq, init=True, comm=self.comm)
        elif model_params['modeltype'] == 'syspul':
            from cardiovascular0D_syspul import cardiovascular0Dsyspul
            self.cardvasc0D = cardiovascular0Dsyspul(model_params['parameters'], self.chamber_models, self.cq, self.vq, valvelaws=valvelaws, cormodel=self.coronary_model, vadmodel=self.vad_model, init=True, comm=self.comm)
        elif model_params['modeltype'] == 'syspulcap':
            from cardiovascular0D_syspulcap import cardiovascular0Dsyspulcap
            self.cardvasc0D = cardiovascular0Dsyspulcap(model_params['parameters'], self.chamber_models, self.cq, self.vq, valvelaws=valvelaws, cormodel=self.coronary_model, vadmodel=self.vad_model, init=True, comm=self.comm)
        elif model_params['modeltype'] == 'syspulcapcor':
            from cardiovascular0D_syspulcap import cardiovascular0Dsyspulcapcor
            self.cardvasc0D = cardiovascular0Dsyspulcapcor(model_params['parameters'], self.chamber_models, self.cq, self.vq, valvelaws=valvelaws, cormodel=self.coronary_model, vadmodel=self.vad_model, init=True, comm=self.comm)
        elif model_params['modeltype'] == 'syspulcaprespir':
            from cardiovascular0D_syspulcaprespir import cardiovascular0Dsyspulcaprespir
            self.cardvasc0D = cardiovascular0Dsyspulcaprespir(model_params['parameters'], self.chamber_models, self.cq, self.vq, valvelaws=valvelaws, cormodel=self.coronary_model, vadmodel=self.vad_model, init=True, comm=self.comm)
        else:
            raise NameError("Unknown 0D modeltype!")

        self.numdof = self.cardvasc0D.numdof

        # vectors and matrices
        self.dK = PETSc.Mat().createAIJ(size=(self.numdof,self.numdof), bsize=None, nnz=None, csr=None, comm=self.comm)
        self.dK.setUp()

        self.K = PETSc.Mat().createAIJ(size=(self.numdof,self.numdof), bsize=None, nnz=None, csr=None, comm=self.comm)
        self.K.setUp()

        self.s, self.s_old, self.s_mid = self.K.createVecLeft(), self.K.createVecLeft(), self.K.createVecLeft()
        self.sTc, self.sTc_old = self.K.createVecLeft(), self.K.createVecLeft()

        self.df, self.df_old = self.K.createVecLeft(), self.K.createVecLeft()
        self.f, self.f_old   = self.K.createVecLeft(), self.K.createVecLeft()

        self.aux, self.aux_old, self.aux_mid = np.zeros(self.numdof), np.zeros(self.numdof), np.zeros(self.numdof)
        self.auxTc, self.auxTc_old = np.zeros(self.numdof), np.zeros(self.numdof)

        self.s_set = self.K.createVecLeft() # set point for multiscale analysis

        self.c, self.y = [], []

        # initialize flow0d time-integration class
        self.ti = timeintegration.timeintegration_flow0d(time_params, time_curves, self.t_init, comm=self.comm)

        if initial_file:
            self.initialconditions = self.cardvasc0D.set_initial_from_file(initial_file)
        else:
            self.initialconditions = time_params['initial_conditions']

        self.cardvasc0D.initialize(self.s, self.initialconditions)
        self.cardvasc0D.initialize(self.s_old, self.initialconditions)
        self.cardvasc0D.initialize(self.sTc_old, self.initialconditions)

        self.theta_ost = time_params['theta_ost']

        # for solver access
        self.odemodel = self.cardvasc0D


    def assemble_residual_stiffness(self, t):

        theta = self.theta0d_timint(t)

        K = PETSc.Mat().createAIJ(size=(self.numdof,self.numdof), bsize=None, nnz=None, csr=None, comm=self.comm)
        K.setUp()

        # 0D rhs vector: r = (df - df_old)/dt + theta * f + (1-theta) * f_old
        r = K.createVecLeft()

        r.axpy(1./self.dt, self.df)
        r.axpy(-1./self.dt, self.df_old)

        r.axpy(theta, self.f)
        r.axpy(1.-theta, self.f_old)

        self.dK.assemble()
        self.K.assemble()
        K.assemble()

        K.axpy(1./self.dt, self.dK)
        K.axpy(theta, self.K)

        # if we have prescribed variable values over time
        if bool(self.prescribed_variables):
            for a in self.prescribed_variables:
                varindex = self.cardvasc0D.varmap[a]
                curvenumber = self.prescribed_variables[a]
                val = self.ti.timecurves(curvenumber)(t)
                self.cardvasc0D.set_prescribed_variables(self.s, r, K, val, varindex)

        return r, K


    def theta0d_timint(self, t):

        if self.initial_backwardeuler:
            if np.isclose(t,self.dt):
                theta = 1.0
            else:
                theta = self.theta_ost
        else:
            theta = self.theta_ost

        return theta


    def writerestart(self, sname, N, ms=False):

        self.cardvasc0D.write_restart(self.output_path_0D, sname+'_s', N, self.s)
        self.cardvasc0D.write_restart(self.output_path_0D, sname+'_aux', N, self.aux)
        self.cardvasc0D.write_restart(self.output_path_0D, sname+'_sTc_old', N, self.sTc_old)
        if ms: self.cardvasc0D.write_restart(self.output_path_0D, sname+'_s_set', N, self.s_set)

        if self.cardvasc0D.T_cycl > 0: # write heart cycle info
            if self.comm.rank == 0:
                filename = self.output_path_0D+'/checkpoint_'+sname+'_cycledata_'+str(N)+'.txt'
                f = open(filename, 'wt')
                f.write('%i %.8f' % (self.ti.cycle[0],self.ti.cycleerror[0]))
                f.close()


    def readrestart(self, sname, rst, ms=False):

        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_s', rst, self.s)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_s', rst, self.s_old)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_aux', rst, self.aux)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_aux', rst, self.aux_old)
        self.cardvasc0D.read_restart(self.output_path_0D, sname+'_sTc_old', rst, self.sTc_old)
        if ms: self.cardvasc0D.read_restart(self.output_path_0D, sname+'_s_set', rst, self.s_set)

        if self.cardvasc0D.T_cycl > 0: # read heart cycle info
            self.ti.cycle[0] = np.loadtxt(self.output_path_0D+'/checkpoint_'+sname+'_cycledata_'+str(rst)+'.txt', usecols=(0), dtype=int)
            self.ti.cycleerror[0] = np.loadtxt(self.output_path_0D+'/checkpoint_'+sname+'_cycledata_'+str(rst)+'.txt', usecols=(1), dtype=float)
            self.t_init -= (self.ti.cycle[0]-1) * self.cardvasc0D.T_cycl


    def evaluate_activation(self, t):

        # activation curves
        if bool(self.chamber_models):
            ci=0
            for ch in ['lv','rv','la','ra']:
                if self.chamber_models[ch]['type']=='0D_elast':
                    self.y[ci] = self.ti.timecurves(self.chamber_models[ch]['activation_curve'])(t)
                    ci+=1
                if self.chamber_models[ch]['type']=='0D_elast_prescr':
                    self.y[ci] = self.ti.timecurves(self.chamber_models[ch]['elastance_curve'])(t)
                    ci+=1
                if self.chamber_models[ch]['type']=='0D_prescr':
                    self.c[self.len_c_3d0d+ci] = self.ti.timecurves(self.chamber_models[ch]['prescribed_curve'])(t)
                    ci+=1


    def induce_perturbation(self):

        if self.perturb_after_cylce > 0: # at least run through one healthy cycle

            if self.ti.cycle[0] > self.perturb_after_cylce:

                if self.comm.rank == 0:
                    print(">>> Induced cardiovascular disease type: %s" % (self.perturb_type))
                    sys.stdout.flush()

                self.cardvasc0D.induce_perturbation(self.perturb_type, self.perturb_factor)
                self.have_induced_pert = True

    ### now the base routines for this problem

    def read_restart(self, sname, N):

        # read restart information
        if self.restart_step > 0:
            self.readrestart(sname, N)
            self.simname += '_r'+str(N)


    def evaluate_initial(self):

        # evaluate old state
        if self.excitation_curve is not None:
            self.c = []
            self.c.append(self.ti.timecurves(self.excitation_curve)(self.t_init))
        if bool(self.chamber_models):
            self.y = []
            for ch in ['lv','rv','la','ra']:
                if self.chamber_models[ch]['type']=='0D_elast': self.y.append(self.ti.timecurves(self.chamber_models[ch]['activation_curve'])(self.t_init))
                if self.chamber_models[ch]['type']=='0D_elast_prescr': self.y.append(self.ti.timecurves(self.chamber_models[ch]['elastance_curve'])(self.t_init))
                if self.chamber_models[ch]['type']=='0D_prescr': self.c.append(self.ti.timecurves(self.chamber_models[ch]['prescribed_curve'])(self.t_init))

        # if we have prescribed variable values over time
        if bool(self.prescribed_variables):
            for a in self.prescribed_variables:
                varindex = self.cardvasc0D.varmap[a]
                curvenumber = self.prescribed_variables[a]
                val = self.ti.timecurves(curvenumber)(self.t_init)
                self.s[varindex], self.s_old[varindex] = val, val

        self.cardvasc0D.evaluate(self.s_old, self.t_init, self.df_old, self.f_old, None, None, self.c, self.y, self.aux_old)
        self.auxTc_old[:] = self.aux_old[:]


    def write_output_ini(self):
        pass


    def get_time_offset(self):

        return (self.ti.cycle[0]-1) * self.cardvasc0D.T_cycl # zero if T_cycl variable is not specified


    def evaluate_pre_solve(self, t):

        # external volume/flux from time curve
        if self.excitation_curve is not None:
            self.c[0] = self.ti.timecurves(self.excitation_curve)(t)
        # activation curves
        self.evaluate_activation(t)


    def evaluate_post_solve(self, t, N):
        pass


    def set_output_state(self):

        # get midpoint dof values for post-processing (has to be called before update!)
        self.cardvasc0D.set_output_state(self.s, self.s_old, self.s_mid, self.theta_ost, midpoint=self.output_midpoint)
        self.cardvasc0D.set_output_state(self.aux, self.aux_old, self.aux_mid, self.theta_ost, midpoint=self.output_midpoint)


    def write_output(self, N, t):

        # raw txt file output of 0D model quantities
        if self.write_results_every_0D > 0 and N % self.write_results_every_0D == 0:
            self.cardvasc0D.write_output(self.output_path_0D, t, self.s_mid, self.aux_mid, self.simname)


    def update(self):

        # update timestep
        self.cardvasc0D.update(self.s, self.df, self.f, self.s_old, self.df_old, self.f_old, self.aux, self.aux_old)


    def print_to_screen(self):

        self.cardvasc0D.print_to_screen(self.s_mid,self.aux_mid)


    def induce_state_change(self):

        # induce some disease/perturbation for cardiac cycle (i.e. valve stenosis or leakage)
        if self.perturb_type is not None and not self.have_induced_pert:
            self.induce_perturbation()


    def write_restart(self, sname, N):

        # write 0D restart info - old and new quantities are the same at this stage (except cycle values sTc)
        if self.write_restart_every > 0 and N % self.write_restart_every == 0:
            self.writerestart(sname, N)


    def check_abort(self, t):

        # check for periodicity in cardiac cycle and stop if reached (only for syspul* models - cycle counter gets updated here)
        is_periodic = self.cardvasc0D.cycle_check(self.s, self.sTc, self.sTc_old, self.aux, self.auxTc, self.auxTc_old, t, self.ti.cycle, self.ti.cycleerror, self.eps_periodic, check=self.periodic_checktype, inioutpath=self.output_path_0D, nm=self.simname, induce_pert_after_cycl=self.perturb_after_cylce)

        if is_periodic:
            if self.comm.rank == 0:
                print("Periodicity reached after %i heart cycles with cycle error %.4f! Finished. :-)" % (self.ti.cycle[0]-1,self.ti.cycleerror[0]))
                sys.stdout.flush()
            return True



class Flow0DSolver(solver_base):

    def initialize_nonlinear_solver(self):

        # initialize nonlinear solver class
        self.solnln = solver_nonlin.solver_nonlinear_ode([self.pb], solver_params=self.solver_params)


    def solve_initial_state(self):
        pass


    def solve_nonlinear_problem(self, t):

        self.solnln.newton(t)


    def print_timestep_info(self, N, t, wt):

        # print time step info to screen
        self.pb.ti.print_timestep(N, t, self.solnln.sepstring, self.pb.numstep, wt=wt)
