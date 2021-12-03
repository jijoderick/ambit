#!/usr/bin/env python3

# Copyright (c) 2019-2021, Dr.-Ing. Marc Hirschvogel
# All rights reserved.

# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import numpy as np
import sympy as sp

from mpiroutines import allgather_vec, allgather_vec_entry


class cardiovascular0Dbase:
    
    def __init__(self, init=True, comm=None):
        self.T_cycl = 0 # duration of one cardiac cycle (gets overridden by derived syspul* classes)
        self.init = init # for output
        self.varmap, self.auxmap = {}, {} # maps for primary and auxiliary variables
        if comm is not None: self.comm = comm # MPI communicator
       
    
    # evaluate model at current nonlinear iteration
    def evaluate(self, x, t, df=None, f=None, dK=None, K=None, c=[], y=[], a=None, fnc=[]):

        if isinstance(x, np.ndarray): x_sq = x
        else: x_sq = allgather_vec(x, self.comm)

        # ODE lhs (time derivative) residual part df
        if df is not None:
            
            for i in range(self.numdof):
                df[i] = self.df__[i](x_sq, c, t, fnc)
            
        # ODE rhs residual part f 
        if f is not None:
            
            for i in range(self.numdof):
                f[i] = self.f__[i](x_sq, c, t, fnc)

        # ODE lhs (time derivative) stiffness part dK (ddf/dx)
        if dK is not None:
            
            for i in range(self.numdof):
                for j in range(self.numdof):
                    dK[i,j] = self.dK__[i][j](x_sq, c, t, fnc)

        # ODE rhs stiffness part K (df/dx)
        if K is not None:
            
            for i in range(self.numdof):
                for j in range(self.numdof):
                    K[i,j] = self.K__[i][j](x_sq, c, t, fnc)

        # auxiliary variable vector a (for post-processing or periodic state check)
        if a is not None:
            
            for i in range(self.numdof):
                a[i] = self.a__[i](x_sq, c, t, fnc)


    # symbolic stiffness matrix contributions ddf_/dx, df_/dx
    def set_stiffness(self):
        
        for i in range(self.numdof):
            for j in range(self.numdof):
        
                self.dK_[i][j] = sp.diff(self.df_[i],self.x_[j])
                self.K_[i][j]  = sp.diff(self.f_[i],self.x_[j])


    # make Lambda functions out of symbolic Sympy expressions
    def lambdify_expressions(self):

        for i in range(self.numdof):
            self.df__[i] = sp.lambdify([self.x_, self.c_, self.t_, self.fnc_], self.df_[i], 'numpy')
            self.f__[i] = sp.lambdify([self.x_, self.c_, self.t_, self.fnc_], self.f_[i], 'numpy')
            self.a__[i] = sp.lambdify([self.x_, self.c_, self.t_, self.fnc_], self.a_[i], 'numpy')            
        
        for i in range(self.numdof):
            for j in range(self.numdof):
                self.dK__[i][j] = sp.lambdify([self.x_, self.c_, self.t_, self.fnc_], self.dK_[i][j], 'numpy')
                self.K__[i][j] = sp.lambdify([self.x_, self.c_, self.t_, self.fnc_], self.K_[i][j], 'numpy')


    # set prescribed variable values
    def set_prescribed_variables(self, x, r, K, val, index_prescribed):

        if isinstance(x, np.ndarray): xs, xe = 0, len(x)
        else: xs, xe = x.getOwnershipRange()

        # modification of rhs entry
        if index_prescribed in range(xs,xe):
            r[index_prescribed] = x[index_prescribed] - val

        # modification of stiffness matrix - all off-columns associated to index_prescribed = 0
        # diagonal entry associated to index_prescribed = 1
        for i in range(self.numdof):
            
            if i==index_prescribed:

                for j in range(self.numdof):
                
                    if j!=index_prescribed:
                        K[i,j] = 0.
                    else:
                        K[i,j] = 1.


    # time step update
    def update(self, var, df, f, var_old, df_old, f_old, aux, aux_old):

        if isinstance(var, np.ndarray): vs, ve = 0, len(var)
        else: vs, ve = var.getOwnershipRange()

        for i in range(vs,ve):
            
            var_old[i] = var[i]
            df_old[i]  = df[i]
            f_old[i]   = f[i]

        # aux vector is always a numpy array
        for i in range(len(aux)):
            
            aux_old[i] = aux[i]
            
            
    # check for cardiac cycle periodicity 
    def cycle_check(self, var, varTc, varTc_old, t, cycle, cyclerr, eps_periodic, check='allvar', inioutpath=None, nm='', induce_pert_after_cycl=-1):
        
        if isinstance(varTc, np.ndarray): vs, ve = 0, len(varTc)
        else: vs, ve = var.getOwnershipRange()

        is_periodic = False
        
        if self.T_cycl > 0. and np.isclose(t, self.T_cycl):
            
            for i in range(vs,ve):
                varTc[i] = var[i]
            
            if check is not None: is_periodic = self.check_periodic(varTc, varTc_old, eps_periodic, check, cyclerr)
            
            # definitely should not be True if we've not yet surpassed the "disease induction" cycle
            if cycle[0] <= induce_pert_after_cycl:
                is_periodic = False
            
            # write "periodic" initial conditions in case we want to restart from this model in another simulation
            if is_periodic and inioutpath is not None:
                self.write_initial(inioutpath, nm, varTc_old, varTc)
            
            for i in range(vs,ve):
                varTc_old[i] = varTc[i]
                
            # update cycle counter
            cycle[0] += 1

        return is_periodic


    # some perturbations/diseases we want to simulate (mr: mitral regurgitation, ms: mitral stenosis, ar: aortic regurgitation, as: aortic stenosis)
    def induce_perturbation(self, perturb_type, perturb_factor):

        if perturb_type=='mr': self.R_vin_l_max *= perturb_factor
        if perturb_type=='ms': self.R_vin_l_min *= perturb_factor
        if perturb_type=='ar': self.R_vout_l_max *= perturb_factor
        if perturb_type=='as': self.R_vout_l_min *= perturb_factor

        # arrays need re-initialization, expressions have to be re-set
        self.setup_arrays(), self.set_compartment_interfaces()
        self.equation_map(), self.set_stiffness(), self.lambdify_expressions()

    
    # set pressure function for 3D FEM model (FEniCS)
    def set_pressure_fem(self, var, ids, pr0D, p0Da):
        
        # set pressure functions
        for i in range(len(ids)):
            pr0D.val = -allgather_vec_entry(var, ids[i], self.comm)
            p0Da[i].interpolate(pr0D.evaluate)


    # midpoint-averaging of state variables (for post-processing)
    def midpoint_avg(self, var, var_old, var_mid, theta):
        
        if isinstance(var, np.ndarray): vs, ve = 0, len(var)
        else: vs, ve = var.getOwnershipRange()

        for i in range(vs,ve):
            var_mid[i] = theta*var[i] + (1.-theta)*var_old[i]


    # set up the dof, coupling quantity, rhs, and stiffness arrays
    def set_solve_arrays(self):

        self.x_, self.a_, self.a__ = [0]*self.numdof, [0]*self.numdof, [0]*self.numdof
        self.c_, self.fnc_ = [], []
        
        self.df_, self.f_, self.df__, self.f__ = [0]*self.numdof, [0]*self.numdof, [0]*self.numdof, [0]*self.numdof
        self.dK_,  self.K_  = [[0]*self.numdof for _ in range(self.numdof)], [[0]*self.numdof for _ in range(self.numdof)]
        self.dK__, self.K__ = [[0]*self.numdof for _ in range(self.numdof)], [[0]*self.numdof for _ in range(self.numdof)]



    # set valve q(p) relationship
    def valvelaw(self, p, popen, Rmin, Rmax, vparams, topen, tclose):

        if vparams[0]=='pwlin_pres': # piecewise linear with resistance depending on pressure difference
            R = sp.Piecewise( (Rmax, p < popen), (Rmin, p >= popen) )
            vl = (popen - p) / R
        elif vparams[0]=='pwlin_time': # piecewise linear with resistance depending on timing
            if topen > tclose: R = sp.Piecewise( (Rmax, sp.And(self.t_ < topen, self.t_ >= tclose)), (Rmin, sp.Or(self.t_ >= topen, self.t_ < tclose)) )
            else:              R = sp.Piecewise( (Rmax, sp.Or(self.t_ < topen, self.t_ >= tclose)), (Rmin, sp.And(self.t_ >= topen, self.t_ < tclose)) )
            vl = (popen - p) / R
        elif vparams[0]=='smooth_pres_resistance': # smooth resistance value
            R = 0.5*(Rmax - Rmin)*(sp.tanh((popen - p)/vparams[-1]) + 1.) + Rmin
            vl = (popen - p) / R            
        elif vparams[0]=='smooth_pres_momentum': # smooth q(p) relationship
            # interpolation by cubic spline in epsilon interval
            p0 = (popen-vparams[-1]/2. - popen)/Rmax
            p1 = (popen+vparams[-1]/2. - popen)/Rmin
            m0 = 1./Rmax
            m1 = 1./Rmin
            s = (p - (popen-vparams[-1]/2.))/vparams[-1]
            # spline ansatz functions
            h00 = 2.*s**3. - 3*s**2. + 1.
            h01 = -2.*s**3. + 3*s**2.
            h10 = s**3. - 2.*s**2. + s
            h11 = s**3. - s**2.
            # spline
            c = h00*p0 + h10*m0*vparams[-1] + h01*p1 + h11*m1*vparams[-1]
            vl = sp.Piecewise( ((popen - p)/Rmax, p < popen-vparams[-1]/2), (-c, sp.And(p >= popen-vparams[-1]/2., p < popen+vparams[-1]/2.)), ((popen - p)/Rmin, p >= popen+vparams[-1]/2.) )
        elif vparams[0]=='pw_pres_regurg':
            vl = sp.Piecewise( (vparams[1]*vparams[2]*sp.sqrt(popen - p), p < popen), ((popen - p) / Rmin, p >= popen) )
        else:
            raise NameError("Unknown valve law %s!" % (vparams[0]))
        
        vlaw = vl
        if popen is not sp.S.Zero:
            res = 1./sp.diff(vl,popen)
        else:
            res = sp.S.One
        
        return vlaw, res


    # set compartment interfaces according to case and coupling quantity (can be volume, flux, or pressure)
    def set_compartment_interfaces(self):
        
        # loop over chambers
        for i, ch in enumerate(['lv','rv','la','ra', 'ao']):
            
            if ch == 'lv': chn = 'v_l'
            if ch == 'rv': chn = 'v_r'
            if ch == 'la': chn = 'at_l'
            if ch == 'ra': chn = 'at_r'
            if ch == 'ao': chn = 'aort_sys'

            if self.chmodels[ch]['type']=='0D_elast' or self.chmodels[ch]['type']=='0D_elast_prescr':
                self.switch_V[i] = 1
                
            elif self.chmodels[ch]['type']=='0D_rigid':
                self.switch_V[i] = 0
            
            elif self.chmodels[ch]['type']=='prescribed':
                if self.cq[i] == 'volume':
                    self.switch_V[i] = 1
                    self.cname.append('V_'+chn)
                elif self.cq[i] == 'flux':
                    self.switch_V[i] = 0
                    self.cname.append('Q_'+chn)
                else:
                    raise NameError("Unknown coupling quantity!")
            
            elif self.chmodels[ch]['type']=='3D_solid':
                if self.cq[i] == 'volume':
                    self.v_ids.append(self.vindex_ch[i]) # variable indices for coupling
                    self.c_ids.append(self.cindex_ch[i]) # coupling quantity indices for coupling
                    self.cname.append('V_'+chn)
                    self.switch_V[i], self.vname[i] = 1, 'p_'+chn
                elif self.cq[i] == 'flux':
                    self.cname.append('Q_'+chn)
                    self.switch_V[i], self.vname[i] = 0, 'p_'+chn
                    self.v_ids.append(self.vindex_ch[i]) # variable indices for coupling
                    self.c_ids.append(self.cindex_ch[i]) # coupling quantity indices for coupling
                elif self.cq[i] == 'pressure':
                    if self.vq[i] == 'volume':
                        self.switch_V[i], self.vname[i] = 1, 'V_'+chn
                    elif self.vq[i] == 'flux':
                        self.switch_V[i], self.vname[i] = 0, 'Q_'+chn
                    else:
                        raise ValueError("Variable quantity has to be volume or flux!")
                    self.cname.append('p_'+chn)
                    self.si[i] = 1 # switch indices of pressure / outflux
                    self.v_ids.append(self.vindex_ch[i]-self.si[i]) # variable indices for coupling
                else:
                    raise NameError("Unknown coupling quantity!")
            
            # 3D fluid currently only working with Cheart!
            elif self.chmodels[ch]['type']=='3D_fluid':
                assert(self.cq[i] == 'pressure')
                self.switch_V[i], self.vname[i] = 0, 'Q_'+chn
                if ch != 'ao': self.si[i] = 1 # switch indices of pressure / outflux
                #self.v_ids.append(self.vindex_ch[i]-self.si[i]) # variable indices for coupling
                # add inflow pressures to coupling name prefixes
                for m in range(self.chmodels[ch]['num_inflows']):
                    self.cname.append('p_'+chn+'_i'+str(m+1)+'')
                # add outflow pressures to coupling name prefixes
                for m in range(self.chmodels[ch]['num_outflows']):
                    self.cname.append('p_'+chn+'_o'+str(m+1)+'')
                # special case where we have a coronary model but an LV with no outflows
                if self.chmodels[ch]['num_inflows']==0 and self.cormodel is not None and ch=='ao':                    
                    self.cname.append('p_v_l_o1')
                
            else:
                raise NameError("Unknown chamber model for chamber %s!" % (ch))


    # set coupling state (populate x and c vectors with Sympy symbols) according to case and coupling quantity (can be volume, flux, or pressure)
    def set_coupling_state(self, ch, chvars, chfncs=[], chvars_2={}):
        
        if ch == 'lv': V_unstressed, i = self.V_v_l_u,  0
        if ch == 'rv': V_unstressed, i = self.V_v_r_u,  1
        if ch == 'la': V_unstressed, i = self.V_at_l_u, 2
        if ch == 'ra': V_unstressed, i = self.V_at_r_u, 3
        if ch == 'ao': V_unstressed, i = self.V_ar_sys_u, 4
   
        # "distributed" p variables
        num_pdist = len(chvars)-1

        # time-varying elastances
        if self.chmodels[ch]['type']=='0D_elast' or self.chmodels[ch]['type']=='0D_elast_prescr':
            chvars['VQ'] = chvars['pi1']/chfncs[0] + V_unstressed # V = p/E(t) + V_u
            self.fnc_.append(chfncs[0])
            
            # all "distributed" p are equal to "main" p of chamber (= pi1)
            for k in range(10): # no more than 10 distributed p's allowed
                if 'pi'+str(k+1)+'' in chvars.keys(): chvars['pi'+str(k+1)+''] = chvars['pi1']
                if 'po'+str(k+1)+'' in chvars.keys(): chvars['po'+str(k+1)+''] = chvars['pi1']

        # rigid
        elif self.chmodels[ch]['type']=='0D_rigid':
            chvars['VQ'] = 0
            
            # all "distributed" p are equal to "main" p of chamber (= pi1)
            for k in range(10): # no more than 10 distributed p's allowed
                if 'pi'+str(k+1)+'' in chvars.keys(): chvars['pi'+str(k+1)+''] = chvars['pi1']
                if 'po'+str(k+1)+'' in chvars.keys(): chvars['po'+str(k+1)+''] = chvars['pi1']

        # 3D solid mechanics model, or 0D prescribed volume/flux/pressure (non-primary variables!)
        elif self.chmodels[ch]['type']=='3D_solid' or self.chmodels[ch]['type']=='prescribed':

            # all "distributed" p are equal to "main" p of chamber (= pi1)
            for k in range(10): # no more than 10 distributed p's allowed
                if 'pi'+str(k+1)+'' in chvars.keys(): chvars['pi'+str(k+1)+''] = chvars['pi1']
                if 'po'+str(k+1)+'' in chvars.keys(): chvars['po'+str(k+1)+''] = chvars['pi1']

            if self.cq[i] == 'volume' or self.cq[i] == 'flux':
                self.c_.append(chvars['VQ']) # V or Q
            if self.cq[i] == 'pressure':
                self.x_[self.vindex_ch[i]-self.si[i]] = chvars['VQ'] # V or Q
                self.c_.append(chvars['pi1'])

        # 3D fluid mechanics model
        elif self.chmodels[ch]['type']=='3D_fluid': # also for 2D FEM models
            
            assert(self.cq[i] == 'pressure' and self.vq[i] == 'flux')

            self.x_[self.vindex_ch[i]-self.si[i]] = chvars['VQ'] # Q of chamber is now variable

            # all "distributed" p that are not coupled are set to first inflow p
            for k in range(self.chmodels[ch]['num_inflows'],10):
                if 'pi'+str(k+1)+'' in chvars.keys(): chvars['pi'+str(k+1)+''] = chvars['pi1']

            # if no inflow is present, set to zero
            if self.chmodels[ch]['num_inflows']==0: chvars['pi1'] = sp.S.Zero

            # now add inflow pressures to coupling array
            for m in range(self.chmodels[ch]['num_inflows']):
                self.c_.append(chvars['pi'+str(m+1)+''])
            
            # all "distributed" p that are not coupled are set to first outflow p
            for k in range(self.chmodels[ch]['num_outflows'],10):
                if 'po'+str(k+1)+'' in chvars.keys(): chvars['po'+str(k+1)+''] = chvars['po1']
            
            # if no outflow is present, set to zero
            if self.chmodels[ch]['num_outflows']==0: chvars['po1'] = sp.S.Zero

            # now add outflow pressures to coupling array
            for m in range(self.chmodels[ch]['num_outflows']):
                self.c_.append(chvars['po'+str(m+1)+''])
                
            # special case where we have a coronary model but an LV with no outflows (need an externally computed LV pressure)
            if self.chmodels[ch]['num_inflows']==0 and self.cormodel is not None and ch=='ao':
                chvars_2['po1'] = sp.Symbol('p_v_l_o1_')
                self.c_.append(chvars_2['po1'])

        else:
            raise NameError("Unknown chamber model for chamber %s!" % (ch))
        

    # evaluate time-dependent state of chamber (for 0D elastance models)
    def evaluate_chamber_state(self, y, t):
        
        chamber_funcs=[]

        ci=0
        for i, ch in enumerate(['lv','rv','la','ra']):

            if self.chmodels[ch]['type']=='0D_elast':
                
                if ch == 'lv': E_max, E_min = self.E_v_max_l,  self.E_v_min_l
                if ch == 'rv': E_max, E_min = self.E_v_max_r,  self.E_v_min_r
                if ch == 'la': E_max, E_min = self.E_at_max_l, self.E_at_min_l
                if ch == 'ra': E_max, E_min = self.E_at_max_r, self.E_at_min_r

                # time-varying elastance model (y should be normalized activation function provided by user)
                E_ch_t = (E_max - E_min) * y[ci] + E_min
                
                chamber_funcs.append(E_ch_t)
                
                ci+=1

            elif self.chmodels[ch]['type']=='0D_elast_prescr':
                
                E_ch_t = y[ci]
                
                chamber_funcs.append(E_ch_t)
                
                ci+=1
                
            else:
                
                pass
            
        return chamber_funcs


    # initialize Lagrange multipliers for monolithic Lagrange-type coupling (FEniCS)
    def initialize_lm(self, var, iniparam):
        
        for i, ch in enumerate(['lv','rv','la','ra']):
            
            if self.chmodels[ch]['type']=='3D_solid':
                
                if ch=='lv':
                    if 'p_v_l_0' in iniparam.keys(): var[i] = iniparam['p_v_l_0']
                if ch=='rv':
                    if 'p_v_r_0' in iniparam.keys(): var[i] = iniparam['p_v_r_0']
                if ch=='la':
                    if 'p_at_l_0' in iniparam.keys(): var[i] = iniparam['p_at_l_0']
                if ch=='ra':
                    if 'p_at_r_0' in iniparam.keys(): var[i] = iniparam['p_at_r_0']
                

    # output routine for 0D models
    def write_output(self, path, t, var, aux, nm=''):

        if isinstance(var, np.ndarray): var_sq = var
        else: var_sq = allgather_vec(var, self.comm)

        # mode: 'wt' generates new file, 'a' appends to existing one
        if self.init: mode = 'wt'
        else: mode = 'a'
        
        self.init = False

        if self.comm.rank == 0:

            for i in range(len(self.varmap)):
                
                filename = path+'/results_'+nm+'_'+list(self.varmap.keys())[i]+'.txt'
                f = open(filename, mode)
                
                f.write('%.16E %.16E\n' % (t,var_sq[list(self.varmap.values())[i]]))
                
                f.close()

            for i in range(len(self.auxmap)):
                
                filename = path+'/results_'+nm+'_'+list(self.auxmap.keys())[i]+'.txt'
                f = open(filename, mode)
                
                f.write('%.16E %.16E\n' % (t,aux[list(self.auxmap.values())[i]]))
                
                f.close()


    # write restart routine for 0D models
    def write_restart(self, path, nm, N, var):
        
        if isinstance(var, np.ndarray): var_sq = var
        else: var_sq = allgather_vec(var, self.comm)

        if self.comm.rank == 0:
        
            filename = path+'/checkpoint_'+nm+'_'+str(N)+'.txt'
            f = open(filename, 'wt')
            
            for i in range(len(var_sq)):
                
                f.write('%.16E\n' % (var_sq[i]))
                
            f.close()


    # read restart routine for 0D models
    def read_restart(self, path, nm, rstep, var):

        restart_data = np.loadtxt(path+'/checkpoint_'+nm+'_'+str(rstep)+'.txt')

        var[:] = restart_data[:]


    # to write initial conditions (i.e. after a model has reached periodicity, so we may want to export these if we want to use
    # them in a new simulation starting from a homeostatic state)
    def write_initial(self, path, nm, varTc_old, varTc):
        
        if isinstance(varTc_old, np.ndarray): varTc_old_sq, varTc_sq = varTc_old, varTc
        else: varTc_old_sq, varTc_sq = allgather_vec(varTc_old, self.comm), allgather_vec(varTc, self.comm)
        
        if self.comm.rank == 0:
        
            filename1 = path+'/initial_data_'+nm+'_Tstart.txt' # conditions at beginning of cycle
            f1 = open(filename1, 'wt')
            filename2 = path+'/initial_data_'+nm+'_Tend.txt' # conditions at end of cycle
            f2 = open(filename2, 'wt')
            
            for i in range(len(self.varmap)):
                
                f1.write('%s %.16E\n' % (list(self.varmap.keys())[i]+'_0',varTc_old_sq[list(self.varmap.values())[i]]))
                f2.write('%s %.16E\n' % (list(self.varmap.keys())[i]+'_0',varTc_sq[list(self.varmap.values())[i]]))
                
            f1.close()
            f2.close()


    # if we want to set the initial conditions from a txt file
    def set_initial_from_file(self, initialdata):
    
        pini0D = {}
        with open(initialdata) as fh:
            for line in fh:
                (key, val) = line.split()
                pini0D[key] = float(val)
                
        return pini0D
