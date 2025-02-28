import numpy as np
import mne
from copy import deepcopy
from .base import BaseSolver, InverseOperator
from scipy.sparse.csgraph import laplacian
from ..util import best_index_residual, thresholding, calc_residual_variance, find_corner

class SolverSMP(BaseSolver):
    ''' Class for the Smooth Matching Pursuit (SMP) inverse solution. Developed
        by Lukas Hecker as a smooth extension of the orthogonal matching pursuit
        algorithm [1,2], 19.10.2022.
    
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    [1] Tropp, J. A., & Gilbert, A. C. (2007). Signal recovery from random
    measurements via orthogonal matching pursuit. IEEE Transactions on
    information theory, 53(12), 4655-4666.
    [2] Duarte, M. F., & Eldar, Y. C. (2011). Structured compressed sensing:
    From theory to applications. IEEE Transactions on signal processing, 59(9),
    4053-4085.
     

    '''
    def __init__(self, name="Smooth Matching Pursuit", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, *args, alpha='auto', **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        self.inverse_operators = []
        
        adjacency = mne.spatial_src_adjacency(self.forward['src'], verbose=0).toarray()
        laplace_operator = laplacian(adjacency)
        self.laplace_operator = laplace_operator
        leadfield_smooth = self.leadfield @ abs(laplace_operator)

        leadfield_smooth -= leadfield_smooth.mean(axis=0)
        self.leadfield -= self.leadfield.mean(axis=0)
        self.leadfield_smooth = leadfield_smooth
        self.leadfield_smooth_normed = self.leadfield_smooth / np.linalg.norm(self.leadfield_smooth, axis=0)
        self.leadfield_normed = self.leadfield / np.linalg.norm(self.leadfield, axis=0)
        
        return self

    def apply_inverse_operator(self, mne_obj, K=1, include_singletons=True) -> mne.SourceEstimate:
        ''' Apply the inverse operator.
        Parameters
        ----------
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.

        Return
        ------
        stc : mne.SourceEstimate
            The mne Source Estimate object
        '''
        data = self.unpack_data_obj(mne_obj)

        source_mat = np.stack([self.calc_smp_solution(y, include_singletons=include_singletons) for y in data.T], axis=1)
        stc = self.source_to_object(source_mat)
        return stc
    

    def calc_smp_solution(self, y, include_singletons=True):
        """ Calculates the Orthogonal Matching Pursuit (OMP) inverse solution.
        
        Parameters
        ----------
        y : numpy.ndarray
            The data matrix (channels,).
        include_singletons : bool
            If True -> Include not only smooth patches but also single dipoles.
        

        Return
        ------
        x_hat : numpy.ndarray
            The inverse solution (dipoles,)
        """
        
        n_chans = len(y)
        _, n_dipoles = self.leadfield.shape
        
        y -= y.mean()
        x_hat = np.zeros(n_dipoles)
        omega = np.array([])
        r = deepcopy(y)
        y_hat = self.leadfield@x_hat
        y_hat -= y_hat.mean(axis=0)
        residuals = np.array([np.linalg.norm(y - y_hat), ])
        unexplained_variance = np.array([calc_residual_variance(y_hat, y),])
        # source_norms = np.array([0,])
        x_hats = [deepcopy(x_hat), ]

        for _ in range(n_chans):
            b_smooth = self.leadfield_smooth_normed.T @ r
            b_sparse = self.leadfield_normed.T @ r

            if include_singletons & (abs(b_sparse).max() > abs(b_smooth).max()):  # if sparse is better
                b_sparse_thresh = thresholding(b_sparse, 1)
                new_patch = np.where(b_sparse_thresh != 0)[0]
                
            else: # else if patch is better
                b_smooth_thresh = thresholding(b_smooth, 1)
                new_patch = np.where(self.laplace_operator[b_smooth_thresh!=0][0]!=0)[0]


            omega = np.append(omega, new_patch)
            omega = omega.astype(int)
            x_hat[omega] = np.linalg.pinv(self.leadfield[:, omega]) @ y
            y_hat = self.leadfield@x_hat
            y_hat -= y_hat.mean(axis=0)
            r = y - y_hat

            residuals = np.append(residuals, np.linalg.norm(r))
            unexplained_variance = np.append(unexplained_variance, calc_residual_variance(y_hat, y))
            # source_norms = np.append(source_norms, np.sum(x_hat**2))
            x_hats.append(deepcopy(x_hat))
            if residuals[-1] > residuals[-2]:
                break

        x_hat = best_index_residual(unexplained_variance, x_hats, plot=False)
        
        # x_hat = x_hats[corner_idx]
        return x_hat

class SolverSSMP(BaseSolver):
    ''' Class for the Smooth Simultaneous Matching Pursuit (SSMP) inverse
        solution. Developed by Lukas Hecker as a smooth extension of the
        orthogonal matching pursuit algorithm [1,2], 19.10.2022.
    
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    [1] Duarte, M. F., & Eldar, Y. C. (2011). Structured compressed sensing:
    From theory to applications. IEEE Transactions on signal processing, 59(9),
    4053-4085.

    [2] Donoho, D. L. (2006). Compressed sensing. IEEE Transactions on
    information theory, 52(4), 1289-1306.

    '''
    def __init__(self, name="Smooth Simultaneous Matching Pursuit", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, *args, alpha='auto', **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        self.inverse_operators = []
        
        # Prepare spatial laplacian operator
        adjacency = mne.spatial_src_adjacency(self.forward['src'], verbose=0).toarray()
        laplace_operator = laplacian(adjacency)
        self.laplace_operator = laplace_operator
        
        # Create a leadfield of  smooth sources
        leadfield_smooth = self.leadfield @ abs(laplace_operator)

        leadfield_smooth -= leadfield_smooth.mean(axis=0)
        self.leadfield -= self.leadfield.mean(axis=0)
        self.leadfield_smooth = leadfield_smooth
        self.leadfield_smooth_normed = self.leadfield_smooth / np.linalg.norm(self.leadfield_smooth, axis=0)
        self.leadfield_normed = self.leadfield / np.linalg.norm(self.leadfield, axis=0)
        
        return self

    def apply_inverse_operator(self, mne_obj, include_singletons=True) -> mne.SourceEstimate:
        ''' Apply the inverse operator.
        Parameters
        ----------
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.
        include_singletons : bool
            If True -> Include not only smooth patches but also single dipoles.

        Return
        ------
        stc : mne.SourceEstimate
            The mne Source Estimate object
        '''
        data = self.unpack_data_obj(mne_obj)
        source_mat = self.calc_ssmp_solution(data, include_singletons=include_singletons)
        stc = self.source_to_object(source_mat)
        return stc
    

    def calc_ssmp_solution(self, y, include_singletons=True):
        """ Calculates the Smooth Simultaneous Orthogonal Matching Pursuit (SSMP) inverse solution.
        
        Parameters
        ----------
        y : numpy.ndarray
            The data matrix (channels,).
        include_singletons : bool
            If True -> Include not only smooth patches but also single dipoles.

        Return
        ------
        x_hat : numpy.ndarray
            The inverse solution (dipoles,)
        """

        n_chans, n_time = y.shape
        max_iter = int(n_chans/2)
        _, n_dipoles = self.leadfield.shape
        
        y -= y.mean(axis=0)

        x_hat = np.zeros((n_dipoles, n_time))
        x_hats = [deepcopy(x_hat)]
        residuals = np.array([np.linalg.norm(y - self.leadfield@x_hat), ])
        source_norms = np.array([0,])

        R = deepcopy(y)
        omega = np.array([])

        y_hat = self.leadfield @ x_hat
        y_hat -= y_hat.mean(axis=0)
        residuals = np.array([np.linalg.norm(y - y_hat), ])
        x_hats = [deepcopy(x_hat), ]
        q = 1
        
        for _ in range(max_iter):
            b_n_smooth = np.linalg.norm(self.leadfield_smooth_normed.T @ R, axis=1, ord=q)
            b_n_sparse = np.linalg.norm(self.leadfield_normed.T @ R, axis=1, ord=q)

            if include_singletons & (abs(b_n_sparse).max() > abs(b_n_smooth).max()):  # if sparse is better
                b_n_sparse_thresh = thresholding(b_n_sparse, 1)
                new_patch = np.where(b_n_sparse_thresh != 0)[0]
                
            else: # else if patch is better
                b_n_smooth_thresh = thresholding(b_n_smooth, 1)
                new_patch = np.where(self.laplace_operator[b_n_smooth_thresh!=0][0]!=0)[0]


            omega = np.append(omega, new_patch)
            omega = omega.astype(int)
            x_hat[omega] = np.linalg.pinv(self.leadfield[:, omega]) @ y
            
            y_hat = self.leadfield@x_hat
            y_hat -= y_hat.mean(axis=0)
            R = y - y_hat

            residuals = np.append(residuals, np.linalg.norm(y - y_hat))
            source_norms = np.append(source_norms, np.sum(x_hat**2))
            x_hats.append(deepcopy(x_hat))

            if residuals[-1] > residuals[-2]:
                break



        # Model selection (Regularisation)
        x_hat = best_index_residual(residuals, x_hats, plot=False)

        return x_hat
    
class SolverSubSMP(BaseSolver):
    ''' Class for the Subspace Smooth Matching Pursuit (SubSMP) inverse solution. Developed
        by Lukas Hecker as a smooth extension of the orthogonal matching pursuit
        algorithm [1], 19.10.2022.
    
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    [1] Duarte, M. F., & Eldar, Y. C. (2011). Structured compressed sensing:
    From theory to applications. IEEE Transactions on signal processing, 59(9),
    4053-4085.

    '''
    def __init__(self, name="Subspace Smooth Matching Pursuit", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, *args, alpha='auto', **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        self.inverse_operators = []
        
        adjacency = mne.spatial_src_adjacency(self.forward['src'], verbose=0).toarray()
        
        # Create spatial laplacian operator
        laplace_operator = laplacian(adjacency)
        self.laplace_operator = laplace_operator
        
        # Create leadfield of smooth sources
        leadfield_smooth = self.leadfield @ abs(laplace_operator)
        leadfield_smooth -= leadfield_smooth.mean(axis=0)
        self.leadfield -= self.leadfield.mean(axis=0)
        self.leadfield_smooth = leadfield_smooth
        self.leadfield_smooth_normed = self.leadfield_smooth / np.linalg.norm(self.leadfield_smooth, axis=0)
        self.leadfield_normed = self.leadfield / np.linalg.norm(self.leadfield, axis=0)
        
        return self

    def apply_inverse_operator(self, mne_obj,include_singletons=True) -> mne.SourceEstimate:
        ''' Apply the inverse operator.
        Parameters
        ----------
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.

        Return
        ------
        stc : mne.SourceEstimate
            The mne Source Estimate object
        '''
        data = self.unpack_data_obj(mne_obj)
        source_mat = self.calc_subsmp_solution(data, include_singletons=include_singletons)
        stc = self.source_to_object(source_mat)
        return stc

    def calc_subsmp_solution(self, y, include_singletons=True, var_thresh=1):
        ''' Calculate the Subspace Smooth Matching Pursuit (SubSMP) solution.

        Parameters
        ----------
        y : numpy.ndarray
            The M/EEG data Matrix
        include_singletons : bool
            If True -> include single dipoles as candidates, 
            else include only smooth patches
        var_thresh : float
            Threshold how much variance will be explained in the data

        '''
        _, n_time = y.shape
        n_dipoles = self.leadfield.shape[1]
        
        y -= y.mean(axis=0)
        u, s, _ = np.linalg.svd(y)
        
        # Select components
        idx = find_corner(np.arange(len(s)), s) + 1
        print(f"Selected {idx} components")
        # Kaiser-Guttmann:
        # thr = 1  # 1=kaiser-guttmann, else: np.e**(-16)
        # idx = np.where((s*len(s) / s.sum()) > thr)[0][-1] + 1

        # Find matching sources to each of the selected eigenvectors (components)
        topos = u[:, :idx]
        x_hats = []
        for topo in topos.T:
            x_hats.append( self.calc_smp_solution(topo, include_singletons=include_singletons, var_thresh=var_thresh) )
        
        # Extract all active dipoles and patches:
        omegas = np.unique(np.where((np.stack(x_hats, axis=0)**2).sum(axis=0) != 0)[0])
        
        # Calculate minimum-norm solution at active dipoles and patches
        x_hat = np.zeros((n_dipoles, n_time))

        ## Use different y without unnecessary components here:
        x_hat[omegas, :] = np.linalg.pinv(self.leadfield[:, omegas]) @ y
        
        # import matplotlib.pyplot as plt
        # plt.figure()
        # plt.plot(np.arange(len(s)), s, '*k')
        # plt.plot(np.arange(len(s))[idx], s[idx], 'or')
        

        return x_hat

    def calc_smp_solution(self, y, include_singletons=True, var_thresh=1):
        """ Calculates the Orthogonal Matching Pursuit (OMP) inverse solution.
        
        Parameters
        ----------
        y : numpy.ndarray
            The data matrix (channels,).
        include_singletons : bool
            If True -> Include not only smooth patches but also single dipoles.
        var_thresh : float
            Threshold how much variance will be explained in the data
            
        Return
        ------
        x_hat : numpy.ndarray
            The inverse solution (dipoles,)
        """
        n_chans = len(y)
        _, n_dipoles = self.leadfield.shape
        
        y -= y.mean()
        x_hat = np.zeros(n_dipoles)
        omega = np.array([])
        r = deepcopy(y)
        y_hat = self.leadfield@x_hat
        y_hat -= y_hat.mean(axis=0)
        residuals = np.array([np.linalg.norm(y - y_hat), ])
        unexplained_variance = np.array([calc_residual_variance(y_hat, y),])
        # source_norms = np.array([0,])
        x_hats = [deepcopy(x_hat), ]

        for _ in range(n_chans):
            b_smooth = self.leadfield_smooth_normed.T @ r
            b_sparse = self.leadfield_normed.T @ r

            if include_singletons & (abs(b_sparse).max() > abs(b_smooth).max()):  # if sparse is better
                b_sparse_thresh = thresholding(b_sparse, 1)
                new_patch = np.where(b_sparse_thresh != 0)[0]
                
            else: # else if patch is better
                b_smooth_thresh = thresholding(b_smooth, 1)
                new_patch = np.where(self.laplace_operator[b_smooth_thresh!=0][0]!=0)[0]


            omega = np.append(omega, new_patch)
            omega = omega.astype(int)
            x_hat[omega] = np.linalg.pinv(self.leadfield[:, omega]) @ y
            y_hat = self.leadfield@x_hat
            y_hat -= y_hat.mean(axis=0)
            r = y - y_hat

            residuals = np.append(residuals, np.linalg.norm(r))
            unexplained_variance = np.append(unexplained_variance, calc_residual_variance(y_hat, y))
            # source_norms = np.append(source_norms, np.sum(x_hat**2))
            x_hats.append(deepcopy(x_hat))
            if residuals[-1] > residuals[-2]:
                break

        if unexplained_variance[1] > var_thresh:
            x_hat = best_index_residual(unexplained_variance, x_hats, plot=False)
        else:
            x_hat = x_hats[1]
        
        # x_hat = x_hats[corner_idx]
        return x_hat
    
class SolverISubSMP(BaseSolver):
    ''' Class for the Subspace Smooth Matching Pursuit (SubSMP) inverse solution. Developed
        by Lukas Hecker as a smooth extension of the orthogonal matching pursuit
        algorithm [1], 19.10.2022.
    
    
    Attributes
    ----------
    forward : mne.Forward
        The mne-python Forward model instance.
    
    References
    ----------
    [1] Duarte, M. F., & Eldar, Y. C. (2011). Structured compressed sensing:
    From theory to applications. IEEE Transactions on signal processing, 59(9),
    4053-4085.

    '''
    def __init__(self, name="Iterative Subspace Smooth Matching Pursuit", **kwargs):
        self.name = name
        return super().__init__(**kwargs)

    def make_inverse_operator(self, forward, *args, alpha='auto', verbose=0, **kwargs):
        ''' Calculate inverse operator.

        Parameters
        ----------
        forward : mne.Forward
            The mne-python Forward model instance.
        alpha : float
            The regularization parameter.
        
        Return
        ------
        self : object returns itself for convenience
        '''
        super().make_inverse_operator(forward, *args, alpha=alpha, **kwargs)
        self.inverse_operators = []
        
        adjacency = mne.spatial_src_adjacency(self.forward['src'], verbose=0).toarray()
        laplace_operator = laplacian(adjacency)
        self.laplace_operator = laplace_operator
        leadfield_smooth = self.leadfield @ abs(laplace_operator)

        leadfield_smooth -= leadfield_smooth.mean(axis=0)
        self.leadfield -= self.leadfield.mean(axis=0)
        self.leadfield_smooth = leadfield_smooth
        self.leadfield_smooth_normed = self.leadfield_smooth / np.linalg.norm(self.leadfield_smooth, axis=0)
        self.leadfield_normed = self.leadfield / np.linalg.norm(self.leadfield, axis=0)
        
        return self

    def apply_inverse_operator(self, mne_obj, include_singletons=True) -> mne.SourceEstimate:
        ''' Apply the inverse operator.
        Parameters
        ----------
        mne_obj : [mne.Evoked, mne.Epochs, mne.io.Raw]
            The MNE data object.

        Return
        ------
        stc : mne.SourceEstimate
            The mne Source Estimate object
        '''
        data = self.unpack_data_obj(mne_obj)
        source_mat = self.calc_subsmp_solution(data, include_singletons=include_singletons)
        stc = self.source_to_object(source_mat)
        return stc

    def calc_subsmp_solution(self, y, include_singletons=True, var_thresh=0.1):
        ''' Calculate the Subspace Smooth Matching Pursuit (SubSMP) solution.
        '''
        _, n_time = y.shape
        n_dipoles = self.leadfield.shape[1]
        
        y -= y.mean(axis=0)


        # Find matching sources to each of the selected eigenvectors (components)
 
        x_hat = self.calc_isubsmp_solution(y, include_singletons=include_singletons, var_thresh=var_thresh)

        return x_hat

    def calc_isubsmp_solution(self, y, include_singletons=True, var_thresh=1):
        """ Calculates the Orthogonal Matching Pursuit (OMP) inverse solution.
        
        Parameters
        ----------
        y : numpy.ndarray
            The data matrix (channels, n_time).
        include_singletons : bool
            If True -> Include not only smooth patches but also single dipoles.

        Return
        ------
        x_hat : numpy.ndarray
            The inverse solution (dipoles,)
        """
        n_chans, n_time = y.shape
        _, n_dipoles = self.leadfield.shape
        
        y -= y.mean(axis=0)

        # print(y.shape)
        x_hat = np.zeros((n_dipoles, n_time))
        omega = np.array([])
        r = deepcopy(y)
        r_component = np.linalg.svd(r, full_matrices=False)[0][:, 0]
        y_hat = self.leadfield@x_hat
        y_hat -= y_hat.mean(axis=0)
        residuals = np.array([np.linalg.norm(y - y_hat), ])
        unexplained_variance = np.array([calc_residual_variance(y_hat, y),])
        # source_norms = np.array([0,])
        x_hats = [deepcopy(x_hat), ]

        for _ in range(n_chans):
            b_smooth = self.leadfield_smooth_normed.T @ r_component
            b_sparse = self.leadfield_normed.T @ r_component

            if include_singletons & (abs(b_sparse).max() > abs(b_smooth).max()):  # if sparse is better
                b_sparse_thresh = thresholding(b_sparse, 1)
                new_patch = np.where(b_sparse_thresh != 0)[0]
                
            else: # else if patch is better
                b_smooth_thresh = thresholding(b_smooth, 1)
                new_patch = np.where(self.laplace_operator[b_smooth_thresh!=0][0]!=0)[0]


            omega = np.append(omega, new_patch)
            omega = omega.astype(int)
            x_hat[omega] = np.linalg.pinv(self.leadfield[:, omega]) @ y
            y_hat = self.leadfield@x_hat
            y_hat -= y_hat.mean(axis=0)
            r = y - y_hat
            r_component = np.linalg.svd(r, full_matrices=False)[0][:, 0]

            residuals = np.append(residuals, np.linalg.norm(r))
            unexplained_variance = np.append(unexplained_variance, calc_residual_variance(y_hat, y))
            # source_norms = np.append(source_norms, np.sum(x_hat**2))
            x_hats.append(deepcopy(x_hat))
            if residuals[-1] > residuals[-2] or unexplained_variance[-1]<var_thresh:
                break



        
        # Remove where residual is below threshold:
        # keep_idc = np.where(unexplained_variance>=residual_thresh*100)[0]
        # if len(keep_idc) == 1:
        #     keep_idc = np.append(keep_idc, 1)
        
        # print(keep_idc)
        # unexplained_variance = unexplained_variance[keep_idc]
        # x_hats = [x_hats[idx] for idx in keep_idc]
        # if unexplained_variance[-1] > var_thresh:
        #     x_hat = best_index_residual(unexplained_variance, x_hats, plot=False)
        # else:
        #     x_hat = x_hats[-1]
        
        # x_hat = x_hats[corner_idx]
        return x_hat