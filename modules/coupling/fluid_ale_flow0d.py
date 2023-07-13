#!/usr/bin/env python3

# Copyright (c) 2019-2023, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import time, sys, copy
import numpy as np
from dolfinx import fem
import ufl
from petsc4py import PETSc

import solver_nonlin

from fluid_ale import FluidmechanicsAleProblem
from fluid_flow0d import FluidmechanicsFlow0DProblem
from fluid import FluidmechanicsSolverPrestr
from ale import AleProblem
from base import problem_base, solver_base
from meshutils import gather_surface_dof_indices


class FluidmechanicsAleFlow0DProblem(FluidmechanicsAleProblem,problem_base):

    def __init__(self, io_params, time_params_fluid, time_params_flow0d, fem_params, constitutive_models_fluid, constitutive_models_ale, model_params_flow0d, bc_dict_fluid, bc_dict_ale, time_curves, coupling_params_fluid_ale, coupling_params_fluid_flow0d, io, mor_params={}, comm=None):
        problem_base.__init__(self, io_params, time_params_fluid, comm)

        self.problem_physics = 'fluid_ale_flow0d'

        try: self.coupling_fluid_ale = coupling_params_fluid_ale['coupling_fluid_ale']
        except: self.coupling_fluid_ale = {}

        try: self.coupling_ale_fluid = coupling_params_fluid_ale['coupling_ale_fluid']
        except: self.coupling_ale_fluid = {}

        try: self.fluid_on_deformed = coupling_params_fluid_ale['fluid_on_deformed']
        except: self.fluid_on_deformed = 'consistent'

        self.have_dbc_fluid_ale, self.have_weak_dirichlet_fluid_ale, self.have_dbc_ale_fluid, self.have_robin_ale_fluid = False, False, False, False

        # initialize problem instances (also sets the variational forms for the fluid flow0d problem)
        self.pba  = AleProblem(io_params, time_params_fluid, fem_params, constitutive_models_ale, bc_dict_ale, time_curves, io, mor_params=mor_params, comm=self.comm)
        # ALE variables that are handed to fluid problem
        alevariables = {'Fale' : self.pba.ki.F(self.pba.d), 'Fale_old' : self.pba.ki.F(self.pba.d_old), 'w' : self.pba.wel, 'w_old' : self.pba.w_old, 'fluid_on_deformed' : self.fluid_on_deformed}
        self.pbf0 = FluidmechanicsFlow0DProblem(io_params, time_params_fluid, time_params_flow0d, fem_params, constitutive_models_fluid, model_params_flow0d, bc_dict_fluid, time_curves, coupling_params_fluid_flow0d, io, mor_params=mor_params, comm=self.comm, alevar=alevariables)

        self.pbf = self.pbf0.pbf
        self.pb0 = self.pbf0.pb0

        # modify results to write...
        self.pbf.results_to_write = io_params['results_to_write'][0]
        self.pba.results_to_write = io_params['results_to_write'][1]

        self.io = io

        # indicator for no periodic reference state estimation
        self.noperiodicref = 1

        self.localsolve = False
        self.have_rom = False

        # NOTE: Fluid and ALE function spaces should be of the same type, but are different objects.
        # For some reason, when applying a function from one funtion space as DBC to another function space,
        # errors occur. Therefore, we define these auxiliary variables and interpolate respectively...

        # fluid displacement, but defined within ALE function space
        self.ufa = fem.Function(self.pba.V_d)
        # ALE velocity, but defined within fluid function space
        self.wf = fem.Function(self.pbf.V_v)

        self.set_variational_forms()

        self.numdof = self.pbf.numdof + self.pbf0.lm.getSize() + self.pba.numdof

        self.have_rom = self.pbf.have_rom
        if self.have_rom: self.rom = self.pbf.rom

        self.sub_solve = True


    def get_problem_var_list(self):

        if self.pbf0.pbf.num_dupl > 1: is_ghosted = [1, 2, 0, 1]
        else:                          is_ghosted = [1, 1, 0, 1]
        return [self.pbf.v.vector, self.pbf.p.vector, self.pbf0.lm, self.pba.d.vector], is_ghosted


    def set_variational_forms(self):
        super().set_variational_forms()

        self.dcqd = []
        for n in range(self.pbf0.num_coupling_surf):
            self.dcqd.append(ufl.derivative(self.pbf0.cq[n], self.pba.d, self.pba.dd))


    def assemble_residual(self, t, subsolver=None):

        r_list = [None]*4

        if self.have_dbc_fluid_ale:
            # we need a vector representation of ufluid to apply in ALE DBCs
            uf_vec = self.pbf.ti.update_uf_ost(self.pbf.v.vector, self.pbf.v_old.vector, self.pbf.uf_old.vector, ufl=False)
            self.ufa.vector.axpby(1.0, 0.0, uf_vec)
            self.ufa.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
            uf_vec.destroy()

        if self.have_dbc_ale_fluid:
            #we need a vector representation of w to apply in fluid DBCs
            w_vec = self.pba.ti.update_w_ost(self.pba.d.vector, self.pba.d_old.vector, self.pba.w_old.vector, ufl=False)
            self.wf.vector.axpby(1.0, 0.0, w_vec)
            self.wf.vector.ghostUpdate(addv=PETSc.InsertMode.INSERT, mode=PETSc.ScatterMode.FORWARD)
            w_vec.destroy()

        r_list_fluidflow0d = self.pbf0.assemble_residual(t, subsolver=subsolver)

        r_list_ale = self.pba.assemble_residual(t)

        # fluid
        r_list[0] = r_list_fluidflow0d[0]
        r_list[1] = r_list_fluidflow0d[1]
        # flow0d
        r_list[2] = r_list_fluidflow0d[2]
        # ALE
        r_list[3] = r_list_ale[0]

        return r_list


    def assemble_stiffness(self, t, subsolver=None):

        K_list = [[None]*4 for _ in range(4)]

        # if self.have_dbc_fluid_ale:
            # K_list[3][0] = self.K_dv
        if self.have_weak_dirichlet_fluid_ale:
            K_dv = fem.petsc.assemble_matrix(fem.form(self.jac_dv), self.pba.bc.dbcs)
            K_dv.assemble()
            K_list[3][0] = K_dv

        K_list_fluidflow0d = self.pbf0.assemble_stiffness(t, subsolver=subsolver)

        K_list_ale = self.pba.assemble_stiffness(t)

        K_list[0][0] = K_list_fluidflow0d[0][0]
        K_list[0][1] = K_list_fluidflow0d[0][1]
        K_list[0][2] = K_list_fluidflow0d[0][2]

        K_list[1][0] = K_list_fluidflow0d[1][0]
        K_list[1][1] = K_list_fluidflow0d[1][1]
        K_list[1][2] = K_list_fluidflow0d[1][2]

        K_list[2][0] = K_list_fluidflow0d[2][0]
        K_list[2][1] = K_list_fluidflow0d[2][1]
        K_list[2][2] = K_list_fluidflow0d[2][2]

        # derivative of fluid momentum w.r.t. ALE displacement
        K_vd = fem.petsc.assemble_matrix(self.jac_vd, self.pbf.bc.dbcs)
        K_vd.assemble()
        K_list[0][3] = K_vd

        # derivative of fluid continuity w.r.t. ALE velocity
        if self.pbf.num_dupl > 1:
            K_pd = fem.petsc.assemble_matrix_block(self.jac_pd_, [])
        else:
            K_pd = fem.petsc.assemble_matrix(self.jac_pd, [])
        K_pd.assemble()
        K_list[1][3] = K_pd

        # offdiagonal s-d rows: derivative of flux constraint w.r.t. ALE displacement (in case of moving coupling boundaries)
        row_ids = list(range(self.pbf0.num_coupling_surf))
        k_sd_rows=[]
        for i in range(len(row_ids)):
            k_sd_rows.append(fem.petsc.assemble_vector(fem.form(self.pbf0.cq_factor[i]*self.dcqd[i])))
            k_sd_rows[-1].ghostUpdate(addv=PETSc.InsertMode.ADD, mode=PETSc.ScatterMode.REVERSE)

        locmatsize = self.pba.V_d.dofmap.index_map.size_local * self.pba.V_d.dofmap.index_map_bs
        matsize = self.pba.V_d.dofmap.index_map.size_global * self.pba.V_d.dofmap.index_map_bs
        K_sd = PETSc.Mat().createAIJ(size=((K_list[2][2].getSize()[0]),(locmatsize,matsize)), bsize=None, nnz=None, csr=None, comm=self.comm)
        K_sd.setUp()
        # set rows
        irs, ire = K_list[0][0].getOwnershipRange()
        for i in range(len(row_ids)):
            K_sd[row_ids[i], irs:ire] = k_sd_rows[i][irs:ire]
        K_sd.assemble()
        K_list[2][3] = K_sd

        K_list[3][3] = K_list_ale[0][0]

        # destroy PETSc vector
        for i in range(len(row_ids)): k_sd_rows[i].destroy()

        return K_list


    def get_index_sets(self, isoptions={}):

        if self.have_rom: # currently, ROM can only be on (subset of) first variable
            vred = PETSc.Vec().createMPI(size=(self.rom.V.getLocalSize()[1],self.rom.V.getSize()[1]), comm=self.comm)
            self.rom.V.multTranspose(self.pbf.v.vector, vred)
            vvec = vred
        else:
            vvec = self.pbf.v.vector

        offset_v = vvec.getOwnershipRange()[0] + self.pbf.p.vector.getOwnershipRange()[0] + self.pbf0.lm.getOwnershipRange()[0] + self.pba.d.vector.getOwnershipRange()[0]
        iset_v = PETSc.IS().createStride(vvec.getLocalSize(), first=offset_v, step=1, comm=self.comm)

        if isoptions['rom_to_new']:
            iset_r = PETSc.IS().createStride(len(self.rom.im_rom_r), first=offset_v, step=1, comm=self.comm) # same offset, since contained in v
            iset_v = iset_v.difference(iset_r) # subtract

        offset_p = offset_v + vvec.getLocalSize()
        iset_p = PETSc.IS().createStride(self.pbf.p.vector.getLocalSize(), first=offset_p, step=1, comm=self.comm)

        offset_s = offset_p + self.pbf.p.vector.getLocalSize()
        iset_s = PETSc.IS().createStride(self.pbf0.lm.getLocalSize(), first=offset_s, step=1, comm=self.comm)

        offset_d = offset_s + self.pbf0.lm.getLocalSize()
        iset_d = PETSc.IS().createStride(self.pba.d.vector.getLocalSize(), first=offset_d, step=1, comm=self.comm)

        if isoptions['rom_to_new']:
            iset_s = iset_s.expand(iset_r) # add to 0D block

        # for convenience, add ALE as last in list (since we might want to address this with a decoupled block solve)
        if isoptions['lms_to_p']:
            iset_p = iset_p.expand(iset_s) # add to pressure block - attention: will merge ROM to this block too in case of 'rom_to_new' is True!
            return [iset_v, iset_p, iset_d]
        elif isoptions['lms_to_v']:
            iset_v = iset_v.expand(iset_s) # add to velocity block (could be bad...) - attention: will merge ROM to this block too in case of 'rom_to_new' is True!
            return [iset_v, iset_p, iset_d]
        else:
            return [iset_v, iset_p, iset_s, iset_d]


    ### now the base routines for this problem

    def read_restart(self, sname, N):

        # fluid-ALE + flow0d problem
        if self.restart_step > 0:
            self.io.readcheckpoint(self, N)
            self.simname += '_r'+str(N)
            # TODO: quick-fix - simname variables of single field problems need to be addressed, too
            # but this should be handled by one variable, however neeeds revamp of I/O
            self.pbf.simname += '_r'+str(N)
            self.pba.simname += '_r'+str(N)

        self.pb0.read_restart(sname, N)

        if self.restart_step > 0:
            self.pb0.cardvasc0D.read_restart(self.pb0.output_path_0D, sname+'_lm', N, self.pbf0.lm)
            self.pb0.cardvasc0D.read_restart(self.pb0.output_path_0D, sname+'_lm', N, self.pbf0.lm_old)


    def evaluate_initial(self):

        self.pbf0.evaluate_initial()
        self.pba.evaluate_initial()


    def write_output_ini(self):

        self.io.write_output(self, writemesh=True)


    def get_time_offset(self):

        return (self.pb0.ti.cycle[0]-1) * self.pb0.cardvasc0D.T_cycl * self.noperiodicref # zero if T_cycl variable is not specified


    def evaluate_pre_solve(self, t, N):

        self.pbf0.evaluate_pre_solve(t, N)
        self.pba.evaluate_pre_solve(t, N)


    def evaluate_post_solve(self, t, N):

        self.pbf0.evaluate_post_solve(t, N)
        self.pba.evaluate_post_solve(t, N)


    def set_output_state(self, N):

        self.pbf0.set_output_state(N)
        self.pba.set_output_state(N)


    def write_output(self, N, t, mesh=False):

        self.io.write_output(self, N=N, t=t) # combined fluid-ALE output routine
        self.pb0.write_output(N, t)


    def update(self):

        # update time step - fluid+flow0d and ALE
        self.pbf0.update()
        self.pba.update()


    def print_to_screen(self):

        self.pbf0.print_to_screen()
        self.pba.print_to_screen()


    def induce_state_change(self):

        self.pbf0.induce_state_change()
        self.pba.induce_state_change()


    def write_restart(self, sname, N):

        self.io.write_restart(self, N)

        self.pb0.write_restart(sname, N)

        if self.pbf.io.write_restart_every > 0 and N % self.pbf.io.write_restart_every == 0:
            self.pb0.cardvasc0D.write_restart(self.pb0.output_path_0D, sname+'_lm', N, self.pbf0.lm)


    def check_abort(self, t):

        self.pbf0.check_abort(t)
        self.pba.check_abort(t)



class FluidmechanicsAleFlow0DSolver(solver_base):

    def initialize_nonlinear_solver(self):

        self.pb.set_problem_residual_jacobian_forms()

        # perform Proper Orthogonal Decomposition
        if self.pb.have_rom:
            self.pb.rom.prepare_rob()

        # initialize nonlinear solver class
        self.solnln = solver_nonlin.solver_nonlinear([self.pb], solver_params=self.solver_params)

        if (self.pb.pbf.prestress_initial or self.pb.pbf.prestress_initial_only) and self.pb.pbf.restart_step == 0:
            solver_params_prestr = copy.deepcopy(self.solver_params)
            # modify solver parameters in case user specified alternating ones for prestressing (should do, because it's a 2x2 problem)
            try: solver_params_prestr['solve_type'] = self.solver_params['solve_type_prestr']
            except: pass
            try: solver_params_prestr['block_precond'] = self.solver_params['block_precond_prestr']
            except: pass
            try: solver_params_prestr['precond_fields'] = self.solver_params['precond_fields_prestr']
            except: pass
            # initialize fluid mechanics solver
            self.solverprestr = FluidmechanicsSolverPrestr(self.pb.pbf, solver_params_prestr)


    def solve_initial_state(self):

        # in case we want to prestress with MULF (Gee et al. 2010) prior to solving the 3D-0D problem
        if (self.pb.pbf.prestress_initial or self.pb.pbf.prestress_initial_only) and self.pb.pbf.restart_step == 0:
            # solve solid prestress problem
            self.solverprestr.solve_initial_prestress()
            self.solverprestr.solnln.ksp.destroy()

        # consider consistent initial acceleration
        if (self.pb.pbf.fluid_governing_type == 'navierstokes_transient' or self.pb.pbf.fluid_governing_type == 'stokes_transient') and self.pb.pbf.restart_step == 0:
            # weak form at initial state for consistent initial acceleration solve
            weakform_a = self.pb.pbf.deltaW_kin_old + self.pb.pbf.deltaW_int_old - self.pb.pbf.deltaW_ext_old

            weakform_lin_aa = ufl.derivative(weakform_a, self.pb.pbf.a_old, self.pb.pbf.dv) # actually linear in a_old

            # solve for consistent initial acceleration a_old
            if self.pb.io.USE_MIXED_DOLFINX_BRANCH:
                res_a, jac_aa  = fem.form(weakform_a, entity_maps=self.pb.pbf.io.entity_maps), fem.form(weakform_lin_aa, entity_maps=self.pb.pbf.io.entity_maps)
            else:
                res_a, jac_aa  = fem.form(weakform_a), fem.form(weakform_lin_aa)
            self.solnln.solve_consistent_ini_acc(res_a, jac_aa, self.pb.pbf.a_old)


    def solve_nonlinear_problem(self, t):

        self.solnln.newton(t)


    def print_timestep_info(self, N, t, ni, li, wt):

        # print time step info to screen
        self.pb.pb0.ti.print_timestep(N, t, self.solnln.sepstring, self.pb.pbf.numstep, ni=ni, li=li, wt=wt)
